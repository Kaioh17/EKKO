"""Recency-weighted domain continuity buffer.

Stands in for "TF-IDF context management" from the original brief, which
doesn't exist anywhere in this codebase (see intent_routing.md / readme.md
-- the router is pure MiniLM cosine similarity, no cross-turn context at
all). Rather than add a second, weaker similarity mechanism next to the
embedding one already doing the real work, this reuses the same anchor
score from routing/domains.py and just tracks *which domain was recently
active*, so a context-free follow-up ("what account should I use") can
still clear a domain's threshold shortly after a clear finance turn, even
though its own raw anchor score alone (measured: ~0.3-0.45 for a bare
"is that a good idea"/"what should I do next") sits well below any
reasonable threshold.

This buffer only ever adds a bounded boost to a domain's raw anchor
score (routing/domains.py's match_domain() caps that boost at the
domain's own registry.yaml continuity_boost ceiling) -- it can never
itself decide a match, and it has no path to routing/route.py's command
security boundary at all.

Usage:
    python listener/continuity.py     # runs a scripted decay demo
"""

import time
from dataclasses import dataclass, field


@dataclass
class Turn:
    transcript: str
    domain: str | None  # which domain this turn resolved to, if any
    timestamp: float  # time.monotonic() at record() time


@dataclass
class ContinuityBuffer:
    """Held by the same long-lived object that holds routing.route.Router
    (constructed once at listener startup), not a global -- see
    listener/vad_listener.py's integration point.

    max_turns / decay_seconds are uncalibrated placeholders, flagged the
    same way routing/matcher.py flags DEFAULT_THRESHOLD before real
    calibration data exists: max_turns=6 is "a handful of exchanges, not
    a long memory" (that's memory/'s job, not this buffer's), and
    decay_seconds=120 is "long enough to cover a genuine same-topic
    follow-up 30-60s later, short enough not to drag a stale domain into
    an unrelated later conversation." Re-tune both once
    llm_fallback/gemini/logs/fallback.jsonl has real domain-tagged calls
    to look at.
    """

    max_turns: int = 6
    decay_seconds: float = 120.0
    _turns: list[Turn] = field(default_factory=list)

    def record(self, transcript: str, domain: str | None, now: float | None = None) -> None:
        """Records every turn that reaches the fallback path, matched or
        not -- domain=None still occupies a slot, so an intervening
        unrelated turn correctly ages out a stale domain rather than
        being skipped over (recency is turn-count-aware, not
        domain-turn-count-aware, matching how a real conversation
        actually interleaves).
        """
        now = time.monotonic() if now is None else now
        self._turns.append(Turn(transcript=transcript, domain=domain, timestamp=now))
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns :]

    def boost_for(self, domain_key: str, now: float | None = None) -> float:
        """Sum of per-turn contributions from recent turns tagged with
        domain_key, each decaying linearly to 0 over decay_seconds. The
        caller (routing/domains.py's match_domain()) is responsible for
        capping this at the domain's own continuity_boost ceiling -- this
        function reports the raw recency signal, not the clamped one, so
        that ceiling stays a single, visible decision in one place
        (registry.yaml) rather than duplicated here.
        """
        now = time.monotonic() if now is None else now
        total = 0.0
        for turn in self._turns:
            if turn.domain != domain_key:
                continue
            age = now - turn.timestamp
            if age < 0 or age >= self.decay_seconds:
                continue
            total += 1.0 - (age / self.decay_seconds)
        return total

    def clear(self) -> None:
        self._turns.clear()


if __name__ == "__main__":
    # Scripted decay demo -- no mic, no API key, matches this project's
    # "each module gets its own manually-runnable check" convention.
    buf = ContinuityBuffer(max_turns=6, decay_seconds=120.0)
    t0 = 1000.0
    buf.record("should I start investing", "finance", now=t0)
    print(f"t=0s     boost_for('finance') = {buf.boost_for('finance', now=t0):.3f}")
    print(f"t=30s    boost_for('finance') = {buf.boost_for('finance', now=t0 + 30):.3f}")
    print(f"t=60s    boost_for('finance') = {buf.boost_for('finance', now=t0 + 60):.3f}")
    print(f"t=119s   boost_for('finance') = {buf.boost_for('finance', now=t0 + 119):.3f}")
    print(f"t=121s   boost_for('finance') = {buf.boost_for('finance', now=t0 + 121):.3f} (expired)")

    print("\n-- an intervening unrelated turn still ages the buffer normally --")
    buf.record("open task manager", None, now=t0 + 10)
    print(f"t=40s    boost_for('finance') = {buf.boost_for('finance', now=t0 + 40):.3f}")

    print("\n-- max_turns eviction --")
    buf2 = ContinuityBuffer(max_turns=2, decay_seconds=120.0)
    buf2.record("should I start investing", "finance", now=t0)
    buf2.record("open task manager", None, now=t0 + 5)
    buf2.record("what's the weather", None, now=t0 + 10)
    print(f"turns held: {len(buf2._turns)} (max_turns=2, oldest finance turn evicted)")
    print(f"boost_for('finance') = {buf2.boost_for('finance', now=t0 + 15):.3f} (0.0 -- evicted)")
