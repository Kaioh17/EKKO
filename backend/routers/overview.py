"""Read-only stats for the UI, read straight from the files that already
track them -- nothing here is stored in backend/db.py."""

import json
import os
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
from listener.vad_listener import DEFAULT_HARD_STOP_HOTKEY, DEFAULT_MANUAL_WAKE_HOTKEY
from llm_fallback.claude_code import fallback as claude_code
from llm_fallback.gemini import fallback_gemini as gemini
from llm_fallback.gemini.client import API_KEY_ENV
from llm_fallback.ollama import fallback_ollama as ollama
from memory import short_term
from memory.store import DEFAULT_LOG_PATH as MEMORY_DECISIONS_LOG
from routing import host
from routing.route import DEFAULT_LOG_PATH as ROUTING_LOG

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


def _llm_usage() -> tuple[LlmUsageOut, list[LlmUsageOut]]:
    """(total, per-provider) from each provider's running usage_summary.json."""
    g, c, o = gemini.load_usage_summary(), claude_code.load_usage_summary(), ollama.load_usage_summary()
    claude_tokens = ("input", "output", "cache_creation_input", "cache_read_input")
    providers = [
        _usage("gemini", g.get("total_calls", 0), g.get("total_latency_seconds", 0.0),
               g.get("total_totalTokenCount", 0), g.get("total_cachedContentTokenCount", 0), None),
        _usage("claude_code", c.get("total_calls", 0), None,
               sum(c.get(f"total_{k}_tokens", 0) for k in claude_tokens),
               c.get("total_cache_read_input_tokens", 0), c.get("total_cost_usd", 0.0)),
        _usage("ollama", o.get("total_calls", 0), o.get("total_duration_ns", 0) / 1e9,
               o.get("total_prompt_eval_count", 0) + o.get("total_eval_count", 0), 0, None),
    ]
    # Latency averaged over providers that track it (claude_code doesn't).
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
    key = os.environ.get(API_KEY_ENV)
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
        gemini_key_hint=(key[-4:] if len(key) > 12 else "set") if key else None,
    )
