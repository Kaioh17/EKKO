"""What the LLM gets as conversational context, and the log of how big it was.

gather() is the one call both the voice listener and the chat path make
before llm_fallback: the last 24h of exchanges (memory/day_log.py) plus the
one-line keys of older days (memory/archive.py). attempt_fallback() writes
one line per LLM call to memory/logs/context_metrics.jsonl so speed, prompt
size, truncation and recall behaviour can be reviewed and the budgets in
day_log.py / archive.py tuned from real numbers.
"""

import datetime
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from paths import data  # noqa: E402

from memory import archive, day_log  # noqa: E402

METRICS_PATH = data("memory", "logs", "context_metrics.jsonl")


def gather() -> tuple[str | None, list[str] | None, dict]:
    """(day_context, archive_keys, stats) -- never raises, degrades to empty."""
    try:
        block, stats = day_log.render_block(day_log.recent_turns())
        keys = archive.index_keys()
    except Exception:  # noqa: BLE001 -- memory trouble must not block an answer
        return None, None, {}
    stats |= {"archive_keys_sent": len(keys), "archive_key_tokens_est": day_log.est_tokens("\n".join(keys))}
    return block or None, keys or None, stats


def log_metrics(entry: dict, path: str | Path = METRICS_PATH) -> None:
    try:
        path = Path(path)
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": datetime.datetime.now().isoformat(), **entry}) + "\n")
    except OSError:
        pass
