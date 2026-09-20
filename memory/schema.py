"""Shared types for the memory module. No I/O here -- see store.py for
reading/writing MEMORY.md and scoring.py for the accept/reject decision.

There is no memory system anywhere else in this codebase to match the
shape of (confirmed by exhaustive search -- see intent_routing.md/
readme.md for what does exist: write-only logs, never read back). This
is greenfield, built to mirror the same "typed spec, strict validation"
shape routing/config.py already uses for intents.yaml.
"""

from dataclasses import dataclass

# Fixed section order -- also the file's on-disk header order, so
# MEMORY.md reads the same every time it's regenerated rather than
# reshuffling based on dict insertion order.
SECTIONS: tuple[str, ...] = (
    "Identity",
    "Preferences",
    "Active Projects",
    "Recent Events",
    "Known Facts",
    "Open Threads",
)

# category (from a Gemini memory_candidate field) -> which section it
# lands in. "correction" isn't its own section -- a correction updates
# whichever section the corrected fact already lives in (or Known Facts
# if it's new), see scoring.py.
CATEGORY_TO_SECTION: dict[str, str] = {
    "preference": "Preferences",
    "project": "Active Projects",
    "event": "Recent Events",
    "fact": "Known Facts",
    "correction": "Known Facts",  # default target; scoring.py may retarget to wherever the original lives
}

CONFIDENCE_LEVELS = ("explicit_request", "stated_preference", "inferred")


@dataclass(frozen=True)
class MemoryLine:
    date: str  # ISO date, e.g. "2026-08-22"
    text: str


@dataclass(frozen=True)
class MemoryBundle:
    """Parsed MEMORY.md. Read-only -- store.write_memory() is the only
    thing that ever produces a new file on disk, and it re-parses fresh
    each time rather than mutating a bundle in place (same
    read-modify-write discipline as routing/config.py loading fresh
    rather than trusting cached state to still be current).
    """

    sections: dict[str, tuple[MemoryLine, ...]]

    def lines_in(self, section: str) -> tuple[MemoryLine, ...]:
        return self.sections.get(section, ())

    def all_lines(self) -> list[tuple[str, MemoryLine]]:
        return [(section, line) for section in SECTIONS for line in self.sections.get(section, ())]

    def as_prompt_block(self, max_lines_per_section: int = 8) -> str:
        """Compact, capped rendering for injection into a Gemini
        system_instruction. Capped per-section, not just overall, so one
        overgrown section (e.g. Recent Events) can't crowd out Identity/
        Preferences, which matter more per-line and change less often.
        """
        parts = ["# MEMORY (persisted across sessions, read-only context for you)"]
        any_content = False
        for section in SECTIONS:
            lines = self.sections.get(section, ())
            if not lines:
                continue
            any_content = True
            parts.append(f"\n## {section}")
            for line in lines[-max_lines_per_section:]:
                parts.append(f"- {line.text}")
        if not any_content:
            return ""
        return "\n".join(parts)


@dataclass(frozen=True)
class MemoryCandidate:
    """What a Gemini JSON reply's optional memory_candidate field
    deserializes into, after basic type-checking (see store.py's
    parsing) -- this is still untrusted model output at this point.
    scoring.propose_and_score() is what decides whether it's ever
    written anywhere.
    """

    text: str
    category: str  # one of CATEGORY_TO_SECTION's keys, else rejected
    confidence: str  # one of CONFIDENCE_LEVELS, else treated as "inferred"


@dataclass(frozen=True)
class ScoredDecision:
    accept: bool
    section: str | None  # None when accept is False
    text: str | None  # final text to write, None when accept is False
    merge_into: MemoryLine | None  # existing line to bump the date on, or None to append fresh
    reason: str  # short clause, for a log a human might read later

@dataclass(frozen=True)
class ShortMemoryResponse:

    prior_question: str  # the transcript that produced this turn (fallback_outcome's source transcript)
    prior_answer: str  # the llm's spoken answer (fallback_outcome.answer)
    follow_up: str  # the llm's grounded next-step question (fallback_outcome.follow_up)
    follow_up_answer: str | None = None
    timestamp: str = ""
    status: str = "active"
