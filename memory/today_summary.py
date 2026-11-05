"""Running summary of today: memory/today_summary.json.

{"date": "2026-11-04", "summary": "...", "topics": [...],
 "covered_until": "<ts of the last day-log turn folded in>", "updated_at": "..."}

The LLM's live transcript is the day-log window (memory/day_log.window);
turns that scroll out of it are folded in here so the rest of today stays in
context. Only the backend writes this file (spin_cycle's loop and the chat
path's nudge), under one lock, atomically -- the voice listener and the file
gateway only append to the day log.

A new date resets it: yesterday is archived from its raw day log by
spin_cycle, which is more faithful than re-compressing this summary.
"""

import datetime
import json
import os
import sys
import threading
from pathlib import Path
from typing import Callable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from paths import data  # noqa: E402

from memory import day_log  # noqa: E402

SUMMARY_PATH = data("memory", "today_summary.json")
# The chat nudge waits for this much evicted text before paying for a call;
# spin_cycle's 5-minute tick folds whatever is pending regardless.
MIN_FOLD_TOKENS = 800

_lock = threading.Lock()


def read(path: str | Path = SUMMARY_PATH) -> dict | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) and isinstance(raw.get("date"), str) else None


def read_today(now: datetime.datetime | None = None, path: str | Path = SUMMARY_PATH) -> dict | None:
    """The summary only if it is today's -- a leftover from yesterday is not context."""
    s = read(path)
    today = (now or datetime.datetime.now()).date().isoformat()
    return s if s and s["date"] == today else None


def _write(entry: dict, path: Path) -> None:
    os.makedirs(path.parent, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def clear(path: str | Path = SUMMARY_PATH) -> bool:
    with _lock:
        existed = Path(path).exists()
        Path(path).unlink(missing_ok=True)
        return existed


def pending_turns(
    now: datetime.datetime | None = None,
    path: str | Path = SUMMARY_PATH,
    directory: str | Path = day_log.DAY_LOG_DIR,
    budget: int = day_log.WINDOW_TOKEN_BUDGET,
) -> list[dict]:
    """Today's turns that have left the window and aren't summarized yet."""
    now = now or datetime.datetime.now()
    turns = day_log.recent_turns(now, directory=directory)
    in_window, _, _ = day_log.window(turns, budget)
    evicted = turns[: len(turns) - len(in_window)]
    current = read_today(now, path)
    covered = current["covered_until"] if current else ""
    today = now.date().isoformat()
    return [t for t in evicted if t["ts"][:10] == today and t["ts"] > covered]


def _default_summarize(previous: str, block: str):
    from llm_fallback.brain.summarize import summarize_today  # lazy: keeps memory/ importable without providers

    return summarize_today(previous, block)


def refresh(
    now: datetime.datetime | None = None,
    force: bool = False,
    summarize: Callable[[str, str], tuple[dict, dict]] | None = None,
    path: str | Path = SUMMARY_PATH,
    directory: str | Path = day_log.DAY_LOG_DIR,
) -> dict | None:
    """Fold pending turns into today's summary. Returns the new summary, or
    None when there was nothing (or not enough, unless `force`) to fold.
    Raises on summarizer failure; nothing is written then, so the same
    turns are retried next time."""
    now = now or datetime.datetime.now()
    path = Path(path)
    with _lock:
        pending = pending_turns(now, path, directory)
        if not pending:
            return None
        block = "\n".join(day_log.format_turn(t) for t in pending)  # whole answers, like the window
        if not force and day_log.est_tokens(block) < MIN_FOLD_TOKENS:
            return None
        current = read_today(now, path)
        result, _meta = (summarize or _default_summarize)(current["summary"] if current else "", block)
        entry = {
            "date": now.date().isoformat(),
            "summary": result["summary"],
            "topics": result.get("topics", []),
            "covered_until": pending[-1]["ts"],
            "updated_at": now.isoformat(),
        }
        _write(entry, path)
        return entry


def refresh_async() -> None:
    """Fire-and-forget nudge after a turn is recorded. Skipped while a
    refresh is already running; never raises."""
    if _lock.locked():
        return

    def _run() -> None:
        try:
            refresh()
        except Exception as exc:  # noqa: BLE001 -- retried on the next nudge or tick
            print(f"  [memory] today summary refresh failed: {exc}")

    threading.Thread(target=_run, daemon=True, name="today-summary").start()


if __name__ == "__main__":
    print(json.dumps(read(), indent=2) if read() else f"{SUMMARY_PATH}  (no summary yet)")
