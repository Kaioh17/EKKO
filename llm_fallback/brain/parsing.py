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
    # Archive index entries the model wants fetched (memory/archive.py).
    recall: tuple[str, ...] = ()

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


# The first spoken chunk may also break at a clause mark once it is this
# long, so audio starts before the first full sentence has arrived. Raise it
# if the opening sounds choppy, lower it for a quicker start.
_FIRST_CLAUSE_MIN_CHARS = 40
_SENTENCE_END = re.compile(r"""[.!?]+["')\]]*\s+""")
_CLAUSE_END = re.compile(r"[,;:]\s+")
_ANSWER_OPEN = re.compile(r'"answer"\s*:\s*(\S)')
_INTENT_NULL = re.compile(r'"intent"\s*:\s*null')


class AnswerStream:
    """Pulls speakable chunks of `answer` out of a reply while it is still
    streaming in. feed() takes the next raw text delta and returns the
    chunks that became complete; `spoken` is everything returned so far.

    Nothing is returned unless `"intent": null` came before `answer`, so a
    command pick is never spoken early. A sentence ending in "?" is held
    until more text follows it, since parse_result() drops a trailing
    question when `follow_up` is set; the caller speaks whatever is left
    with remaining_answer() once the full reply is parsed.
    """

    def __init__(self):
        self._buf = ""
        self._start: int | None = None  # index just past the answer's opening quote
        self._dead = False
        self.spoken = ""

    def _decoded(self) -> tuple[str, bool]:
        """(answer text so far, whether its closing quote has arrived)."""
        buf, i, closed = self._buf, self._start, False
        while i < len(buf):
            if buf[i] == '"':
                closed = True
                break
            if buf[i] == "\\":
                step = 6 if buf[i + 1 : i + 2] == "u" else 2
                if i + step > len(buf):
                    break  # escape still arriving
                i += step
            else:
                i += 1
        try:
            return json.loads(f'"{buf[self._start:i]}"', strict=False), closed
        except json.JSONDecodeError:
            self._dead = True
            return "", False

    def feed(self, delta: str) -> list[str]:
        if self._dead:
            return []
        self._buf += delta
        if self._start is None:
            match = _ANSWER_OPEN.search(self._buf)
            if match is None:
                return []
            if match.group(1) != '"' or not _INTENT_NULL.search(self._buf[: match.start()]):
                self._dead = True
                return []
            self._start = match.end()

        text, closed = self._decoded()
        chunks: list[str] = []
        pos = len(self.spoken)
        for match in _SENTENCE_END.finditer(text, pos):
            if text[pos : match.end()].rstrip().endswith("?") and match.end() == len(text):
                break  # trailing question so far, hold it
            chunks.append(text[pos : match.end()])
            pos = match.end()
        if not self.spoken and not chunks:
            clause = _CLAUSE_END.search(text, _FIRST_CLAUSE_MIN_CHARS)
            if clause:
                chunks.append(text[: clause.end()])
                pos = clause.end()
        if closed:
            self._dead = True
            if text[pos:].strip() and not text.rstrip().endswith("?"):
                chunks.append(text[pos:])
                pos = len(text)
        self.spoken = text[:pos]
        return chunks


def remaining_answer(answer: str | None, spoken: str) -> tuple[str, bool]:
    """(what is still left to say, mismatch) once the final answer is known.
    Mismatch means the final answer isn't a continuation of what was already
    spoken (another provider answered after a mid-stream failure), so the
    whole answer is returned.
    """
    answer, spoken = (answer or "").strip(), spoken.strip()
    if answer.startswith(spoken):
        return answer[len(spoken):].strip(), False
    if spoken.startswith(answer):
        return "", False  # a trailing question was spoken before it was stripped
    return answer, True


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

    raw_recall = parsed.get("recall")
    recall = tuple(r.strip() for r in raw_recall if isinstance(r, str) and r.strip())[:3] if isinstance(raw_recall, list) else ()
    if intent is not None or answer is not None:
        recall = ()  # a real pick/answer wins; recall is only a "need more first"

    reason = str(parsed.get("reason", ""))
    return ParsedResult(
        intent=intent,
        slots={str(k): str(v) for k, v in slots.items()},
        answer=(answer or None),
        urls=urls,
        follow_up=(follow_up or None),
        memory_candidate=memory_candidate,
        reason=reason,
        recall=recall,
    )
