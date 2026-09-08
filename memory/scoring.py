"""propose_and_score() -- the memory write policy's decision function.

Pure decision, no I/O: takes a candidate (Gemini's optional
memory_candidate field, already type-checked into a MemoryCandidate --
still untrusted content at this point) plus the existing MEMORY.md
bundle, and returns accept/reject + where + what. store.write_memory()
is the only thing that ever touches the file, same match()/execute()
separation routing/route.py keeps between deciding and acting.

This mirrors llm_fallback/claude_code/fallback.py's validate_pick():
"never trust the model blindly." validate_pick() re-checks an intent
name against a closed set with an exact-match test; there is no
equivalent categorical guarantee for a free-text memory candidate, so
this applies a priority-ordered policy instead -- the closest structural
analogue available for prose rather than a closed vocabulary.

Priority order (first matching rule wins), per the approved plan:
  1. Explicit remember-request                    -> accept
  2. Correction or stated preference               -> accept
  3. Fact repeated across sightings                -> accept on 2nd+ sighting,
                                                       reject (but remember) on 1st
  4. Tied to an active project/goal                -> accept
  5. Otherwise (transient one-off)                 -> reject

Usage:
    python memory/scoring.py    # runs fixture-based scoring demo, no LLM/mic needed
"""

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from . import store
    from .schema import CATEGORY_TO_SECTION, MemoryBundle, MemoryCandidate, MemoryLine, ScoredDecision
except ImportError:
    import store
    from schema import CATEGORY_TO_SECTION, MemoryBundle, MemoryCandidate, MemoryLine, ScoredDecision

# routing/ is a sibling directory one level up -- same sys.path bootstrap
# fallback_gemini.py uses, so `from routing.config import normalise`/
# `from routing import matcher` resolve regardless of invocation cwd or
# whether this module was imported as a package or run directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Checked against the raw transcript, not just the model's self-reported
# confidence -- same "don't just trust what the model claims about its
# own output" instinct as validate_pick(), applied to the "was this
# actually an explicit request" question instead of an intent name.
_EXPLICIT_REMEMBER_RE = re.compile(
    r"\b(remember (that|this|i)|don'?t forget|make a note|keep in mind)\b", re.IGNORECASE
)

# Dedup/reinforcement threshold for "is this the same fact as an earlier
# one" -- looser than store.DEDUP_SIMILARITY_THRESHOLD's "is this
# literally already written down," since two phrasings of the same fact
# across two different turns won't be word-for-word identical the way a
# re-detected duplicate within one write would be.
REPEATED_FACT_SIMILARITY_THRESHOLD = 0.75

# How long an unconfirmed first sighting stays eligible to be reinforced
# into an accept by a second, similar sighting. Uncalibrated placeholder,
# same posture as listener/continuity.py's constants -- this is a
# single-user always-on process, so an in-memory cache (not a persistent
# store) is fine for this; it resets on restart, which just means a fact
# mentioned once before a restart needs to be mentioned twice again after
# one. That's an acceptable cost for not building a second persistent
# store just to track "has this been said before."
PENDING_FACT_TTL_SECONDS = 3600.0


@dataclass
class PendingFactCache:
    """Bounded, in-memory record of fact candidates seen once but not yet
    accepted -- the "requires holding candidates across calls" piece
    scoring.py's docstring above describes. Held by whatever object also
    holds the DomainRouter/ContinuityBuffer for the process's lifetime,
    not persisted.
    """

    max_items: int = 200
    _items: list[tuple[str, float]] = field(default_factory=list)  # (normalised text, seen_at)

    def _prune(self, now: float) -> None:
        self._items = [
            (text, seen_at) for text, seen_at in self._items if now - seen_at < PENDING_FACT_TTL_SECONDS
        ][-self.max_items :]

    def check_and_record(self, text: str, model=None, now: float | None = None) -> bool:
        """Returns True if a similar candidate was already pending (this
        is the 2nd+ sighting -> caller should accept), False if this is
        the first sighting (recorded for next time, caller should
        reject-for-now). Uses embedding cosine similarity when a model is
        given (reuses routing.matcher.embed(), no separate mechanism),
        else falls back to normalised-string equality -- same
        normalise() routing/config.py already uses for exact-alias
        comparison, a reasonable degradation when no model is on hand.
        """
        now = time.monotonic() if now is None else now
        self._prune(now)

        matched = False
        if model is not None:
            try:
                from routing import matcher
            except ImportError:
                import matcher
            if self._items:
                query = matcher.embed(model, [text])
                existing = matcher.embed(model, [t for t, _ in self._items])
                import torch

                sims = (existing @ query.T).squeeze(1)
                matched = bool(torch.max(sims) >= REPEATED_FACT_SIMILARITY_THRESHOLD)
        else:
            try:
                from routing.config import normalise
            except ImportError:
                from config import normalise
            normalised = normalise(text)
            matched = any(normalise(t) == normalised for t, _ in self._items)

        if not matched:
            self._items.append((text, now))
        return matched


def _find_similar_existing(
    bundle: MemoryBundle, section: str, text: str, model=None
) -> MemoryLine | None:
    """Existing line in `section` that's already essentially this fact,
    for merge/reinforcement in write_memory() rather than a fresh
    duplicate bullet."""
    existing_lines = bundle.lines_in(section)
    if not existing_lines:
        return None
    if model is not None:
        try:
            from routing import matcher
        except ImportError:
            import matcher
        import torch

        query = matcher.embed(model, [text])
        candidates = matcher.embed(model, [line.text for line in existing_lines])
        sims = (candidates @ query.T).squeeze(1)
        best = int(torch.argmax(sims))
        if float(sims[best]) >= store.DEDUP_SIMILARITY_THRESHOLD:
            return existing_lines[best]
        return None
    try:
        from routing.config import normalise
    except ImportError:
        from config import normalise
    normalised = normalise(text)
    for line in existing_lines:
        if normalise(line.text) == normalised:
            return line
    return None


def propose_and_score(
    candidate: MemoryCandidate | None,
    existing: MemoryBundle,
    transcript: str,
    pending: PendingFactCache | None = None,
    model=None,
) -> ScoredDecision:
    if candidate is None:
        return ScoredDecision(accept=False, section=None, text=None, merge_into=None, reason="no candidate proposed")

    text = candidate.text.strip()
    if not text:
        return ScoredDecision(accept=False, section=None, text=None, merge_into=None, reason="empty candidate text")

    category = candidate.category
    if category not in CATEGORY_TO_SECTION:
        return ScoredDecision(
            accept=False, section=None, text=None, merge_into=None,
            reason=f"unknown category {category!r}, not one of {sorted(CATEGORY_TO_SECTION)}",
        )
    section = CATEGORY_TO_SECTION[category]

    # 1. Explicit remember-request -- checked locally against the real
    # transcript, not just candidate.confidence's self-report.
    if candidate.confidence == "explicit_request" or _EXPLICIT_REMEMBER_RE.search(transcript):
        merge = _find_similar_existing(existing, section, text, model=model)
        return ScoredDecision(accept=True, section=section, text=text, merge_into=merge, reason="explicit remember-request")

    # 2. Correction or stated preference.
    if category in ("correction", "preference"):
        # A correction should update wherever the original fact already
        # lives, not always land in Known Facts -- search every section
        # for a near-duplicate before falling back to this category's
        # default section.
        if category == "correction":
            for candidate_section in CATEGORY_TO_SECTION.values():
                merge = _find_similar_existing(existing, candidate_section, text, model=model)
                if merge is not None:
                    return ScoredDecision(
                        accept=True, section=candidate_section, text=text, merge_into=merge, reason="correction of an existing entry"
                    )
        merge = _find_similar_existing(existing, section, text, model=model)
        return ScoredDecision(accept=True, section=section, text=text, merge_into=merge, reason=f"{category}")

    # 3. Repeated fact -- first sighting rejects (but remembers), second
    # similar sighting accepts as reinforcement.
    if category == "fact":
        already_known = _find_similar_existing(existing, "Known Facts", text, model=model)
        if already_known is not None:
            return ScoredDecision(accept=True, section=section, text=text, merge_into=already_known, reason="matches an already-known fact")
        if pending is not None and pending.check_and_record(text, model=model):
            return ScoredDecision(accept=True, section=section, text=text, merge_into=None, reason="repeated across two sightings")
        return ScoredDecision(accept=False, section=None, text=None, merge_into=None, reason="single unconfirmed sighting, not yet written")

    # 4. Tied to an active project/goal.
    if category == "project":
        merge = _find_similar_existing(existing, section, text, model=model)
        return ScoredDecision(accept=True, section=section, text=text, merge_into=merge, reason="active project/goal")

    # 5. Otherwise (category == "event", or anything else that fell through) -- events
    # are logged as transient by default; only accept if explicitly requested
    # (already handled above) or clearly project-tied. A bare "event" candidate
    # with plain "inferred" confidence and no repetition is the transient
    # one-off case the policy exists to filter out.
    return ScoredDecision(accept=False, section=None, text=None, merge_into=None, reason="transient one-off, not promoted")


if __name__ == "__main__":
    from schema import MemoryCandidate as _MC

    fixture = store._empty_bundle()
    pending_cache = PendingFactCache()

    cases = [
        ("remember that I prefer Brave over Chrome", _MC(text="Prefers Brave over Chrome", category="preference", confidence="explicit_request")),
        ("I'm building a domain-aware fallback layer for EKKO", _MC(text="Building a domain-aware fallback layer for EKKO", category="project", confidence="inferred")),
        ("I use a Discord server called the lab", _MC(text="Uses a Discord server called 'the lab'", category="fact", confidence="inferred")),
        ("yeah I use the lab discord for updates too", _MC(text="Uses a Discord server called 'the lab'", category="fact", confidence="inferred")),
        ("it's kind of sunny today I guess", _MC(text="Mentioned it was sunny today", category="event", confidence="inferred")),
    ]
    for transcript, cand in cases:
        decision = propose_and_score(cand, fixture, transcript, pending=pending_cache)
        print(f"{transcript!r}\n  -> accept={decision.accept} section={decision.section} reason={decision.reason!r}\n")
