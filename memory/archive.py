"""Archive of past days: memory/archive.json.

{"2026-10-03 | planned the installer build": {"summary": "...", "topics": [...],
  "turns": 42, "details": "longer context"}}

The key (date + a very short summary) is all the LLM sees about a past day;
the value is fetched only when the model asks for it (`recall` in the reply,
see llm_fallback/brain/core.py). Written atomically, never raises on read.
"""

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from paths import data  # noqa: E402

from memory.day_log import est_tokens  # noqa: E402

ARCHIVE_PATH = data("memory", "archive.json")

# Newest keys sent with every prompt (~20 tokens each).
KEY_CAP = 60
# Total size of the values fetched for one recall.
FETCH_TOKEN_CAP = 1500


def read_archive(path: str | Path = ARCHIVE_PATH) -> dict[str, dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def has_date(date: str, path: str | Path = ARCHIVE_PATH) -> bool:
    return any(k.startswith(date) for k in read_archive(path))


def write_entry(key: str, value: dict, path: str | Path = ARCHIVE_PATH) -> None:
    path = Path(path)
    archive = read_archive(path)
    archive[key] = value
    os.makedirs(path.parent, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def delete_entry(key: str, path: str | Path = ARCHIVE_PATH) -> bool:
    path = Path(path)
    archive = read_archive(path)
    if key not in archive:
        return False
    del archive[key]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return True


def index_keys(limit: int = KEY_CAP, path: str | Path = ARCHIVE_PATH) -> list[str]:
    """Newest first. Keys start with an ISO date, so a reverse sort is by date."""
    return sorted(read_archive(path), reverse=True)[:limit]


def fetch(keys: list[str], token_cap: int = FETCH_TOKEN_CAP, path: str | Path = ARCHIVE_PATH) -> tuple[str, dict]:
    """(text for the prompt, stats). A key matches exactly, else by its date
    prefix -- the model only has to get the date right."""
    archive = read_archive(path)
    parts, hits, used = [], 0, 0
    for wanted in keys:
        key = wanted if wanted in archive else next((k for k in archive if k[:10] == wanted[:10]), None)
        if key is None:
            continue
        value = archive[key]
        text = f"{key}\n{value.get('summary', '')}\n{value.get('details', '')}".strip()
        room = (token_cap - used) * 4
        if room <= 0:
            break
        text = text[:room]
        used += est_tokens(text)
        hits += 1
        parts.append(text)
    return "\n\n".join(parts), {"recall_keys": len(keys), "recall_hits": hits, "recall_tokens_est": used}
