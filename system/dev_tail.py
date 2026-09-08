"""Live, multi-source log dashboard for EKKO's dev mode.

Not a handler, not tied to any intent -- see system/README.md's opening
paragraph, this directory is process lifecycle for the listener, not the
execution boundary (that's scripts/). Launched by system/dev.ps1 in a
new Windows Terminal tab: same "PowerShell owns the window, Python owns
the rendering" split scripts/lib/system_metrics.ps1's Open-ReportWindow
uses for scripts/render_report.py, just live instead of one-shot, and
living here rather than under scripts/ since it isn't reachable from a
voice command and isn't named by any intent.

Tails six sources at once, each tagged and colored so a fast-scrolling
mixed stream stays readable:
    LISTENER  system/logs/listener.out.log       -- vad_listener.py's own
              print() stream (wake word hits, speech start/end, saves,
              stream warnings), shown as-is.
    ERROR     system/logs/listener.err.log       -- stderr/tracebacks.
    ROUTE     routing/logs/routing.jsonl         -- one JSON object per
              routed transcript (intent, score, status, slots), decoded
              and reformatted onto one line.
    CAPTURE   listener/captures/transcripts.jsonl -- one JSON object per
              accepted command segment (transcript, verify_score),
              likewise decoded.
    DOMAIN    routing/logs/domains.jsonl         -- one JSON object per
              routing/domains.py DomainRouter.match() call, matched or
              not (every domain's raw/boosted score against threshold),
              decoded. This is the "is domain attribution actually
              happening" answer -- see routing/domains.py's log_match().
    MEMORY    memory/logs/decisions.jsonl        -- one JSON object per
              memory/scoring.py propose_and_score() call, accepted or
              rejected, decoded. This is the "is memory actually being
              written" answer -- see memory/store.py's log_decision().

None of these files have to exist yet -- a fresh checkout, or the
listener not having run yet -- every source is polled for existence, not
opened once and assumed to stay there. Backfills the last --lines
(default 20) of whichever files already exist so the screen isn't blank
on open, then polls every --interval seconds (default 0.3) for new bytes,
same shape as `Get-Content -Wait`. No inotify/watchdog dependency: these
are local append-only text files written at speech-driven rates, not
network volume, so polling is plenty.

Log lines are read and matched byte-for-byte, not line-for-line: a poll
that lands mid-write (the writer flushed a partial line) holds that
incomplete tail back rather than splitting a JSON line in half and
failing to parse it -- see _read_new_lines. Files are also handled being
truncated out from under this: system/*.ps1's own size-capped rotation
renames an oversized log to .old and starts the original fresh, which
this notices (new size < last known position) and restarts from byte 0
of the new file instead of seeking past its end forever.

Every displayed line goes through rich's Text object, never an f-string
handed to console.print() as markup -- transcripts are arbitrary speech,
free to contain "[", "]" or anything else that would otherwise be parsed
as (and potentially break on) rich markup syntax.

Usage:
    python system/dev_tail.py
    python system/dev_tail.py --lines 50
    python system/dev_tail.py --only listener,route
"""

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.text import Text

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Source:
    tag: str
    path: Path
    style: str
    # None for the two plain-text listener logs; a key into _FORMATTERS
    # for the two JSONL sources.
    formatter_key: str | None = None


def _format_route(obj: dict) -> str:
    ts = (obj.get("timestamp") or "")[11:19]  # HH:MM:SS out of an isoformat string
    status = obj.get("status", "?")
    intent = obj.get("intent") or "-"
    score = obj.get("score")
    score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "?"
    transcript = obj.get("transcript", "")
    slots = obj.get("slots") or {}
    slot_str = f" slots={slots}" if slots else ""
    missing = obj.get("missing_slot")
    missing_str = f" missing_slot={missing!r}" if missing else ""
    return f"{ts}  {status:<16} {intent:<22} {score_str}  {transcript!r}{slot_str}{missing_str}"


def _format_capture(obj: dict) -> str:
    ts = (obj.get("timestamp") or "")[11:19]
    score = obj.get("verify_score")
    score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "-"
    transcript = obj.get("transcript", "")
    return f"{ts}  verify={score_str}  {transcript!r}"


def _format_domain(obj: dict) -> str:
    ts = (obj.get("timestamp") or "")[11:19]
    matched = obj.get("matched") or "-"
    transcript = obj.get("transcript", "")
    scores = obj.get("scores") or {}
    score_str = " ".join(
        f"{key}={s.get('raw', 0):.3f}{'*' if s.get('cleared') else ''}" for key, s in scores.items()
    )
    return f"{ts}  matched={matched:<10} {score_str}  {transcript!r}"


def _format_memory(obj: dict) -> str:
    ts = (obj.get("timestamp") or "")[11:19]
    accept = "accept" if obj.get("accept") else "reject"
    section = obj.get("section") or "-"
    reason = obj.get("reason", "")
    text = obj.get("candidate_text")
    text_str = f"  {text!r}" if text else "  (no candidate)"
    return f"{ts}  {accept:<6} {section:<16} {reason}{text_str}"


_FORMATTERS = {"route": _format_route, "capture": _format_capture, "domain": _format_domain, "memory": _format_memory}

_ALL_SOURCES = [
    Source("LISTENER", _PROJECT_ROOT / "system" / "logs" / "listener.out.log", "white"),
    Source("ERROR", _PROJECT_ROOT / "system" / "logs" / "listener.err.log", "bold red"),
    Source("ROUTE", _PROJECT_ROOT / "routing" / "logs" / "routing.jsonl", "cyan", "route"),
    Source("CAPTURE", _PROJECT_ROOT / "listener" / "captures" / "transcripts.jsonl", "green", "capture"),
    Source("DOMAIN", _PROJECT_ROOT / "routing" / "logs" / "domains.jsonl", "magenta", "domain"),
    Source("MEMORY", _PROJECT_ROOT / "memory" / "logs" / "decisions.jsonl", "yellow", "memory"),
]


def _render(source: Source, line: str) -> str:
    if source.formatter_key is None:
        return line
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return line  # malformed/partial despite the newline check -- show raw rather than drop
    return _FORMATTERS[source.formatter_key](obj)


def _emit(console: Console, source: Source, line: str, backfilled: bool) -> None:
    if not line.strip():
        return
    text = Text()
    if backfilled:
        text.append("(backfill) ", style="dim")
    text.append(f"{source.tag:<9}", style=source.style)
    text.append(" ")
    text.append(_render(source, line))
    console.print(text)


def _read_new_lines(path: Path, position: int) -> tuple[list[str], int]:
    """Byte-exact tail of one file since `position`. Returns (complete
    lines found, new position). Binary mode throughout, not text mode:
    text-mode file objects only guarantee seek() is valid at offsets
    previously returned by tell() on THAT handle, which a position
    computed on a prior poll's handle isn't -- see the io module docs.
    Byte offsets from a binary handle don't have that restriction.
    """
    if not path.exists():
        return [], position
    size = path.stat().st_size
    if size < position:
        position = 0  # truncated/rotated out from under us, restart from the top
    if size == position:
        return [], position

    with path.open("rb") as f:
        f.seek(position)
        chunk = f.read()

    last_newline = chunk.rfind(b"\n")
    if last_newline == -1:
        return [], position  # nothing complete yet, hold for the next poll

    complete = chunk[: last_newline + 1]
    new_position = position + len(complete)
    lines = complete.decode("utf-8", errors="replace").splitlines()
    return lines, new_position


def follow(sources: list[Source], backfill_lines: int, interval: float, console: Console) -> None:
    positions: dict[str, int] = {}
    for source in sources:
        if source.path.exists():
            size = source.path.stat().st_size
            text = source.path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines()[-backfill_lines:] if backfill_lines else []:
                _emit(console, source, line, backfilled=True)
            positions[source.tag] = size
        else:
            positions[source.tag] = 0

    console.print(Text(f"-- live, watching {len(sources)} source(s), Ctrl+C to stop --", style="dim"))
    try:
        while True:
            for source in sources:
                new_lines, positions[source.tag] = _read_new_lines(source.path, positions[source.tag])
                for line in new_lines:
                    _emit(console, source, line, backfilled=False)
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print(Text("stopped", style="dim"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lines", type=int, default=20, help="Backfill this many existing lines per source (default: 20)."
    )
    parser.add_argument(
        "--interval", type=float, default=0.3, help="Poll interval in seconds for new lines (default: 0.3)."
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated subset of listener,error,route,capture,domain,memory (default: all six).",
    )
    args = parser.parse_args()

    sources = _ALL_SOURCES
    if args.only:
        wanted = {tag.strip().lower() for tag in args.only.split(",") if tag.strip()}
        unknown = wanted - {s.tag.lower() for s in _ALL_SOURCES}
        if unknown:
            raise SystemExit(
                f"--only: unknown source(s) {sorted(unknown)}, expected listener,error,route,capture,domain,memory"
            )
        sources = [s for s in _ALL_SOURCES if s.tag.lower() in wanted]

    console = Console(highlight=False)
    follow(sources, args.lines, args.interval, console)


if __name__ == "__main__":
    main()
