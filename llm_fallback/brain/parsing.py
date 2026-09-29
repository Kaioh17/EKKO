"""Parses a provider's raw text reply into a typed ParsedResult.

Operates on plain text (already extracted from whatever envelope shape a
provider's API/CLI/local-server returns -- Gemini's `candidates[...]`,
Claude's `--output-format json` `result` field, Ollama's `response` field)
so this parsing logic is identical regardless of which provider produced
the text.
"""

import json
import re
from dataclasses import dataclass

from memory.schema import MemoryCandidate

_MAX_URLS = 3


def strip_markdown_fence(text: str) -> str:
    """`responseMimeType: application/json` (or the local-model equivalent)
    doesn't guarantee an unfenced response in practice -- observed in
    manual testing across every provider. Strips a single leading/trailing
    triple-backtick fence, with or without a language tag; a no-op if the
    text wasn't fenced.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class ParsedResult:
    """What one provider reply deserializes into, before validate_pick()'s
    re-check."""

    intent: str | None
    slots: dict[str, str]
    answer: str | None
    urls: list[str]
    follow_up: str | None
    memory_candidate: MemoryCandidate | None
    reason: str

    @classmethod
    def empty(cls, reason: str = "", answer: str | None = None) -> "ParsedResult":
        """No pick, no slots, and (unless `answer` is given) nothing to say."""
        return cls(None, {}, answer, [], None, None, reason)


def _parse_memory_candidate(raw: object) -> MemoryCandidate | None:
    """Type-checks the memory_candidate field into a MemoryCandidate --
    still untrusted content, no scoring happens here. Any shape mismatch
    (missing text, non-string fields, category outside the known set)
    degrades to None rather than raising -- same bug-tolerant posture as
    every other field in this function; memory/scoring.py's
    propose_and_score() is the actual gatekeeper, not this parser.
    """
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    category = raw.get("category")
    confidence = raw.get("confidence")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(category, str):
        return None
    if not isinstance(confidence, str):
        confidence = "inferred"
    return MemoryCandidate(text=text.strip(), category=category.strip().lower(), confidence=confidence.strip().lower())


def _strip_trailing_question(answer: str) -> str:
    """Drop a trailing question sentence from `answer` when a separate
    `follow_up` is being spoken too. Models sometimes end `answer` with
    "Would you like ...?" and also fill `follow_up` with the same ask, and
    the listener speaks both back to back. The contract forbids it, this
    enforces it. Never empties the answer: if the whole answer is a
    question, it is left as is.
    """
    sentences = re.findall(r"[^.!?]+[.!?]+(?:\s+|$)|[^.!?]+$", answer.strip())
    while len(sentences) > 1 and sentences[-1].strip().endswith("?"):
        sentences.pop()
    return "".join(sentences).strip()


def parse_result(text: str) -> ParsedResult:
    """A blocked/empty response (empty text) is treated the same as any
    other unparseable result -- intent=answer=None, urls=[] -- not a crash,
    same bug-tolerant shape every provider needs for its own malformed-
    output handling.
    """
    result_text = strip_markdown_fence(text)
    try:
        parsed = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return ParsedResult.empty(f"unparseable response: {result_text!r}")

    if not isinstance(parsed, dict):
        return ParsedResult.empty(f"response wasn't a JSON object: {parsed!r}")

    intent = parsed.get("intent")
    if intent is not None and not isinstance(intent, str):
        intent = None
    if intent is not None and intent.strip().lower() in ("null", "none"):
        # A model emitting the JSON string "null" instead of the literal is
        # harmless for safety (validate_pick() would reject it as an
        # unknown intent name regardless) but worth normalising rather than
        # relying on no config ever legitimately being named "null".
        intent = None
    slots = parsed.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    answer = parsed.get("answer") if intent is None else None
    if answer is not None and not isinstance(answer, str):
        answer = None

    urls: list[str] = []
    if answer is not None:
        raw_urls = parsed.get("urls") or []
        urls = [u for u in raw_urls if isinstance(u, str)][:_MAX_URLS]

    # follow_up only makes sense grounded in a real spoken answer -- if
    # there's no answer (a command pick, a decline, noise), there's
    # nothing for it to be grounded in, so it's discarded rather than
    # trusted regardless of what the model put there.
    follow_up = parsed.get("follow_up") if answer is not None else None
    if follow_up is not None and not isinstance(follow_up, str):
        follow_up = None
    if follow_up is not None and follow_up.strip().lower() in ("null", "none", ""):
        follow_up = None

    if answer is not None and follow_up:
        answer = _strip_trailing_question(answer)

    memory_candidate = _parse_memory_candidate(parsed.get("memory_candidate"))

    reason = str(parsed.get("reason", ""))
    return ParsedResult(
        intent=intent,
        slots={str(k): str(v) for k, v in slots.items()},
        answer=(answer or None),
        urls=urls,
        follow_up=(follow_up or None),
        memory_candidate=memory_candidate,
        reason=reason,
    )
