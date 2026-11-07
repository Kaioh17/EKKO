"""One JSON line per closed speech segment or listener event, so STT changes
can be measured instead of guessed. listener/stt_report.py reads it; the
record shapes are documented in listener/STT_IMPROVEMENTS.md (Segment log)."""

import datetime
import json
import os
from pathlib import Path

from paths import data

LOG_PATH = data("listener", "logs", "segments.jsonl")


def write(record: dict, path: Path = LOG_PATH) -> None:
    try:
        os.makedirs(Path(path).parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.datetime.now().isoformat(), **record}) + "\n")
    except OSError:
        pass  # a full disk or locked file must never stop the listener


def reason_code(reason: str | None) -> str | None:
    """A rejection reason without its per-clip numbers, for grouping."""
    if reason is None:
        return None
    head = reason.split(" (", 1)[0].strip()
    return "too short" if head.startswith("only ") and head.endswith(" long") else head


def overflow_event(status, gate_open: bool) -> dict | None:
    """The record for a PortAudio input overflow, or None. `gate_open` tells
    whether the lost audio could have been the user (open) or was being
    dropped anyway while EKKO spoke (closed)."""
    if status is not None and getattr(status, "input_overflow", False):
        return {"event": "overflow", "gate_open": gate_open}
    return None
