"""One plain-text-in, JSON-out call for memory/spin_cycle.py's daily summary.

attempt_with_failover() is tied to the command/answer contract, so this
walks the same provider order (failover_chain) itself and reuses the pieces
that matter: provider.call(), ProviderBlocked (the spend cap) and log_call(),
so a summary's tokens land in llm_fallback/logs/<provider>/ and count toward
the same usage totals as every other call.
"""

import time

from llm_fallback.brain.failover import failover_chain
from llm_fallback.brain.core import default_provider
from llm_fallback.brain.logging import log_call
from llm_fallback.brain.outcome import FallbackOutcome
from llm_fallback.brain.parsing import strip_markdown_fence
from llm_fallback.brain.providers import get_provider, load_providers

import json

_SYSTEM = (
    "You compress one day of a voice assistant's conversation log into a memory entry. "
    "Reply with ONLY a JSON object, no prose, no markdown fence: "
    '{"key_summary": "<at most 10 words naming the day\'s main topics>", '
    '"summary": "<2-3 sentences>", "topics": ["<short topic>", ...], '
    '"details": "<the facts, decisions, preferences, names and open questions worth '
    'recalling later, under 200 words>"}. Use only what the log says.'
)


def summarize_day(date: str, block: str) -> tuple[dict, dict]:
    """(entry, meta). Raises RuntimeError if every provider fails or the
    reply isn't the expected JSON -- spin_cycle leaves the day in place and
    retries next tick."""
    load_providers()
    prompt = f"Conversation log for {date}:\n\n{block}"
    errors: list[str] = []
    for name in failover_chain(default_provider()):
        impl = get_provider(name)
        start = time.perf_counter()
        try:
            raw = impl.call(prompt, _SYSTEM, model=impl.default_model)
            latency = raw.latency_seconds if raw.latency_seconds is not None else round(time.perf_counter() - start, 3)
            parsed = json.loads(strip_markdown_fence(raw.text))
            if not isinstance(parsed, dict) or not str(parsed.get("key_summary", "")).strip():
                raise ValueError("reply missing key_summary")
        except Exception as exc:  # noqa: BLE001 -- provider/transport/parse failure: next provider
            errors.append(f"{name}: {exc}")
            continue
        log_call(
            FallbackOutcome(
                bundle=None, answer=None, urls=[], raw_intent=None, raw_slots={}, reason="spin_cycle",
                provider=name, model=raw.model, usage=raw.usage, latency_seconds=latency, error=None, source="spin",
            ),
            f"[spin_cycle {date}]",
        )
        topics = parsed.get("topics")
        entry = {
            "summary": str(parsed.get("summary", "")).strip(),
            "topics": [str(t) for t in topics] if isinstance(topics, list) else [],
            "details": str(parsed.get("details", "")).strip(),
            "key_summary": " ".join(str(parsed["key_summary"]).split()),
        }
        return entry, {"provider": name, "model": raw.model, "latency_s": latency, "usage": raw.usage}
    raise RuntimeError("; ".join(errors) or "no provider available")
