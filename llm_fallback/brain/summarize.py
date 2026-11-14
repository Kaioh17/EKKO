"""Plain-text-in, JSON-out calls for memory: memory/spin_cycle.py's daily
archive entry and memory/today_summary.py's running summary of today.

attempt_with_failover() is tied to the command/answer contract, so this
walks the same provider order (failover_chain) itself and reuses the pieces
that matter: provider.call(), ProviderBlocked (the spend cap) and log_call(),
so a summary's tokens land in llm_fallback/logs/<provider>/ and count toward
the same usage totals as every other call.
"""

import json
import time

from llm_fallback.brain.failover import failover_chain
from llm_fallback.brain.core import default_provider
from llm_fallback.brain.logging import log_call
from llm_fallback.brain.outcome import FallbackOutcome
from llm_fallback.brain.parsing import strip_markdown_fence
from llm_fallback.brain.providers import get_provider, load_providers

_SYSTEM = (
    "You compress one day of a voice assistant's conversation log into a memory entry. "
    "Reply with ONLY a JSON object, no prose, no markdown fence: "
    '{"key_summary": "<at most 10 words naming the day\'s main topics>", '
    '"summary": "<2-3 sentences>", "topics": ["<short topic>", ...], '
    '"details": "<the facts, decisions, preferences, names and open questions worth '
    'recalling later, under 200 words>"}. Use only what the log says.'
)

_TODAY_SYSTEM = (
    "You keep a running summary of today's conversation between a user and their "
    "assistant (voice and typed chat). You get the current summary (may be empty) and "
    "older exchanges that just scrolled out of the assistant's live transcript. Fold "
    "them into an updated summary. Reply with ONLY a JSON object, no prose, no markdown "
    'fence: {"summary": "<under 250 words: what was asked, answered, decided or left '
    'open today, keeping names, numbers and any numbered items the user may refer back '
    'to>", "topics": ["<short topic>", ...]}. Use only what you are given.'
)


def ask_json(
    prompt: str, system: str, label: str, required: str, start: str | None = None, timeout: float | None = None
) -> tuple[dict, dict]:
    """First provider in the failover chain (from `start`, default: the
    configured one) that returns a JSON object with a non-empty `required`
    field. Raises RuntimeError if none does. Also used by the daily briefing."""
    load_providers()
    errors: list[str] = []
    for name in failover_chain(start or default_provider()):
        impl = get_provider(name)
        began = time.perf_counter()
        try:
            raw = impl.call(prompt, system, model=impl.default_model, **({"timeout": timeout} if timeout else {}))
            latency = raw.latency_seconds if raw.latency_seconds is not None else round(time.perf_counter() - began, 3)
            parsed = json.loads(strip_markdown_fence(raw.text))
            if not isinstance(parsed, dict) or not str(parsed.get(required, "")).strip():
                raise ValueError(f"reply missing {required}")
        except Exception as exc:  # noqa: BLE001 -- provider/transport/parse failure: next provider
            errors.append(f"{name}: {exc}")
            continue
        log_call(
            FallbackOutcome(
                bundle=None, answer=None, urls=[], raw_intent=None, raw_slots={}, reason="spin_cycle",
                provider=name, model=raw.model, usage=raw.usage, latency_seconds=latency, error=None, source="spin",
            ),
            label,
        )
        return parsed, {"provider": name, "model": raw.model, "latency_s": latency, "usage": raw.usage}
    raise RuntimeError("; ".join(errors) or "no provider available")


def _topics(parsed: dict) -> list[str]:
    topics = parsed.get("topics")
    return [str(t) for t in topics] if isinstance(topics, list) else []


def summarize_day(date: str, block: str) -> tuple[dict, dict]:
    """(entry, meta). Raises RuntimeError if every provider fails or the
    reply isn't the expected JSON -- spin_cycle leaves the day in place and
    retries next tick."""
    parsed, meta = ask_json(f"Conversation log for {date}:\n\n{block}", _SYSTEM, f"[spin_cycle {date}]", "key_summary")
    entry = {
        "summary": str(parsed.get("summary", "")).strip(),
        "topics": _topics(parsed),
        "details": str(parsed.get("details", "")).strip(),
        "key_summary": " ".join(str(parsed["key_summary"]).split()),
    }
    return entry, meta


def summarize_today(previous: str, block: str) -> tuple[dict, dict]:
    """({summary, topics}, meta) folding `block` into `previous`."""
    prompt = f"Current summary:\n{previous or '(empty)'}\n\nExchanges to fold in:\n\n{block}"
    parsed, meta = ask_json(prompt, _TODAY_SYSTEM, "[today_summary]", "summary")
    return {"summary": str(parsed["summary"]).strip(), "topics": _topics(parsed)}, meta
