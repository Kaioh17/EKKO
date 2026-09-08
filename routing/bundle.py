"""The output contract between intent routing and command execution.

Every routing decision produces exactly one IntentBundle, and the
execution layer accepts nothing else, never a raw transcript. That's the
security boundary: a bundle can only name a handler that was already in
the closed intent set, and can only carry slot values that were already
in that intent's declared vocabulary, so by the time execute.py sees it
there is no user-controlled text left to validate.

The class is built to make an invalid bundle awkward to produce by
accident rather than merely discouraged: it's frozen, its slot mapping is
read-only, the four statuses are an enum rather than loose strings, and
__post_init__ asserts the cross-field invariants (a handler exists only
on a MATCHED bundle, and so on). Construct them through the four
classmethods below rather than calling IntentBundle(...) directly.
"""

import types
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class RoutingStatus(StrEnum):
    """Four outcomes, deliberately distinguished rather than collapsed
    into match/no-match: the response layer says something different for
    each, and the JSONL log needs them separable for later threshold
    calibration (see route.log_decision).
    """

    MATCHED = "matched"
    NO_MATCH = "no_match"
    # Intent matched, but a slot it declares couldn't be filled from the
    # transcript. Distinct from NO_MATCH on purpose: "open" alone is not
    # the same failure as "what's the weather", and answering "which app?"
    # is only possible if the two are told apart.
    MISSING_SLOT = "missing_slot"
    # Whisper returns "" for a segment that turned out to be noise rather
    # than speech. Nothing was said, so nothing was misunderstood either.
    EMPTY_TRANSCRIPT = "empty_transcript"


@dataclass(frozen=True)
class IntentBundle:
    status: RoutingStatus
    # The raw transcript, exactly as Whisper produced it. Kept for the
    # log, which is both the calibration record and the labelled dataset
    # a Tier 2 classifier would train on (see intent_routing.md).
    transcript: str
    # Best cosine similarity seen, populated on every status including
    # NO_MATCH, where the near-miss score is the interesting part.
    score: float
    # Which example phrasing won. Not used for any decision, but it's the
    # difference between "scored 0.61" and "scored 0.61 against 'show
    # running processes'" when a mis-route needs explaining.
    matched_example: str = ""
    intent: str | None = None
    handler: str | None = None
    slots: Mapping[str, str] = field(default_factory=dict)
    # Which declared slot couldn't be filled, set only on MISSING_SLOT.
    missing_slot: str | None = None
    # Which input channel produced this transcript. "voice" (the only
    # value that existed before chatbot/chat_listener.py) or "chat" --
    # purely a log/prompt-context tag, never a routing input, so it
    # defaults to today's only value everywhere it's omitted.
    source: str = "voice"

    def __post_init__(self) -> None:
        # A plain dict field would leave a frozen dataclass mutable
        # through the back door (bundle.slots["app"] = ...), which is
        # exactly the mutation that matters here, it's what reaches the
        # command line. Wrap it once, at construction.
        object.__setattr__(self, "slots", types.MappingProxyType(dict(self.slots)))

        matched = self.status is RoutingStatus.MATCHED
        if matched != (self.handler is not None):
            raise ValueError(
                f"handler must be set if and only if status is MATCHED "
                f"(status={self.status}, handler={self.handler!r})"
            )
        if self.slots and not matched:
            # The converse isn't an error: an intent that declares no
            # slots produces a MATCHED bundle with an empty mapping.
            raise ValueError(f"{self.status} bundle must not carry slots: {dict(self.slots)!r}")
        names_intent = matched or self.status is RoutingStatus.MISSING_SLOT
        if names_intent != (self.intent is not None):
            raise ValueError(
                f"intent must be set if and only if status is MATCHED or "
                f"MISSING_SLOT (status={self.status}, intent={self.intent!r})"
            )
        if (self.status is RoutingStatus.MISSING_SLOT) != (self.missing_slot is not None):
            raise ValueError(
                f"missing_slot must be set if and only if status is "
                f"MISSING_SLOT (status={self.status}, missing_slot={self.missing_slot!r})"
            )
        if not -1.0 <= self.score <= 1.0:
            raise ValueError(f"score must be a cosine similarity in [-1, 1], got {self.score}")

    @classmethod
    def matched(
        cls,
        transcript: str,
        intent: str,
        score: float,
        matched_example: str,
        handler: str,
        slots: Mapping[str, str] | None = None,
        source: str = "voice",
    ) -> "IntentBundle":
        return cls(
            status=RoutingStatus.MATCHED,
            transcript=transcript,
            score=score,
            matched_example=matched_example,
            intent=intent,
            handler=handler,
            slots=slots or {},
            source=source,
        )

    @classmethod
    def no_match(
        cls, transcript: str, score: float, matched_example: str = "", source: str = "voice"
    ) -> "IntentBundle":
        return cls(
            status=RoutingStatus.NO_MATCH,
            transcript=transcript,
            score=score,
            matched_example=matched_example,
            source=source,
        )

    @classmethod
    def slot_missing(
        # Not named missing_slot: a classmethod sharing a field's name
        # becomes that field's default value, silently, since a dataclass
        # reads defaults off the class body.
        cls,
        transcript: str,
        intent: str,
        score: float,
        matched_example: str,
        slot_name: str,
        source: str = "voice",
    ) -> "IntentBundle":
        return cls(
            status=RoutingStatus.MISSING_SLOT,
            transcript=transcript,
            score=score,
            matched_example=matched_example,
            intent=intent,
            missing_slot=slot_name,
            source=source,
        )

    @classmethod
    def empty_transcript(cls, transcript: str = "", source: str = "voice") -> "IntentBundle":
        return cls(
            status=RoutingStatus.EMPTY_TRANSCRIPT,
            transcript=transcript,
            score=0.0,
            source=source,
        )

    def as_log_entry(self) -> dict:
        """Flat JSON-safe dict for the JSONL log. Written by hand rather
        than dataclasses.asdict() because slots is a MappingProxyType,
        and because the log's shape is a format other tooling reads, it
        shouldn't silently change when a field is added here.
        """
        return {
            "transcript": self.transcript,
            "status": str(self.status),
            "intent": self.intent,
            "score": round(self.score, 4),
            "matched_example": self.matched_example,
            "slots": dict(self.slots),
            "missing_slot": self.missing_slot,
            "source": self.source,
        }

    def describe(self) -> str:
        """One-line human summary, the shape verify.py prints."""
        if self.status is RoutingStatus.MATCHED:
            slots = f"  slots: {dict(self.slots)}" if self.slots else ""
            return f"MATCHED {self.intent}  (score: {self.score:.3f}){slots}"
        if self.status is RoutingStatus.MISSING_SLOT:
            return (
                f"MISSING SLOT {self.missing_slot!r} for {self.intent}  "
                f"(score: {self.score:.3f})"
            )
        if self.status is RoutingStatus.EMPTY_TRANSCRIPT:
            return "EMPTY TRANSCRIPT (nothing was said)"
        return f"NO MATCH  (best score: {self.score:.3f})"
