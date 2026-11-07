"""STT progress report: the metrics in listener/STT_IMPROVEMENTS.md's
Baseline table, from listener/segment_log.py's segments.jsonl, or from the
older listener.log + transcripts.jsonl when there's no segment log yet.

Usage:
    python -m listener.stt_report                  # print the metrics
    python -m listener.stt_report --append "what changed"   # also add a Check-backs row
"""

import argparse
import datetime
import json
import re
from pathlib import Path

import numpy as np

from listener.segment_log import LOG_PATH as SEGMENTS_PATH
from listener.stt import ekko_phrases, normalize
from paths import ROOT, data

LEGACY_LOG_PATH = data("backend", "logs", "listener.log")
LEGACY_TRANSCRIPTS_PATH = data("listener", "captures", "transcripts.jsonl")
DOC_PATH = ROOT / "listener" / "STT_IMPROVEMENTS.md"


def _pct(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def summarize(records: list[dict], echo_phrases: frozenset[str] = frozenset()) -> dict:
    activations = first_try = silent_timeouts = overflow_acts = 0
    accepted = echo = overflows = overflows_open = 0
    rejected: dict[str, int] = {}
    stt: list[float] = []
    # Per-activation state: did a command segment or an overflow show up
    # before the next activation?
    window_open = seen_command = seen_overflow = False

    def close_window():
        nonlocal overflow_acts
        if window_open and seen_overflow:
            overflow_acts += 1

    for r in records:
        event = r.get("event")
        if event == "activation":
            close_window()
            activations += 1
            window_open, seen_command, seen_overflow = True, False, False
        elif event == "overflow":
            overflows += 1
            overflows_open += bool(r.get("gate_open"))
            seen_overflow = True
        elif event == "timeout":
            if window_open and not seen_command:
                silent_timeouts += 1
        elif event == "segment" and r.get("kind") == "command":
            ok = r.get("outcome") == "accepted"
            if window_open and not seen_command and ok:
                first_try += 1
            seen_command = True
            if r.get("stt_ms") is not None:
                stt.append(r["stt_ms"])
            if ok:
                accepted += 1
                echo += normalize(r.get("text") or "") in echo_phrases
            else:
                reason = r.get("reason") or "unknown"
                rejected[reason] = rejected.get(reason, 0) + 1
    close_window()

    return {
        "activations": activations,
        "first_try_accepts": first_try,
        "silent_timeouts": silent_timeouts,
        "overflows": overflows,
        "overflows_gate_open": overflows_open,
        "overflow_activations": overflow_acts,
        "accepted": accepted,
        "rejected": rejected,
        "stt_p50_ms": _pct(stt, 50),
        "stt_p90_ms": _pct(stt, 90),
        "echo_accepts": echo,
    }


_LEGACY_ACTIVATION = re.compile(r"manual wake triggered|wake word detected \(verification|\] verified\b")


def _legacy_records(log_lines: list[str]) -> list[dict]:
    """listener.log's printed lines as segment_log-shaped records."""
    records = []
    for line in log_lines:
        if _LEGACY_ACTIVATION.search(line):
            records.append({"event": "activation"})
        elif "input overflow" in line:
            records.append({"event": "overflow", "gate_open": None})
        elif "no command heard" in line:
            records.append({"event": "timeout"})
        elif line.lstrip().startswith("transcript:"):
            text = line.split("transcript:", 1)[1].strip().strip("'\"")
            records.append({"event": "segment", "kind": "command", "outcome": "accepted", "text": text})
        elif "ignoring segment:" in line:
            # The reason ends at its numbers "(" or the em dash, which the
            # Windows console encoding turns into U+FFFD.
            reason = re.split(r"\s*[(—�]", line.split("ignoring segment:", 1)[1])[0].strip()
            records.append({"event": "segment", "kind": "command", "outcome": "rejected", "reason": reason})
    return records


def summarize_legacy(
    log_lines: list[str], transcript_lines: list[str], echo_phrases: frozenset[str] = frozenset()
) -> dict:
    s = summarize(_legacy_records(log_lines), echo_phrases)
    # listener.log has no timings; transcripts.jsonl does, as the gap between
    # the capture's save time (its filename) and the transcript being logged.
    latency, echo = [], 0
    for line in transcript_lines:
        if not line.strip():
            continue
        e = json.loads(line)
        if e["transcript"].startswith("[wake"):
            continue
        echo += normalize(e["transcript"]) in echo_phrases
        m = re.search(r"speech_(\d{8}_\d{6}_\d{6})", e.get("wav_path") or "")
        if m:
            saved = datetime.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S_%f")
            ms = (datetime.datetime.fromisoformat(e["timestamp"]) - saved).total_seconds() * 1000
            if 0 <= ms < 30_000:
                latency.append(ms)
    s.update(stt_p50_ms=_pct(latency, 50), stt_p90_ms=_pct(latency, 90), echo_accepts=echo)
    return s


def _jsonl(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # blank or half-written line
    return records


def report(
    segments_path: Path = SEGMENTS_PATH,
    log_path: Path = LEGACY_LOG_PATH,
    transcripts_path: Path = LEGACY_TRANSCRIPTS_PATH,
    echo_phrases: frozenset[str] | None = None,
) -> dict:
    if echo_phrases is None:
        echo_phrases = ekko_phrases()
    records = _jsonl(Path(segments_path)) if Path(segments_path).exists() else []
    if records:
        return {"source": "segments", **summarize(records, echo_phrases)}
    log = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines() if Path(log_path).exists() else []
    tr = Path(transcripts_path).read_text(encoding="utf-8").splitlines() if Path(transcripts_path).exists() else []
    return {"source": "legacy", **summarize_legacy(log, tr, echo_phrases)}


def recent(path=None, n: int = 50) -> dict:
    """The summary over the last `n` command segments in the segment log, for
    the app's Speech recognition card. Empty and well-formed with no log."""
    from listener import segment_log

    records = _jsonl(Path(path or segment_log.LOG_PATH)) if Path(path or segment_log.LOG_PATH).exists() else []
    is_command = [r.get("event") == "segment" and r.get("kind") == "command" for r in records]
    start = 0
    if sum(is_command) > n:  # begin at the n-th command segment from the end
        seen = 0
        for i in range(len(records) - 1, -1, -1):
            seen += is_command[i]
            if seen == n:
                start = i
                break
    window = records[start:]
    return {"n_segments": sum(is_command[start:]), **summarize(window)}


def append_checkback(md_path: Path, row: list[str]) -> None:
    """Add `row` as the last row of the table under '## Check-backs'."""
    lines = Path(md_path).read_text(encoding="utf-8").splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "## Check-backs")
        first = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("|"))
    except StopIteration:
        raise ValueError(f"no Check-backs table in {md_path}") from None
    last = first
    while last + 1 < len(lines) and lines[last + 1].startswith("|"):
        last += 1
    if not lines[last].endswith("\n"):
        lines[last] += "\n"
    lines.insert(last + 1, "| " + " | ".join(row) + " |\n")
    Path(md_path).write_text("".join(lines), encoding="utf-8")


def checkback_row(s: dict, change: str) -> list[str]:
    n = s["activations"]
    fmt = lambda v: "n/a" if v is None else f"{v:.0f}"  # noqa: E731
    return [
        datetime.date.today().isoformat(),
        change,
        f"{s['first_try_accepts']}/{n}",
        f"{s['silent_timeouts']}/{n}",
        f"{s['overflow_activations']}/{n}",
        f"{fmt(s['stt_p50_ms'])} / {fmt(s['stt_p90_ms'])} ms",
        str(s["echo_accepts"]),
        f"source: {s['source']}",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--append", metavar="CHANGE", help="add a Check-backs row to STT_IMPROVEMENTS.md")
    args = parser.parse_args()
    s = report()
    print(json.dumps(s, indent=2))
    if args.append:
        append_checkback(DOC_PATH, checkback_row(s, args.append))
        print(f"added a Check-backs row to {DOC_PATH}")


if __name__ == "__main__":
    main()
