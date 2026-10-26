"""Spin cycle: compact every day older than 24h into one archive entry.

For each date file in memory/day_log/ whose newest entry is >24h old:
summarize it (one LLM call), write the archive entry, and only then delete
the day file. A failure leaves the file untouched and the next tick retries,
so a provider outage or spend cap loses nothing. Re-running is harmless: a
date already in the archive is never summarized twice.

run() is called at backend startup (catches up after the PC was off) and
hourly after that (backend/main.py). `python -m memory.spin_cycle` runs it
once by hand.

A day whose worth score (day_worth) is under MIN_WORTH -- e.g. only stock
commands -- is deleted without a summary call.

Every attempt is one line in memory/logs/spin_cycle.jsonl.
"""

import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from paths import data  # noqa: E402

from memory import archive  # noqa: E402
from memory.day_log import DAY_LOG_DIR, est_tokens, expired_days, render_block  # noqa: E402

LOG_PATH = data("memory", "logs", "spin_cycle.jsonl")
# The whole day goes to the summarizer (it's one call per day); this only
# stops a pathological day from exceeding any model's window.
SUMMARY_INPUT_BUDGET = 30000
INTERVAL_S = 3600

# Decision matrix: is a day worth an archive entry? Each *distinct* user
# phrase scores by how the turn was handled (repeats of one phrase are a
# generic pattern and count once); a day below MIN_WORTH is dropped unarchived.
# Deterministic commands barely count, a real LLM answer does, a miss is noise.
KIND_WEIGHT = {"answer": 3.0, "command": 0.5, "unrecognized": 0.0}
LONG_PHRASE_WORDS, LONG_PHRASE_BONUS = 6, 1.0  # a long request isn't a stock command
MIN_WORTH = 3.0


def day_worth(entries: list[dict]) -> float:
    best: dict[str, float] = {}
    for e in entries:
        text = " ".join(e.get("user", "").lower().split())
        w = KIND_WEIGHT.get(e.get("kind", "answer"), 0.0)
        if w and len(text.split()) >= LONG_PHRASE_WORDS:
            w += LONG_PHRASE_BONUS
        best[text] = max(best.get(text, 0.0), w)
    return sum(best.values())


def _log(entry: dict, log_path: Path) -> None:
    try:
        os.makedirs(log_path.parent, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": datetime.datetime.now().isoformat(), **entry}) + "\n")
    except OSError:
        pass


def _default_summarize(date: str, block: str):
    from llm_fallback.brain.summarize import summarize_day  # lazy: keeps memory/ importable without providers

    return summarize_day(date, block)


def run(
    now: datetime.datetime | None = None,
    summarize: Callable[[str, str], tuple[dict, dict]] | None = None,
    day_dir: str | Path = DAY_LOG_DIR,
    archive_path: str | Path = archive.ARCHIVE_PATH,
    log_path: str | Path = LOG_PATH,
) -> list[dict]:
    """Returns one result dict per day attempted. Never raises."""
    summarize = summarize or _default_summarize
    log_path = Path(log_path)
    results = []
    for date, path, entries in expired_days(now, directory=day_dir):
        if archive.has_date(date, archive_path):
            path.unlink(missing_ok=True)  # already archived, e.g. crash between write and delete
            continue
        worth = day_worth(entries)
        if worth < MIN_WORTH:
            path.unlink(missing_ok=True)
            result = {"date": date, "ok": True, "skipped": "low worth", "worth": worth, "turns_in": len(entries)}
            _log(result, log_path)
            results.append(result)
            continue
        block, stats = render_block(entries, SUMMARY_INPUT_BUDGET)
        result = {"date": date, "turns_in": stats["day_turns"], "tokens_in": stats["day_tokens_est"], "truncated_turns": stats["truncated_turns"]}
        start = time.perf_counter()
        try:
            entry, meta = summarize(date, block)
            key = f"{date} | {entry.pop('key_summary')}"
            archive.write_entry(key, {**entry, "turns": len(entries)}, archive_path)
            path.unlink(missing_ok=True)
            result |= {"ok": True, "key": key, "summary_tokens": est_tokens(json.dumps(entry)), "total_s": round(time.perf_counter() - start, 3), **meta}
        except Exception as exc:  # noqa: BLE001 -- retried next tick, day file untouched
            result |= {"ok": False, "error": str(exc), "total_s": round(time.perf_counter() - start, 3)}
        _log(result, log_path)
        results.append(result)
    return results


def loop(stop=None) -> None:
    """Thread target: run now, then hourly. `stop` is an optional threading.Event."""
    while True:
        try:
            run()
        except Exception as exc:  # noqa: BLE001 -- the loop must outlive any one bad tick
            _log({"ok": False, "error": f"tick crashed: {exc}"}, LOG_PATH)
        if stop is not None:
            if stop.wait(INTERVAL_S):
                return
        else:
            time.sleep(INTERVAL_S)


if __name__ == "__main__":
    for r in run():
        print(r)
