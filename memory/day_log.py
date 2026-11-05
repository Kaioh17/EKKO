"""Day log -- every exchange EKKO has, kept for 24h, one JSONL file per local
date (memory/day_log/YYYY-MM-DD.jsonl).

This is the LLM's conversation memory, shared by voice, chat and file-op
consulting: window() is the newest turns, whole, up to a token budget --
what every NO_MATCH prompt carries as `conversation`. Turns that scroll out
of it are folded into memory/today_summary.py. No timer clears anything.
Written unconditionally -- a command, an answer, a "didn't catch that", a
decline and a consult all count.

Per-date files, not one big file: the listener (a subprocess) and the
backend's chat path both append, and spin_cycle.py deletes a whole file
only once its newest entry is >24h old, so an append never races with
compaction and nothing ever rewrites a file in place.

Never raises: recording a turn must not break the turn.
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

DAY_LOG_DIR = data("memory", "day_log")

WINDOW_HOURS = 24
# Hard cap on the day block sent to the model, in estimated tokens. A chatty
# day drops its *oldest* turns from the prompt (never from disk) rather than
# blow the context window. Tune from logs/context_metrics.jsonl.
TOKEN_BUDGET = 6000
# One long answer shouldn't eat the budget; the full text stays on disk.
ANSWER_CHAR_CAP = 600


def est_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def record_turn(
    user: str,
    assistant: str | None,
    *,
    source: str = "voice",
    kind: str = "answer",
    now: datetime.datetime | None = None,
    directory: str | Path = DAY_LOG_DIR,
) -> None:
    """kind: answer | command | unrecognized | consult | decline. `assistant` is what EKKO said
    or showed (None/empty when it stayed quiet)."""
    if not (user or "").strip():
        return
    now = now or datetime.datetime.now()
    entry = {"ts": now.isoformat(), "source": source, "kind": kind, "user": user.strip(), "assistant": (assistant or "").strip()}
    try:
        directory = Path(directory)
        os.makedirs(directory, exist_ok=True)
        with open(directory / f"{now.date().isoformat()}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_day(path: str | Path) -> list[dict]:
    """Entries of one date file, oldest first. A bad line is skipped."""
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("ts"), str):
                    out.append(obj)
    except OSError:
        pass
    return sorted(out, key=lambda e: e["ts"])


def recent_turns(
    now: datetime.datetime | None = None,
    hours: float = WINDOW_HOURS,
    directory: str | Path = DAY_LOG_DIR,
) -> list[dict]:
    """Entries from the last `hours`, oldest first."""
    now = now or datetime.datetime.now()
    cutoff = (now - datetime.timedelta(hours=hours)).isoformat()
    first = (now - datetime.timedelta(hours=hours)).date()
    turns: list[dict] = []
    directory = Path(directory)
    for day in (first + datetime.timedelta(days=i) for i in range((now.date() - first).days + 1)):
        turns += [e for e in read_day(directory / f"{day.isoformat()}.jsonl") if e["ts"] >= cutoff]
    return turns


def render_block(turns: list[dict], token_budget: int = TOKEN_BUDGET) -> tuple[str, dict]:
    """(text for the prompt, stats for logs/context_metrics.jsonl). Oldest
    turns are dropped until the block fits `token_budget`."""
    lines = []
    for t in turns:
        answer = t.get("assistant", "")
        if len(answer) > ANSWER_CHAR_CAP:
            answer = answer[:ANSWER_CHAR_CAP] + "..."
        lines.append(f"[{t['ts'][:16].replace('T', ' ')}|{t.get('source', 'voice')}] user: {t.get('user', '')}\n  ekko: {answer or '(no reply)'}")
    costs = [est_tokens(line) for line in lines]
    start, total = 0, sum(costs)
    while start < len(lines) and total > token_budget:
        total -= costs[start]
        start += 1
    stats = {"day_turns": len(lines) - start, "day_tokens_est": total, "truncated_turns": start}
    return "\n".join(lines[start:]), stats


WINDOW_TOKEN_BUDGET = 4000


def format_turn(t: dict) -> str:
    return (
        f"[{t['ts'][:16].replace('T', ' ')}|{t.get('source', 'voice')}|{t.get('kind', 'answer')}] "
        f"user: {t.get('user', '')}\n  ekko: {t.get('assistant') or '(no reply)'}"
    )


def window(turns: list[dict], token_budget: int = WINDOW_TOKEN_BUDGET) -> tuple[list[dict], str, dict]:
    """The active transcript: newest turns, whole (no ANSWER_CHAR_CAP),
    until `token_budget`. The newest turn is always kept, even alone over
    budget. Returns (turns in window oldest first, prompt block, stats)."""
    kept: list[dict] = []
    total = 0
    for t in reversed(turns):
        cost = est_tokens(format_turn(t))
        if kept and total + cost > token_budget:
            break
        kept.append(t)
        total += cost
    kept.reverse()
    stats = {"window_turns": len(kept), "window_tokens_est": total, "evicted_turns": len(turns) - len(kept)}
    return kept, "\n".join(format_turn(t) for t in kept), stats


def delete_turn(ts: str, directory: str | Path = DAY_LOG_DIR) -> bool:
    """Forget one exchange (matched by its exact `ts`). Rewrites that date's
    file atomically; an append landing in the same instant could be lost,
    which is acceptable for a deliberate user action."""
    try:
        day = datetime.datetime.fromisoformat(ts).date().isoformat()
    except ValueError:
        return False

    base_dir = Path(directory).resolve()
    path = (base_dir / f"{day}.jsonl").resolve()
    try:
        path.relative_to(base_dir)
    except ValueError:
        return False

    entries = read_day(path)
    kept = [e for e in entries if e["ts"] != ts]
    if len(kept) == len(entries):
        return False
    if not kept:
        path.unlink(missing_ok=True)
        return True
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept), encoding="utf-8")
    os.replace(tmp, path)
    return True


def clear_all(directory: str | Path = DAY_LOG_DIR) -> int:
    """Forget the whole 24h log. Returns how many files were removed."""
    directory = Path(directory)
    files = list(directory.glob("*.jsonl")) if directory.is_dir() else []
    for f in files:
        f.unlink(missing_ok=True)
    return len(files)


def past_days(
    now: datetime.datetime | None = None,
    hours: float = WINDOW_HOURS,
    directory: str | Path = DAY_LOG_DIR,
) -> list[tuple[str, Path, list[dict], bool]]:
    """(date, path, entries, expired) for every date file before today,
    oldest first. A past day is ready to archive at once; `expired` (newest
    entry older than `hours`) says its raw file is no longer needed for the
    transcript window and may be deleted."""
    now = now or datetime.datetime.now()
    cutoff = (now - datetime.timedelta(hours=hours)).isoformat()
    today = now.date().isoformat()
    out = []
    directory = Path(directory)
    for path in sorted(directory.glob("*.jsonl")) if directory.is_dir() else []:
        if path.stem >= today:
            continue
        entries = read_day(path)
        if entries:
            out.append((path.stem, path, entries, entries[-1]["ts"] < cutoff))
        elif path.stat().st_mtime < now.timestamp() - hours * 3600:
            path.unlink(missing_ok=True)  # empty/garbled leftovers: nothing to keep
    return out


if __name__ == "__main__":
    block, stats = render_block(recent_turns())
    print(f"{DAY_LOG_DIR}  {stats}")
    print(block or "(nothing in the last 24h)")
