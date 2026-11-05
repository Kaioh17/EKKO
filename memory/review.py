"""Review queue: Keep-class exchanges (instructions, recipes) still in the 24h
log, offered to the user before they age out. Nothing is stored for a pending
item -- it is derived from the day log on every read -- so the only state is
which exchanges the user already resolved (memory/review.json).

Similar questions ("how do i make a kite" / "how to make a kite") collapse into
one item showing the newest answer; resolving it resolves every version.
"""

import datetime
import json
import os
import re
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from paths import data  # noqa: E402

from memory import day_log, store  # noqa: E402

RESOLVED_PATH = data("memory", "review.json")
SAVED_DIR = Path.home() / "ekko-sandbox"  # default folder for "Save to file"
SAME_QUESTION = 0.8
MEMORY_SECTION = "Known Facts"
ReviewAction = Literal["file", "memory", "discard"]
MEMORY_LINE_CHARS = 300  # a Long Term line rides in every prompt; a pointer, not the whole recipe


def _read(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _mark(ts_list: list[str], action: str, now: datetime.datetime, path: Path) -> None:
    cutoff = (now - datetime.timedelta(hours=2 * day_log.WINDOW_HOURS)).isoformat()
    resolved = {ts: a for ts, a in _read(path).items() if ts >= cutoff}  # entries older than the log are moot
    resolved.update({ts: action for ts in ts_list})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(resolved, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def _slug(title: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", title.lower())[:5]) or "saved-note"


def _preview(answer: str) -> str:
    plain = " ".join(re.sub(r"[#*|>`_-]+", " ", answer).split())
    return plain if len(plain) <= 180 else plain[:180].rstrip() + "…"


@dataclass(frozen=True)
class ReviewItem:
    id: str  # ts of the newest version
    title: str
    preview: str
    answer: str
    filename: str  # suggested
    expires_at: str
    versions: int
    members: list[str]  # every version's ts


def candidates(
    now: datetime.datetime | None = None,
    directory: str | Path = day_log.DAY_LOG_DIR,
    path: str | Path = RESOLVED_PATH,
) -> list[ReviewItem]:
    """Unresolved Keep items, newest first. `id` is the newest
    version's ts; `members` is every version's ts."""
    now = now or datetime.datetime.now()
    resolved = _read(Path(path))
    groups: list[list[dict]] = []
    for t in day_log.recent_turns(now, directory=directory):
        if day_log.classify(t) != day_log.KEEP or t["ts"] in resolved:
            continue
        for g in groups:
            if SequenceMatcher(None, _norm(g[-1]["user"]), _norm(t["user"])).ratio() >= SAME_QUESTION:
                g.append(t)
                break
        else:
            groups.append([t])
    window = datetime.timedelta(hours=day_log.WINDOW_HOURS)
    items = [
        ReviewItem(
            id=g[-1]["ts"],
            title=g[-1]["user"][:80],
            preview=_preview(g[-1]["assistant"]),
            answer=g[-1]["assistant"],
            filename=_slug(g[-1]["user"]) + ".md",
            expires_at=(datetime.datetime.fromisoformat(g[-1]["ts"]) + window).isoformat(),
            versions=len(g),
            members=[t["ts"] for t in g],
        )
        for g in groups
    ]
    return sorted(items, key=lambda i: i.id, reverse=True)


def _safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "-", Path(name.strip()).name).lstrip(".-")
    if not base:
        raise ValueError("file name can't be empty")
    return base if "." in base else base + ".md"


def _save_file(item: ReviewItem, filename: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem, _, ext = _safe_filename(filename).rpartition(".")
    target, n = directory / f"{stem}.{ext}", 2
    while target.exists():  # never overwrite something the user saved earlier
        target = directory / f"{stem}-{n}.{ext}"
        n += 1
    target.write_text(f"# {item.title}\n\n{item.answer}\n", encoding="utf-8")
    return target


def resolve(
    item_id: str,
    action: ReviewAction,
    filename: str | None = None,
    folder: str | None = None,
    now: datetime.datetime | None = None,
    directory: str | Path = day_log.DAY_LOG_DIR,
    path: str | Path = RESOLVED_PATH,
    saved_dir: str | Path = SAVED_DIR,
) -> str:
    """file | memory | discard. Returns a one-line outcome for the UI. Raises
    LookupError if the item is gone (already resolved, or aged out), ValueError
    for a bad file name or folder. The item is only marked resolved after the save succeeded."""
    now = now or datetime.datetime.now()
    item = next((i for i in candidates(now, directory, path) if i.id == item_id), None)
    if item is None:
        raise LookupError("that item is no longer in the queue -- refresh")
    if action == "file":
        target = Path(folder.strip()).expanduser() if folder and folder.strip() else Path(saved_dir)
        if not target.is_absolute():
            raise ValueError("folder must be an absolute path (or start with ~)")
        outcome = f"Saved to {_save_file(item, filename or item.filename, target)}"
    elif action == "memory":
        store.add_line(MEMORY_SECTION, f"{item.title}: {item.preview}"[:MEMORY_LINE_CHARS])
        outcome = f"Added to Long Term memory ({MEMORY_SECTION})"
    else:
        outcome = "Discarded"
    _mark(item.members, action, now, Path(path))
    return outcome
