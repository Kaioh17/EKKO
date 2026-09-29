"""FallbackOutcome -- the one result shape every provider produces: every
field a caller (listener/vad_listener.py, chatbot/chat_listener.py) might
read is always present, populated or left at its default by whichever
provider answered. Provider-specific accounting that doesn't generalize
(Claude's session_id/total_cost_usd, Ollama's total_duration_ns) lives in
`extras`.
"""

from dataclasses import dataclass, field

from routing.bundle import IntentBundle

from memory.schema import MemoryCandidate


@dataclass(frozen=True)
class FallbackOutcome:
    """Never raises past attempt_fallback(): any failure (missing key,
    timeout, unreachable provider, malformed output, a pick that doesn't
    validate) comes back as an outcome with bundle=answer=None, which the
    caller treats exactly like a plain NO_MATCH.
    """

    bundle: IntentBundle | None
    answer: str | None
    urls: list[str]
    raw_intent: str | None
    raw_slots: dict[str, str]
    reason: str
    provider: str
    model: str
    usage: dict
    latency_seconds: float | None
    error: str | None
    # Grounded next-step, from the same call/answer -- null on almost every
    # turn. See BASE_CONTRACT.md's follow_up rules and domains/*/guardrails.md
    # for what a domain may further restrict it from proposing.
    follow_up: str | None = None
    # Untrusted candidate for memory/scoring.py's propose_and_score() to
    # judge -- populating this field here does NOT write anything; see
    # memory/README.md for the accept/reject step that sits between this
    # and memory/store.write_memory().
    memory_candidate: MemoryCandidate | None = None
    # Which domains/registry.yaml key (if any) supplied the
    # system_instruction for this call -- None on the flat/no-domain path.
    domain: str | None = None
    # Which input channel this call came from -- "voice" (default) or
    # "chat". Log tag only, see brain/prompt.py's `source` param for the
    # one place it actually changes what's sent to the model.
    source: str = "voice"
    # Provider-specific accounting that doesn't generalize across every
    # provider (Claude's session_id/total_cost_usd, Ollama's
    # total_duration_ns) -- optional, never read by shared code, only by
    # that provider's own CLI/log formatting.
    extras: dict = field(default_factory=dict)

    @property
    def validated(self) -> bool:
        return self.bundle is not None
