"""Read-only stats for the UI, read straight from the files that already
track them -- nothing here is stored in backend/db.py."""

import json
from collections import deque
from pathlib import Path

from fastapi import APIRouter

from backend.routers.settings import load_general
from backend.schemas.overview import (
    GeneralOverviewOut,
    HotkeyOut,
    LlmUsageOut,
    MemoryCountsOut,
    RoutingOverviewOut,
)
from listener.defaults import DEFAULT_HARD_STOP_HOTKEY, DEFAULT_MANUAL_WAKE_HOTKEY
from llm_fallback.brain.logging import load_usage_summary, usage_summary_path_for
from memory import short_term
from memory.store import DEFAULT_LOG_PATH as MEMORY_DECISIONS_LOG
from routing import host
from routing.defaults import ROUTING_LOG_PATH as ROUTING_LOG

router = APIRouter(prefix="/api/overview", tags=["overview"])

RECENT_ROUTES = 50


def _read_jsonl(path: Path, tail: int | None = None) -> list[dict]:
    """Parsed lines (last `tail` if given); missing file or bad lines skipped."""
    # ponytail: full-file scan, seek-from-end if the logs get large
    try:
        with open(path, encoding="utf-8") as f:
            lines = deque(f, maxlen=tail)
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _usage(provider: str, calls: int, latency_s: float | None, tokens: int, cached: int, cost: float | None) -> LlmUsageOut:
    return LlmUsageOut(
        provider=provider,
        calls=calls,
        avg_latency_s=round(latency_s / calls, 3) if latency_s is not None and calls else None,
        tokens=tokens,
        cached_tokens=cached,
        cost_usd=cost,
    )


_CLAUDE_TOKENS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
_CHAT_TOKENS = ("prompt_tokens", "completion_tokens")
# provider -> (usage_totals keys that add up to "tokens", the cached-tokens key, reports cost_usd).
# Shown in this order; tests/test_catalog.py keeps it in step with the catalog.
_USAGE_FIELDS: dict[str, tuple[tuple[str, ...], str | None, bool]] = {
    "gemini": (("totalTokenCount",), "cachedContentTokenCount", False),
    "claude_code": (_CLAUDE_TOKENS, "cache_read_input_tokens", True),
    "ollama": (("prompt_eval_count", "eval_count"), None, False),
    "deepseek": (_CHAT_TOKENS, "prompt_cache_hit_tokens", True),
    "claude_api": (_CLAUDE_TOKENS, "cache_read_input_tokens", True),
    "openai": (_CHAT_TOKENS, "cached_tokens", True),
}


def _llm_usage() -> tuple[LlmUsageOut, list[LlmUsageOut]]:
    """(total, per-provider) from each provider's running usage_summary.json
    under llm_fallback/logs/<provider>/ -- see brain/logging.py. Every
    provider's summary shares the same top-level shape now
    (total_calls/total_latency_seconds/usage_totals/extras_totals), only
    the keys *inside* usage_totals/extras_totals differ per provider.
    """
    providers = []
    for name, (token_keys, cached_key, has_cost) in _USAGE_FIELDS.items():
        summary = load_usage_summary(usage_summary_path_for(name))
        totals = summary.get("usage_totals", {})
        providers.append(_usage(
            name, summary.get("total_calls", 0), summary.get("total_latency_seconds", 0.0),
            sum(totals.get(k, 0) for k in token_keys),
            totals.get(cached_key, 0) if cached_key else 0,
            summary.get("extras_totals", {}).get("cost_usd", 0.0) if has_cost else None,
        ))
    # brain/core.py measures latency_seconds uniformly for every provider
    # now (previously claude_code didn't track it at all) -- still guard
    # on calls>0 rather than assume every provider has been called yet.
    timed = [p for p in providers if p.avg_latency_s is not None]
    timed_calls = sum(p.calls for p in timed)
    total = LlmUsageOut(
        provider="total",
        calls=sum(p.calls for p in providers),
        avg_latency_s=round(sum(p.avg_latency_s * p.calls for p in timed) / timed_calls, 3) if timed_calls else None,
        tokens=sum(p.tokens for p in providers),
        cached_tokens=sum(p.cached_tokens for p in providers),
        cost_usd=round(sum(p.cost_usd or 0.0 for p in providers), 6),
    )
    return total, providers


def _routing(threshold: float) -> RoutingOverviewOut:
    recent = _read_jsonl(ROUTING_LOG, RECENT_ROUTES)
    last = recent[-1] if recent else {}
    return RoutingOverviewOut(
        recent_scores=[r["score"] for r in recent if r.get("score") is not None],
        match_rate=sum(r.get("status") != "no_match" for r in recent) / len(recent) if recent else None,
        threshold=threshold,
        last_status=last.get("status"),
        last_intent=last.get("intent"),
    )


def _memory() -> MemoryCountsOut:
    decisions = _read_jsonl(MEMORY_DECISIONS_LOG)
    accepted = sum(bool(d.get("accept")) for d in decisions)
    return MemoryCountsOut(
        accepted=accepted,
        rejected=len(decisions) - accepted,
        pending=int(short_term.read_active_short_memory() is not None),
    )


def _ekko_os() -> str | None:
    try:
        return host.current_os()
    except ValueError:
        return None


@router.get("/general")
def get_general_overview() -> GeneralOverviewOut:
    settings = load_general()
    llm_total, llm_providers = _llm_usage()
    return GeneralOverviewOut(
        llm_total=llm_total,
        llm_providers=llm_providers,
        routing=_routing(settings.routing_threshold),
        memory=_memory(),
        hotkeys=[
            HotkeyOut(name="Manual wake", chord=DEFAULT_MANUAL_WAKE_HOTKEY, enabled=not settings.no_manual_wake),
            HotkeyOut(name="Hard stop", chord=DEFAULT_HARD_STOP_HOTKEY, enabled=not settings.no_hard_stop),
        ],
        ekko_os=_ekko_os(),
    )
