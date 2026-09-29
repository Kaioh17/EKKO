"""One shared log writer for every provider, instead of three
near-duplicate implementations (llm_fallback/{gemini,claude_code,ollama}
each used to reimplement log_call()/load_usage_summary()/
update_usage_summary() with only field names differing).

Each provider still gets its own logs/ directory under
llm_fallback/logs/<provider>/ -- token usage, cost, and failure modes are
genuinely different per provider and worth debugging in isolation. What's
centralized is the read-modify-write logic itself.

Usage totals are summed generically rather than off a fixed per-provider
field list: whatever numeric keys FallbackOutcome.usage happens to carry
(Gemini's promptTokenCount, Claude's input_tokens, Ollama's eval_count,
...) get accumulated under the same key in usage_summary.json, so a new
provider's usage dict needs no changes here at all.
"""

import datetime
import json
import os
import sys
from pathlib import Path

from llm_fallback.brain.outcome import FallbackOutcome

# Root on sys.path so paths.py resolves when this file runs as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from paths import data  # noqa: E402

LOGS_ROOT = data("llm_fallback", "logs")


def log_path_for(provider: str) -> Path:
    return LOGS_ROOT / provider / "fallback.jsonl"


def usage_summary_path_for(provider: str) -> Path:
    return LOGS_ROOT / provider / "usage_summary.json"


def log_call(outcome: FallbackOutcome, transcript: str, log_path: str | Path | None = None) -> None:
    """Appends one line to <provider>/fallback.jsonl and folds this call's
    usage/latency into <provider>/usage_summary.json. Called for every
    call regardless of outcome -- a timed-out or errored call still
    measured real latency and, if it got a response back at all, spent
    real tokens, same "count it even if it wasn't usable" reasoning every
    provider already followed independently.
    """
    custom_path = log_path is not None
    log_path = Path(log_path) if custom_path else log_path_for(outcome.provider)
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "transcript": transcript,
        "provider": outcome.provider,
        "model": outcome.model,
        "usage": outcome.usage,
        "extras": outcome.extras,
        "latency_seconds": outcome.latency_seconds,
        "raw_intent": outcome.raw_intent,
        "raw_slots": outcome.raw_slots,
        "answer": outcome.answer,
        "urls": outcome.urls,
        "follow_up": outcome.follow_up,
        # Text intentionally omitted from the log -- only whether a
        # candidate was proposed and what category, enough to tune
        # scoring.py's thresholds later without duplicating personal
        # content into a second file.
        "memory_candidate_category": outcome.memory_candidate.category if outcome.memory_candidate else None,
        "domain": outcome.domain,
        "source": outcome.source,
        "reason": outcome.reason,
        "validated": outcome.validated,
        "final_intent": outcome.bundle.intent if outcome.bundle else None,
        "error": outcome.error,
    }
    os.makedirs(log_path.parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    summary_path = log_path.parent / "usage_summary.json" if custom_path else usage_summary_path_for(outcome.provider)
    update_usage_summary(
        outcome.usage,
        outcome.extras,
        outcome.latency_seconds,
        entry["timestamp"],
        summary_path=summary_path,
    )


def _empty_usage_summary() -> dict:
    return {
        "total_calls": 0,
        "total_latency_seconds": 0.0,
        "usage_totals": {},
        # Provider-specific accounting with no shared meaning (Claude's
        # total_cost_usd/session count, Ollama's total_duration_ns) --
        # same generic "sum whatever numeric keys show up" treatment as
        # usage_totals, kept separate since it's not comparable across
        # providers. See backend/routers/overview.py for a reader.
        "extras_totals": {},
        "first_call": None,
        "last_call": None,
    }


def load_usage_summary(summary_path: str | Path) -> dict:
    """The running total, or a fresh zeroed one if the file doesn't exist
    yet or somehow got corrupted -- a summary file that can't be read is a
    reason to start counting again, not a reason to crash the caller.
    """
    summary_path = Path(summary_path)
    if not summary_path.exists():
        return _empty_usage_summary()
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_usage_summary()


def _sum_into(totals: dict, values: dict) -> None:
    for key, value in (values or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            totals[key] = totals.get(key, 0) + value


def update_usage_summary(
    usage: dict,
    extras: dict,
    latency_seconds: float | None,
    timestamp: str,
    summary_path: str | Path,
) -> dict:
    """Accumulates one call's usage/extras/latency into the running total,
    called from log_call() for every call regardless of outcome.
    """
    summary_path = Path(summary_path)
    summary = load_usage_summary(summary_path)
    summary.setdefault("usage_totals", {})
    summary.setdefault("extras_totals", {})
    summary["total_calls"] += 1
    summary["total_latency_seconds"] = round(
        summary.get("total_latency_seconds", 0.0) + (latency_seconds or 0.0), 3
    )
    _sum_into(summary["usage_totals"], usage)
    _sum_into(summary["extras_totals"], extras)
    if summary["first_call"] is None:
        summary["first_call"] = timestamp
    summary["last_call"] = timestamp

    os.makedirs(summary_path.parent, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
