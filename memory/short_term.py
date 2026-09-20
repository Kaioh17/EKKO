"""Read/write short_term.json -- one in-flight llm_fallback turn, not a
growing history. Same read/write split store.py keeps for MEMORY.md
(this module owns I/O only, no scoring/decision logic -- there isn't any
here to keep separate, a short-memory turn is written unconditionally,
never accepted/rejected the way scoring.propose_and_score() gates
MEMORY.md writes).

Lifecycle, two writes per turn:
  1. Right after an llm_fallback answer with a non-null follow_up
     (listener/vad_listener.py's _handle_command(), same call site that
     already calls memory.store.write_memory()): write_short_memory() is
     called with prior_question/prior_answer/follow_up. This overwrites
     whatever turn was on disk before -- a fresh answer always starts a
     new short-memory turn, it never merges with a stale one.
  2. If the user's very next transcript is a reply to that follow_up
     (vad_listener.py decides this, this module doesn't): write_short_memory()
     is called again, this time with only follow_up_answer. This is a
     read-modify-write against whatever's already on disk from step 1 --
     it fills in follow_up_answer and leaves the other three fields
     alone. If nothing is on disk to update (no pending turn, or it
     already has an answer), this is a no-op that returns None rather
     than inventing a turn from partial data.

short_term.json itself is a single JSON object, not a log -- it's live,
mutable state ("what's the one thing still waiting on a reply right
now"). clear_short_memory() is expected to be called by vad_listener.py
once a session ends (decline, hard stop, or session_until's timeout
elapses); it does not delete the turn, it soft-deletes it (status ->
"stale") and appends it to logs/short_memory.jsonl, so a stale turn
stops surfacing as LLM context (read_active_short_memory()) without
losing the record -- an unanswered follow_up is a failure point worth
reviewing, not just noise to discard.

Usage:
    python memory/short_term.py                 # print the current turn, if any
"""

import datetime
import json
import os
from dataclasses import replace
from pathlib import Path

try:
    from .schema import ShortMemoryResponse
except ImportError:
    from schema import ShortMemoryResponse

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_SHORT_MEMORY_PATH = _PACKAGE_DIR / "short_term.json"

# Append-only history of every turn once it goes stale -- same shape as
# memory/logs/decisions.jsonl, so "what did EKKO ask, and did the user
# ever reply" is answerable from a file instead of being overwritten the
# moment the next turn starts. This is what makes an unanswered follow_up
# (a failure point) reviewable after the fact instead of just vanishing.
DEFAULT_LOG_PATH = _PACKAGE_DIR / "logs" / "short_memory.jsonl"


def read_short_memory(path: str | Path = DEFAULT_SHORT_MEMORY_PATH) -> ShortMemoryResponse | None:
    """Never raises. A missing, empty, or malformed file degrades to
    None -- same "derived/user data that can't be trusted just starts
    over" posture as store.read_memory() and
    fallback_gemini.load_usage_summary(). None means "nothing pending,"
    the same thing an intact-but-answered turn would eventually mean once
    a caller decides it's stale, so callers should treat both alike.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return ShortMemoryResponse(
            prior_question=raw["prior_question"],
            prior_answer=raw["prior_answer"],
            follow_up=raw["follow_up"],
            follow_up_answer=raw.get("follow_up_answer"),
            timestamp=raw.get("timestamp", ""),
            status=raw.get("status", "active"),  # old files predate the field -- treat as active
        )
    except KeyError:
        # Missing one of the three fields every turn is written with --
        # not a shape this module ever produced itself, treat like any
        # other malformed file rather than half-trusting it.
        return None


def read_active_short_memory(path: str | Path = DEFAULT_SHORT_MEMORY_PATH) -> ShortMemoryResponse | None:
    """Same as read_short_memory(), except a stale (soft-deleted) turn
    comes back as None -- this is the "only active memory reaches the
    LLM" boundary. Callers that feed short_memory into a Gemini prompt
    (listener/vad_listener.py) should use this; callers just inspecting
    what's on disk (this module's __main__) can use read_short_memory()
    directly to see stale turns too.
    """
    turn = read_short_memory(path)
    if turn is None or turn.status != "active":
        return None
    return turn


def _write(turn: ShortMemoryResponse, path: str | Path) -> None:
    """Atomic write (.tmp + os.replace), same reasoning as
    store.write_memory(): short_term.json could in principle be read by
    something else (or a person's editor) at the same moment EKKO writes
    it.
    """
    path = Path(path)
    payload = {
        "prior_question": turn.prior_question,
        "prior_answer": turn.prior_answer,
        "follow_up": turn.follow_up,
        "follow_up_answer": turn.follow_up_answer,
        "timestamp": turn.timestamp,
        "status": turn.status,
    }
    os.makedirs(path.parent, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def write_short_memory(
    *,
    prior_question: str | None = None,
    prior_answer: str | None = None,
    follow_up: str | None = None,
    follow_up_answer: str | None = None,
    path: str | Path = DEFAULT_SHORT_MEMORY_PATH,
) -> ShortMemoryResponse | None:
    """Two call shapes, matching the two writes in this module's
    docstring. Which one runs is decided by whether `prior_question` is
    given -- the two shapes are never mixed in one call:

    - New turn: pass prior_question, prior_answer, follow_up (all three
      required together). follow_up_answer is never known yet at this
      point -- it isn't a parameter of this shape at all, unconditionally
      None on the fresh turn. Always overwrites whatever turn was already
      on disk, even an unanswered one: a new llm_fallback answer means a
      new question was just asked, so the previous turn's follow_up is no
      longer the thing a reply would be answering.

    - Follow-up reply: pass only follow_up_answer (prior_question left
      None). Reads the existing turn from `path` and returns a copy with
      follow_up_answer filled in. Returns None, and writes nothing, if
      there's no existing turn to update -- nothing is pending, so there
      is nothing this reply could be completing.

    Any other combination (e.g. neither shape's required arguments, or
    both) raises ValueError -- a silent no-op here would be a hard bug to
    notice from the call site.
    """
    if prior_question is not None:
        if prior_answer is None or follow_up is None:
            raise ValueError("a new turn needs prior_question, prior_answer, and follow_up together")
        if follow_up_answer is not None:
            raise ValueError("follow_up_answer isn't known yet when starting a new turn")
        turn = ShortMemoryResponse(
            prior_question=prior_question,
            prior_answer=prior_answer,
            follow_up=follow_up,
            follow_up_answer=None,
            timestamp=datetime.datetime.now().isoformat(),
            status="active",
        )
        _write(turn, path)
        return turn

    if follow_up_answer is not None:
        existing = read_short_memory(path)
        if existing is None:
            return None
        updated = replace(existing, follow_up_answer=follow_up_answer, timestamp=datetime.datetime.now().isoformat())
        _write(updated, path)
        return updated

    raise ValueError("write_short_memory() needs either a new turn (prior_question/prior_answer/follow_up) or follow_up_answer")


def clear_short_memory(
    path: str | Path = DEFAULT_SHORT_MEMORY_PATH,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> None:
    """Soft delete -- called once a session ends (decline, hard stop, or
    session_until's timeout elapsing) so a stale turn can't surface as
    context for an unrelated later conversation. Never hard-deletes: the
    turn's content (an unanswered follow_up especially -- that's a
    failure point, not noise) is appended to `log_path` and the on-disk
    turn is marked status="stale" rather than removed, so
    read_active_short_memory() stops surfacing it as LLM context while
    the record itself stays reviewable. A no-op, same as before, if
    there's nothing pending or it's already stale (don't log the same
    turn twice).
    """
    path = Path(path)
    turn = read_short_memory(path)
    if turn is None or turn.status != "active":
        return
    stale = replace(turn, status="stale")

    log_path = Path(log_path)
    os.makedirs(log_path.parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "prior_question": stale.prior_question,
            "prior_answer": stale.prior_answer,
            "follow_up": stale.follow_up,
            "follow_up_answer": stale.follow_up_answer,  # None here means the follow_up went unanswered
            "timestamp": stale.timestamp,
            "cleared_at": datetime.datetime.now().isoformat(),
        }) + "\n")

    _write(stale, path)


if __name__ == "__main__":
    current = read_short_memory()
    if current is None:
        print(f"{DEFAULT_SHORT_MEMORY_PATH}  (nothing pending)")
    else:
        print(f"{DEFAULT_SHORT_MEMORY_PATH}")
        print(f"  prior_question:  {current.prior_question!r}")
        print(f"  prior_answer:    {current.prior_answer!r}")
        print(f"  follow_up:       {current.follow_up!r}")
        print(f"  follow_up_answer:{current.follow_up_answer!r}")
        print(f"  timestamp:       {current.timestamp!r}")
        print(f"  status:          {current.status!r}")
