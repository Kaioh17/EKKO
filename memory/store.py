"""Read/write MEMORY.md -- pure I/O, no scoring decisions here (that's
scoring.py, same match()/execute() split routing/route.py already keeps
between deciding and acting).

MEMORY.md is a plain, hand-editable markdown file: sectioned with `##`
headers (memory.schema.SECTIONS, fixed order) and dated bullet lines. A
person can open and edit it directly -- read_memory() only cares about
`## <section>` headers and `- <text>` bullets, an optional leading
`YYYY-MM-DD: ` on a bullet becomes its date, everything else on a line is
the fact text.

Usage:
    python memory/store.py                 # print the current bundle
    python memory/store.py --path other.md
"""

import argparse
import datetime
import json
import os
import re
from pathlib import Path

try:
    from .schema import MemoryBundle, MemoryCandidate, MemoryLine, ScoredDecision, SECTIONS
except ImportError:
    from schema import MemoryBundle, MemoryCandidate, MemoryLine, ScoredDecision, SECTIONS

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_MEMORY_PATH = _PACKAGE_DIR / "MEMORY.md"

# Same append-only JSONL shape as routing/logs/routing.jsonl and
# routing/logs/domains.jsonl -- one entry per scoring.propose_and_score()
# call, accepted or not, so "is memory actually being written" is
# answerable from a file instead of console prints that scroll away once
# the listener has been running a while. See log_decision().
DEFAULT_LOG_PATH = _PACKAGE_DIR / "logs" / "decisions.jsonl"

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_DATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):\s*(.*)$")

# High because this is dedup (the same fact noticed again), not fuzzy
# topic matching -- see scoring.py's repeated-fact rule, which needs a
# looser threshold for "is this the same fact as before" than this one
# needs for "is this bullet literally already there."
DEDUP_SIMILARITY_THRESHOLD = 0.85


def _empty_bundle() -> MemoryBundle:
    return MemoryBundle(sections={section: () for section in SECTIONS})


def read_memory(path: str | Path = DEFAULT_MEMORY_PATH) -> MemoryBundle:
    """Never raises. A missing or malformed file degrades to an empty
    bundle -- same "derived/user data that can't be trusted just starts
    over" posture fallback_gemini.load_usage_summary() already uses for
    its JSON file, applied here to a markdown file instead.
    """
    path = Path(path)
    if not path.exists():
        return _empty_bundle()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _empty_bundle()

    sections: dict[str, list[MemoryLine]] = {section: [] for section in SECTIONS}
    current: str | None = None
    for raw_line in text.splitlines():
        section_match = _SECTION_RE.match(raw_line)
        if section_match:
            heading = section_match.group(1).strip()
            current = heading if heading in sections else None
            continue
        if current is None:
            continue
        bullet_match = _BULLET_RE.match(raw_line)
        if not bullet_match:
            continue
        body = bullet_match.group(1).strip()
        dated_match = _DATED_RE.match(body)
        if dated_match:
            sections[current].append(MemoryLine(date=dated_match.group(1), text=dated_match.group(2).strip()))
        else:
            # Hand-written line with no date stamp -- kept as-is, dated
            # today only once write_memory() ever touches it again.
            sections[current].append(MemoryLine(date="", text=body))

    return MemoryBundle(sections={section: tuple(lines) for section, lines in sections.items()})


def render_memory(bundle: MemoryBundle) -> str:
    parts = []
    for section in SECTIONS:
        parts.append(f"## {section}")
        lines = bundle.sections.get(section, ())
        if not lines:
            parts.append("")
            continue
        for line in lines:
            stamp = f"{line.date}: " if line.date else ""
            parts.append(f"- {stamp}{line.text}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_memory(
    section: str,
    text: str,
    merge_into: MemoryLine | None,
    path: str | Path = DEFAULT_MEMORY_PATH,
    today: str | None = None,
) -> MemoryBundle:
    """Read-modify-write the whole file. If merge_into names an existing
    line (scoring.py found a near-duplicate already in this section),
    that line's date is bumped instead of appending a new bullet, keeping
    the file from accumulating repeats of the same fact. Atomic write
    (.tmp + os.replace) since this file could in principle be read by a
    person's editor at the same moment EKKO is writing it.
    """
    path = Path(path)
    today = today or datetime.date.today().isoformat()
    bundle = read_memory(path)
    sections = {s: list(lines) for s, lines in bundle.sections.items()}
    if section not in sections:
        raise ValueError(f"unknown section {section!r}, must be one of {SECTIONS}")

    lines = sections[section]
    if merge_into is not None:
        for i, line in enumerate(lines):
            if line.date == merge_into.date and line.text == merge_into.text:
                lines[i] = MemoryLine(date=today, text=text)
                break
        else:
            lines.append(MemoryLine(date=today, text=text))
    else:
        lines.append(MemoryLine(date=today, text=text))
    sections[section] = lines

    new_bundle = MemoryBundle(sections={s: tuple(v) for s, v in sections.items()})
    rendered = render_memory(new_bundle)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    os.makedirs(path.parent, exist_ok=True)
    tmp_path.write_text(rendered, encoding="utf-8")
    os.replace(tmp_path, path)
    return new_bundle


def log_decision(
    candidate: MemoryCandidate | None,
    decision: ScoredDecision,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> None:
    """Append one JSON object per propose_and_score() call -- called from
    the same call site as write_memory() (see listener/vad_listener.py's
    _handle_command()), logging every decision, accept or reject, not
    just the ones that actually touched MEMORY.md. scoring.py stays a pure
    decision function (see its module docstring); this is the impure
    logging half, same split routing/route.py keeps between route() and
    log_decision() there.

    candidate.text is included deliberately, not redacted: an accepted
    candidate's text is about to be written into MEMORY.md itself (plain
    text on disk already, see memory/README.md's privacy note), and a
    rejected candidate's text is what makes "why didn't this get
    remembered" answerable at all -- there'd be nothing left to debug
    otherwise.
    """
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "candidate_text": candidate.text if candidate else None,
        "category": candidate.category if candidate else None,
        "confidence": candidate.confidence if candidate else None,
        "accept": decision.accept,
        "section": decision.section,
        "merged": decision.merge_into is not None,
        "reason": decision.reason,
    }
    log_path = Path(log_path)
    os.makedirs(log_path.parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default=str(DEFAULT_MEMORY_PATH), help="Path to MEMORY.md")
    args = parser.parse_args()

    loaded = read_memory(args.path)
    total = sum(len(v) for v in loaded.sections.values())
    print(f"{args.path}  ({total} line(s) across {len(SECTIONS)} sections)\n")
    for sect in SECTIONS:
        entries = loaded.sections.get(sect, ())
        print(f"## {sect} ({len(entries)})")
        for entry in entries:
            stamp = f"{entry.date}: " if entry.date else ""
            print(f"  - {stamp}{entry.text}")
    print("\n--- as_prompt_block() ---")
    block = loaded.as_prompt_block()
    print(block if block else "(empty -- nothing to inject)")
