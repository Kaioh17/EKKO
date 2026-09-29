"""Automatic provider failover, in the order set in the UI (settings DB
section "llm", field failover_order; default: llm_fallback/catalog.py order).

`attempt_with_failover()` wraps `attempt_fallback()` and, when a provider
fails at the transport level (a 503, a timeout, unreachable, missing key,
DeepSeek's spend cap), quietly retries the same transcript on the next
provider in `FAILOVER_ORDER`. The caller gets back one ordinary
FallbackOutcome, exactly as if the provider that finally answered had been
the one it asked for.

What counts as a failure: `outcome.error` is set, or the provider
deliberately refused before sending anything (`extras["blocked"]`, the
DeepSeek spend cap). An outcome with no error but no answer and no command
is a legitimate "noise / didn't understand" and is NOT retried elsewhere.

Every hop is appended to logs/failover.jsonl (and printed), on top of the
per-provider logs/<provider>/fallback.jsonl that each attempt already
writes through log_call().

Cooldown: a provider that just failed is skipped for
`llm.failover_cooldown_s` seconds (default 120), so during a Gemini
outage each command doesn't first burn Gemini's full timeout. It is retried
automatically once the cooldown lapses. In-process state only.

Order: `llm.failover_order`, validated on save (no duplicates, known
names only). A provider left out of the list is never a failover target.

Disable with `llm.failover_enabled`; `--provider ollama` etc. starts the chain
at that provider and only falls through to the ones after it in the order.
"""

import dataclasses
import datetime
import json
import os
import sys
import time
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from llm_fallback.brain.core import attempt_fallback, default_provider  # noqa: E402
from llm_fallback.brain.logging import LOGS_ROOT  # noqa: E402
from llm_fallback.brain.outcome import FallbackOutcome  # noqa: E402
from llm_fallback.brain.prompt import base_system_instruction  # noqa: E402
from llm_fallback.brain.providers import available_providers  # noqa: E402
from llm_fallback.catalog import PROVIDERS  # noqa: E402

FAILOVER_LOG = LOGS_ROOT / "failover.jsonl"

# provider -> time.monotonic() before which it is skipped.
_cooldown_until: dict[str, float] = {}
_warned: set[str] = set()


def _llm():
    from backend.config import get  # lazy: backend.config -> schemas -> listener.defaults

    return get("llm")


def failover_enabled() -> bool:
    return _llm().failover_enabled


def _cooldown_seconds() -> float:
    return _llm().failover_cooldown_s


def failover_order() -> tuple[str, ...]:
    """The order from the UI's settings DB, re-read on every call. The
    schema already rejects duplicates and unknown names; a provider that
    failed to register (missing optional package) is dropped with a
    warning, since it would otherwise silently shorten the chain.
    """
    registered = set(available_providers())
    order: list[str] = []
    for name in _llm().failover_order:
        if name not in registered:
            if name not in _warned:
                _warned.add(name)
                print(f"  [llm_fallback] failover_order: skipping unregistered provider {name!r} (known: {sorted(registered)})")
            continue
        order.append(name)
    return tuple(order) or PROVIDERS


def failover_chain(start: str) -> tuple[str, ...]:
    """`start` and every provider after it in the configured order. A
    provider outside the order just runs alone.
    """
    order = failover_order()
    if start in order:
        return order[order.index(start):]
    return (start,)


def _failure_reason(outcome: FallbackOutcome) -> str | None:
    if outcome.error:
        return outcome.error
    if outcome.extras.get("blocked"):
        return outcome.answer or "blocked"
    return None


def _log_handoff(entry: dict) -> None:
    entry = {"timestamp": datetime.datetime.now().isoformat(), **entry}
    try:
        os.makedirs(FAILOVER_LOG.parent, exist_ok=True)
        with open(FAILOVER_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # logging a handoff must never break the handoff itself


def attempt_with_failover(
    transcript: str,
    provider: str | None = None,
    system_instruction_override: str | None = None,
    domain: str | None = None,
    failover: bool | None = None,
    **kwargs,
) -> FallbackOutcome:
    """Same signature and return type as attempt_fallback(); `provider` is
    where the chain starts. Never raises.
    """
    provider = provider or default_provider()
    if failover is None:
        failover = failover_enabled()
    chain = failover_chain(provider) if failover else (provider,)
    registered = set(available_providers())
    chain = tuple(p for p in chain if p in registered) or (provider,)

    now = time.monotonic()
    ready = tuple(p for p in chain if _cooldown_until.get(p, 0.0) <= now)
    skipped = [p for p in chain if p not in ready]
    # Everything cooling down is no reason to give up: try the whole chain.
    attempts = ready or chain
    if not ready:
        skipped = []

    for p in skipped:
        _log_handoff({"event": "skipped", "provider": p, "reason": "cooldown"})

    failures: list[tuple[str, str]] = []
    outcome: FallbackOutcome | None = None
    for i, p in enumerate(attempts):
        hop_kwargs = dict(kwargs)
        hop_override = system_instruction_override
        if p != provider:
            # `model` names one provider's model, and a domain override
            # embeds the requesting provider's delta: drop the first,
            # rebuild the second for this provider.
            hop_kwargs.pop("model", None)
            if domain:
                from domains.loader import compose_system_instruction

                hop_override = compose_system_instruction(domain, base_system_instruction(p))

        outcome = attempt_fallback(
            transcript, provider=p, system_instruction_override=hop_override, domain=domain, **hop_kwargs
        )
        reason = _failure_reason(outcome)
        if reason is None:
            break

        failures.append((p, reason))
        if not outcome.extras.get("blocked"):
            _cooldown_until[p] = time.monotonic() + _cooldown_seconds()
        nxt = attempts[i + 1] if i + 1 < len(attempts) else None
        _log_handoff(
            {
                "event": "handoff" if nxt else "exhausted",
                "from": p,
                "to": nxt,
                "reason": reason,
                "latency_seconds": outcome.latency_seconds,
                "model": outcome.model,
                "cooldown_s": None if outcome.extras.get("blocked") else _cooldown_seconds(),
            }
        )
        if nxt:
            print(f"  [llm_fallback] {p} failed ({reason}) -> handing off to {nxt}")
    else:
        # Loop ended without a break: every provider failed. Surface the
        # whole story rather than only the last provider's error.
        summary = "; ".join(f"{p}: {r}" for p, r in failures)
        outcome = dataclasses.replace(
            outcome, answer=None, extras={**outcome.extras, "blocked": 0}, error=f"all providers failed - {summary}"
        )
        return outcome

    if failures:
        outcome = dataclasses.replace(
            outcome, extras={**outcome.extras, "failover_from": [p for p, _ in failures]}
        )
        _log_handoff({"event": "recovered", "answered_by": outcome.provider, "after": [p for p, _ in failures]})
    return outcome
