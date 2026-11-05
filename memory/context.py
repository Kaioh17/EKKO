"""What the LLM gets as conversational context, and the log of how big it was.

gather() is the one call the voice listener, the chat path and file-op
consulting make before an LLM call: the active transcript window (newest
day-log turns, whole, up to a token budget -- memory/day_log.py), today's
running summary of what scrolled out (memory/today_summary.py) and the
one-line keys of older days (memory/archive.py). attempt_fallback() writes
one line per LLM call to memory/logs/context_metrics.jsonl so speed, prompt
size, truncation and recall behaviour can be reviewed and the budgets in
day_log.py / archive.py tuned from real numbers.
"""

import datetime
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from paths import data  # noqa: E402

from memory import archive, day_log, store, today_summary  # noqa: E402

METRICS_PATH = data("memory", "logs", "context_metrics.jsonl")


@dataclass(frozen=True)
class Context:
    conversation: str | None = None  # the day-log window, whole turns, oldest first
    recent_user_turns: tuple[str, ...] = ()  # what the user said in the window, oldest first
    today_summary: str | None = None  # today's turns that scrolled out of the window
    archive_keys: list[str] | None = None
    stats: dict = field(default_factory=dict)

    def as_kwargs(self) -> dict:
        """The attempt_fallback() keyword arguments this context fills."""
        return {
            "conversation": self.conversation,
            "recent_user_turns": self.recent_user_turns,
            "today_summary": self.today_summary,
            "archive_keys": self.archive_keys,
            "context_stats": self.stats,
        }


def gather() -> Context:
    """Never raises, degrades to an empty Context. Read before the current
    turn is recorded, so the window holds only earlier turns."""
    try:
        turns, block, stats = day_log.window(day_log.recent_turns())
        summary = today_summary.read_today()
        keys = archive.index_keys()
    except Exception:  # noqa: BLE001 -- memory trouble must not block an answer
        return Context()
    summary_text = summary["summary"] if summary else None
    stats |= {
        "archive_keys_sent": len(keys),
        "archive_key_tokens_est": day_log.est_tokens("\n".join(keys)),
        "today_summary_tokens_est": day_log.est_tokens(summary_text or ""),
    }
    users = tuple(t["user"] for t in turns if t.get("user") and t.get("kind") != "decline")
    return Context(block or None, users, summary_text, keys or None, stats)


def describe() -> list[tuple[str, str]]:
    """The memory the next message carries, named, as gather() + MEMORY.md give it
    (not the fixed instructions or intent list). For the UI's context view."""
    ctx = gather()
    return [
        ("Long-term memory", store.read_memory().as_prompt_block()),
        ("Archive titles", "\n".join(ctx.archive_keys or [])),
        ("Today so far", ctx.today_summary or ""),
        ("Live conversation", ctx.conversation or ""),
    ]


def log_metrics(entry: dict, path: str | Path = METRICS_PATH) -> None:
    try:
        path = Path(path)
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": datetime.datetime.now().isoformat(), **entry}) + "\n")
    except OSError:
        pass
