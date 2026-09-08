"""Full-feature twin of `llm_fallback/claude_code/fallback.py`, on the Gemini
API's free tier instead of a `claude -p` subprocess. Same job, same one-call
merged schema, same safety contract — see that module's docstring for the
reasoning this one doesn't repeat.

Migrated here, not reimplemented from scratch where avoidable:
  - `validate_pick()`, `_intents_for_prompt()`, `_strip_markdown_fence()`,
    `open_research_tabs()`, `_MAX_URLS`, and the research-tabs constants are
    imported straight from `claude_code.fallback` — there is exactly one
    place that decides what a model's JSON output is allowed to mean, and
    exactly one script that opens a browser tab, regardless of which model
    proposed either. See `validate_pick()`'s docstring there for why an
    intent name is never trusted without re-checking it against a fresh
    `routing/intents.yaml` load.
  - `build_prompt()`, `parse_result()`, logging, and usage tracking are
    reimplemented, not imported: the request/response envelope shape is
    Gemini's, not `claude -p`'s JSON envelope, and Gemini has no
    `total_cost_usd` (free tier) to track.

What's genuinely different from the cloud (Claude) version:
  - Talks directly to Gemini's REST API (`generativelanguage.googleapis.com`)
    over `urllib.request` (stdlib only, same reasoning `ollama/
    fallback_ollama.py` gives for not adding a dependency for one caller) --
    no CLI process to cold-start, which is the latency source
    `llm_fallback/README.md` and `claude_code/fallback.py`'s own usage log
    point at (`logs/fallback.jsonl`: 15% of calls hit the 20s timeout, and
    91% of every call's output tokens were unused extended-thinking tokens
    Haiku spent reasoning toward a one-line JSON object). `thinkingConfig`
    below asks Gemini to skip that entirely for this call.
  - Free: `usage_summary.json` here tracks tokens and latency, not dollars.
    Gemini's free tier (no billing account attached) does **not** carry
    Anthropic's no-training-on-API-data-by-default guarantee -- inputs may
    be used to improve Google's products. That's a real change from what
    `readme.md` and `claude_code/fallback.py`'s own module docstring say
    about this data ("never leaves this machine except as the call itself"
    was true for the Claude path specifically). Worth knowing before this
    is the thing that hears everything that fails the deterministic
    matcher. See README.md's Privacy section.
  - `google_search`, Gemini's tool for live/current information and the
    equivalent of the cloud version's `WebSearch`, is OFF by default
    (`ENABLE_SEARCH = False`) -- confirmed against the live API that this
    tool has zero quota on a pure free-tier (no-billing) key, a plain
    `generateContent` call succeeds and the identical call with the tool
    attached gets an immediate 429, even as the first call of a session.
    Pass `--search` (or `enable_search=True`) once a billing account is
    linked, and re-verify quota first. Structured output
    (`responseMimeType: application/json`) is requested but not enforced
    via a strict `responseSchema`, tool-attached or not -- forcing a JSON
    schema and combining it with search grounding is unreliable across
    Gemini model versions as of this writing, so this leans on the
    prompt's explicit schema instruction plus `_strip_markdown_fence()` /
    `parse_result()`'s tolerant parsing, same belt-and-suspenders approach
    the cloud version already needs for Haiku's occasional markdown fence.

LIVE: `listener/vad_listener.py` imports `attempt_fallback()` from this
module directly and calls it whenever the deterministic matcher returns
NO_MATCH -- this is the fallback path that's actually wired in today, not
a prototype (that status applied before 2026-08-22; corrected here since
this file's own docstring was the only place still saying otherwise).
`llm_fallback/claude_code/` remains as a rollback reference and
`llm_fallback/ollama/` as an unwired prototype -- see
`llm_fallback/gemini/README.md`.

Domain-aware system prompts (`domains/loader.py`) and memory-context
injection (`memory/`) both hook into this module via optional parameters
on `attempt_fallback()`/`run_gemini()`/`build_prompt()` -- see their own
docstrings below. Neither changes this module's core contract: exactly
one Gemini call per fallback attempt, same never-raises guarantee.

Setup:
    Get a free API key at https://aistudio.google.com/apikey (no billing
    account required for the free tier) and set it as an environment
    variable before running anything here:

        setx GEMINI_API_KEY "<your key>"        (persists, new shells only)
        $env:GEMINI_API_KEY = "<your key>"       (current PowerShell session)

Usage:
    python llm_fallback/gemini/fallback_gemini.py "open the task thing"
    python llm_fallback/gemini/fallback_gemini.py "tell me a joke"
    python llm_fallback/gemini/fallback_gemini.py --dry-run "open the task thing"
    python llm_fallback/gemini/fallback_gemini.py --usage
"""

import argparse
import datetime
import json
import os
import sys
import time
import dataclasses
from dataclasses import dataclass
from pathlib import Path

# routing/ and llm_fallback/claude_code/ are siblings two and one
# directories up from here respectively -- same sys.path pattern
# claude_code/fallback.py and ollama/fallback_ollama.py both use, for the
# same reason: resolve regardless of invocation cwd.
GEMINI_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = GEMINI_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from routing.bundle import IntentBundle  # noqa: E402
from routing.config import DEFAULT_CONFIG_PATH, IntentConfig, load_config  # noqa: E402

# Typed container for an optional memory_candidate field, see
# parse_result()/FallbackOutcome below -- schema only, no I/O, so this
# import doesn't pull in memory/store.py or memory/scoring.py here.
# ShortMemoryResponse is the same kind of hook -- a type only, build_prompt()
# just reads its fields, the actual read/write of short_term.json happens
# in listener/vad_listener.py, not here (same split as memory_context,
# see build_prompt()'s docstring).
from memory.schema import MemoryCandidate, ShortMemoryResponse  # noqa: E402

# Reused, not reimplemented -- see module docstring.
from llm_fallback.claude_code.fallback import (  # noqa: E402
    DEFAULT_TABS_TIMEOUT,
    _intents_for_prompt,
    _MAX_URLS,
    open_research_tabs,
    validate_pick,
)
from llm_fallback.gemini.client import (  # noqa: E402
    API_BASE,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    GeminiError,
    extract_text,
    generate_content,
)

DEFAULT_LOG_PATH = GEMINI_DIR / "logs" / "fallback.jsonl"
DEFAULT_USAGE_SUMMARY_PATH = GEMINI_DIR / "logs" / "usage_summary.json"
SYSTEM_PROMPT_PATH = GEMINI_DIR / "SYSTEM_PROMPT.md"

# Alias, not a new type: everything below (attempt_fallback()'s except
# clause, this module's own docstrings) was written against this name
# before client.py existed to hold the actual transport error. Kept as an
# alias rather than a rename so a caller `except`ing GeminiFallbackError
# still works unchanged -- it's the exact same class object as
# client.GeminiError, not a wrapper.
GeminiFallbackError = GeminiError

# Off by default: confirmed against the live API on 2026-08-20 that the
# `google_search` grounding tool has ZERO quota on a pure free-tier
# (no-billing) key/project -- a plain generateContent call succeeds, the
# identical call with `tools: [{"google_search": {}}]` attached gets an
# immediate 429 RESOURCE_EXHAUSTED, even as the very first call of the
# session. This is not a rate limit that recovers on retry, it's the free
# tier not including grounding at all. Rather than silently degrade every
# open-ended answer's quality (no live/current-info capability) or retry
# without the tool and hide that from the caller, this stays an explicit
# opt-in -- flip to True (or pass --search) once a billing account is
# linked to the Google Cloud project behind GEMINI_API_KEY, and re-verify
# quota at https://ai.google.dev/gemini-api/docs/rate-limits first.
ENABLE_SEARCH = False

_SUMMED_USAGE_FIELDS = (
    "promptTokenCount",
    "candidatesTokenCount",
    "thoughtsTokenCount",
    "totalTokenCount",
    "cachedContentTokenCount",
)


@dataclass(frozen=True)
class FallbackOutcome:
    """Same shape as claude_code.fallback.FallbackOutcome, minus
    total_cost_usd (nothing here has a dollar cost) plus latency_seconds,
    this module's equivalent of "what did this actually cost in time,"
    which is the number worth comparing against the cloud version's
    wall-clock/timeout behavior.
    """

    bundle: IntentBundle | None
    answer: str | None
    urls: list[str]
    raw_intent: str | None
    raw_slots: dict[str, str]
    reason: str
    model: str
    usage: dict
    latency_seconds: float | None
    error: str | None
    # Grounded next-step, from the same call/answer -- null on almost every
    # turn. See SYSTEM_PROMPT.md's follow_up rules and domains/*/guardrails.md
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
    # Which input channel this call came from -- "voice" (default,
    # reproduces every call site that predates chatbot/chat_listener.py)
    # or "chat". Log tag only, see build_prompt()'s `source` param for the
    # one place it actually changes what's sent to Gemini.
    source: str = "voice"

    @property
    def validated(self) -> bool:
        return self.bundle is not None


def build_prompt(
    transcript: str,
    config: IntentConfig,
    enable_search: bool = ENABLE_SEARCH,
    memory_context: str | None = None,
    short_memory: ShortMemoryResponse | None = None,
    source: str = "voice",
) -> str:
    """Same payload shape and schema as claude_code.fallback.build_prompt()
    -- the two are meant to be directly comparable, same transcript in, same
    intent list in, same schema demanded back. Not imported directly since
    the two modules' wording is allowed to drift independently (e.g. if one
    provider needs a nudge the other doesn't), but starts identical.

    The search-specific sentence is only included when `enable_search` is
    True (and the `google_search` tool is actually attached in
    run_gemini()) -- telling the model it can search when the tool isn't in
    this request would just produce a confident answer/`urls` claiming
    research that never happened. See ENABLE_SEARCH's comment for why it's
    off by default.

    memory_context, when given (memory.schema.MemoryBundle.as_prompt_block()'s
    output), is per-call payload rather than baked into system_instruction:
    it changes every call as MEMORY.md grows, whereas a domain's guardrails/
    persona/knowledge are stable across calls -- keeping the two separate
    means whatever prompt-caching behavior Gemini does for a repeated
    system_instruction isn't invalidated by memory content changing between
    calls.

    short_memory, when given, is the single prior llm_fallback turn from
    *this same session* (memory.short_term.read_short_memory()'s output) --
    the question EKKO was just asked, what it answered, and the follow_up
    it spoke. It's a different kind of context than `memory_context`: not a
    durable fact worth carrying across sessions, just what makes the
    *current* transcript legible as a reply rather than a fresh,
    standalone question. Passed as-is regardless of whether its
    follow_up_answer field happens to be filled in yet (see
    listener/vad_listener.py's call site) -- what matters here is
    prior_question/prior_answer/follow_up, the same three fields either
    way. None on the large majority of calls: only present when a session
    is active and a prior turn actually left a follow_up open.

    source ("voice", the default, or "chat") changes the wording of the
    prompt's framing sentence and its `answer` instruction -- a voice
    turn's answer is read aloud by TTS (one or two spoken sentences, no
    markdown), a chat turn's is displayed as text instead, which can
    afford to be a little longer and needn't avoid markdown. Nothing
    else about the schema or the rest of this function changes.
    """
    payload = {
        "transcript": transcript,
        "status": "not_understood",
        "intents": _intents_for_prompt(config),
    }
    if memory_context:
        payload["memory"] = memory_context
    if short_memory is not None:
        payload["short_memory"] = {
            "prior_question": short_memory.prior_question,
            "prior_answer": short_memory.prior_answer,
            "follow_up": short_memory.follow_up,
        }
    search_note = (
        " It's fine to use Google Search for a factual or current-events "
        "question. `urls` is 0-3 real search result URLs worth reading "
        "further, only when answer is non-null and you actually searched "
        "for it -- never invented, [] otherwise."
        if enable_search
        else " You have no search tool for this call -- answer only from "
        "your own knowledge, and say so plainly rather than guessing if a "
        "question needs live/current information you don't have. Always "
        "leave `urls` as []."
    )
    # Only present when the caller actually found a pending turn (see
    # short_memory's docstring above) -- most calls get no such sentence at
    # all, same conditional-note pattern search_note already uses so a
    # capability that isn't there this call is never implied.
    short_memory_note = (
        " The `short_memory` field, when present, is the immediately "
        "preceding turn in this same session -- the transcript you were "
        "just asked, what you answered, and the follow_up you spoke. If "
        "this transcript reads as a reply to that follow_up (e.g. \"yes\", "
        "\"the second one\", \"tell me more\", a short answer that only "
        "makes sense in light of it) rather than a new, standalone "
        "question, answer it in that context instead of treating it as "
        "unrelated."
        if short_memory is not None
        else ""
    )
    channel_note = (
        "A voice command" if source == "voice" else "A typed chat message"
    )
    answer_note = (
        "must be plain text meant to be read aloud by "
        "text-to-speech -- one or two sentences, no markdown, no lists."
        if source == "voice"
        else "is displayed as text, not spoken -- normal prose, markdown is fine."
    )
    return (
        f"{channel_note} didn't match anything in the closed intent set below "
        "(status: not_understood). First, decide whether it plausibly means "
        "one of these intents anyway, e.g. due to mishearing or paraphrase. "
        "If it doesn't, decide whether it's instead a genuine open-ended "
        "question or request you can just answer directly.\n\n"
        f"{json.dumps(payload)}\n\n"
        "Respond with ONLY this JSON object, no prose, no markdown fence: "
        '{"intent": "<name_from_the_list_above_or_null>", "slots": {}, '
        '"answer": "<spoken_text_or_null>", "urls": [], '
        '"follow_up": "<grounded_next_step_or_null>", '
        '"memory_candidate": {"text": "...", "category": "preference|correction|fact|project|event", '
        '"confidence": "explicit_request|stated_preference|inferred"} or null, '
        '"reason": "<short>"}\n'
        f"Exactly one of intent/answer may be non-null, never both. `answer`, "
        f"when used, {answer_note} "
        "`follow_up` must be grounded in this same `answer` -- a specific "
        "next step based on what you just said, never a generic "
        "\"anything else?\" -- and null whenever `answer` is null. "
        "`memory_candidate` stays null on almost every call; only populate "
        "it when the transcript contains something worth remembering across "
        "sessions (an explicit request to remember, a stated preference, a "
        "correction, or a fact tied to an ongoing project), never for a "
        "fact merely mentioned in passing."
        f"{search_note}"
        f"{short_memory_note}"
    )


def run_gemini(
    prompt: str,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
    enable_search: bool = ENABLE_SEARCH,
    system_instruction_override: str | None = None,
) -> dict:
    """Thin wrapper over client.generate_content(), fixing the
    intent-routing-specific choices: `system_instruction` carries
    SYSTEM_PROMPT.md, the same "only project context this call gets" role
    claude_code/CLAUDE.md plays for the cloud version. `google_search`, when
    `enable_search` is True, is the tool grant, the equivalent of the cloud
    version's `--allowedTools "WebSearch"` -- and, same as that flag, the
    *only* tool ever given; nothing here can read a file, run a shell
    command, or execute code. Off by default -- see ENABLE_SEARCH's comment
    for why (zero free-tier quota, confirmed against the live API).

    system_instruction_override, when given (domains/loader.py's
    compose_system_instruction() output), replaces the flat SYSTEM_PROMPT.md
    read for this one call -- the domain-detection hook point. The
    composed prompt still ends with the full SYSTEM_PROMPT.md contract
    appended verbatim (see loader.py), so this is additive, never a
    reduction of the base contract.

    Raises GeminiFallbackError (an alias of client.GeminiError, see above)
    on any transport failure -- unchanged from before this was split out,
    attempt_fallback()'s `except GeminiFallbackError` still catches it.
    """
    system_prompt = system_instruction_override or SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return generate_content(
        prompt,
        system_instruction=system_prompt,
        tools=[{"google_search": {}}] if enable_search else None,
        model=model,
        timeout=timeout,
    )


def _grounding_urls(candidate: dict) -> list[str]:
    """Pulls real, already-fetched result URLs out of
    `groundingMetadata.groundingChunks[].web.uri` -- the Google Search
    grounding tool's equivalent of the cloud version relying on the model to
    echo back WebSearch result URLs itself. Preferring these (when present)
    over whatever the model wrote into its own `urls` field is the same
    "don't trust the model's own claim, use what the tool actually
    returned" reasoning validate_pick() applies to an intent pick -- these
    are real URLs the grounding step actually retrieved, not the model's
    recollection of them.
    """
    chunks = candidate.get("groundingMetadata", {}).get("groundingChunks", [])
    urls = []
    for chunk in chunks:
        uri = chunk.get("web", {}).get("uri")
        if isinstance(uri, str):
            urls.append(uri)
    return urls[:_MAX_URLS]


@dataclass(frozen=True)
class ParsedResult:
    """What one Gemini reply deserializes into, before validate_pick()'s
    re-check. Was a 5-tuple before follow_up/memory_candidate were added;
    a dataclass reads better than a growing positional tuple at this
    field count, and it's harder to accidentally swap two same-typed
    fields at a call site."""

    intent: str | None
    slots: dict[str, str]
    answer: str | None
    urls: list[str]
    follow_up: str | None
    memory_candidate: MemoryCandidate | None
    reason: str


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


def parse_result(envelope: dict) -> ParsedResult:
    """Same contract as claude_code.fallback.parse_result(), applied to
    Gemini's `candidates[0].content.parts[*].text` instead of the `claude
    -p` envelope's `result` field. Text can arrive split across multiple
    parts (observed when a tool call happens mid-response) -- concatenated
    here before parsing, same reasoning as any other place that assembles a
    model's full text output before treating it as one JSON blob.

    A blocked/empty response (`candidates` missing, or a
    `promptFeedback.blockReason`) is treated the same as any other
    unparseable result -- intent=answer=None, urls=[] -- not a crash, same
    bug-tolerant shape as the cloud version's malformed-output handling.
    """
    empty = lambda reason: ParsedResult(None, {}, None, [], None, None, reason)  # noqa: E731

    candidates = envelope.get("candidates") or []
    if not candidates:
        block_reason = envelope.get("promptFeedback", {}).get("blockReason")
        return empty(f"no candidates returned (blockReason={block_reason!r})")

    candidate = candidates[0]
    result_text = extract_text(envelope)

    try:
        parsed = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return empty(f"unparseable response: {result_text!r}")

    if not isinstance(parsed, dict):
        return empty(f"response wasn't a JSON object: {parsed!r}")

    intent = parsed.get("intent")
    if intent is not None and not isinstance(intent, str):
        intent = None
    if intent is not None and intent.strip().lower() in ("null", "none"):
        # Same normalisation ollama/fallback_ollama.py applies: a model
        # emitting the JSON string "null" instead of the literal is
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
        grounded = _grounding_urls(candidate)
        if grounded:
            urls = grounded
        else:
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


def log_call(outcome: FallbackOutcome, transcript: str, log_path: str | Path = DEFAULT_LOG_PATH) -> None:
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "transcript": transcript,
        "model": outcome.model,
        "usage": outcome.usage,
        "latency_seconds": outcome.latency_seconds,
        "raw_intent": outcome.raw_intent,
        "raw_slots": outcome.raw_slots,
        "answer": outcome.answer,
        "urls": outcome.urls,
        "follow_up": outcome.follow_up,
        # Text intentionally omitted from the log -- only whether a
        # candidate was proposed and what category, enough to tune
        # scoring.py's thresholds later without duplicating personal
        # content into a second file.
        "memory_candidate_category": outcome.memory_candidate.category if outcome.memory_candidate else None,
        "domain": outcome.domain,
        "source": outcome.source,
        "reason": outcome.reason,
        "validated": outcome.validated,
        "final_intent": outcome.bundle.intent if outcome.bundle else None,
        "error": outcome.error,
    }
    log_path = Path(log_path)
    os.makedirs(log_path.parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    update_usage_summary(
        outcome.usage,
        outcome.latency_seconds,
        entry["timestamp"],
        summary_path=log_path.parent / DEFAULT_USAGE_SUMMARY_PATH.name,
    )


def _empty_usage_summary() -> dict:
    summary = {
        "total_calls": 0,
        "total_latency_seconds": 0.0,
        "first_call": None,
        "last_call": None,
    }
    for field in _SUMMED_USAGE_FIELDS:
        summary[f"total_{field}"] = 0
    return summary


def load_usage_summary(summary_path: str | Path = DEFAULT_USAGE_SUMMARY_PATH) -> dict:
    """The running total, or a fresh zeroed one if the file doesn't exist
    yet or somehow got corrupted -- a summary file that can't be read is a
    reason to start counting again, not a reason to crash the caller, same
    as both other fallback directories' identical function.
    """
    summary_path = Path(summary_path)
    if not summary_path.exists():
        return _empty_usage_summary()
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_usage_summary()


def update_usage_summary(
    usage: dict,
    latency_seconds: float | None,
    timestamp: str,
    summary_path: str | Path = DEFAULT_USAGE_SUMMARY_PATH,
) -> dict:
    """Accumulates one call's usage/latency into the running total, called
    from log_call() for every call regardless of outcome -- a timed-out or
    errored call still measured real latency and, if it got a response back
    at all, spent real tokens, same "count it even if it wasn't usable"
    reasoning claude_code/fallback.py's update_usage_summary() gives.
    """
    summary_path = Path(summary_path)
    summary = load_usage_summary(summary_path)
    summary["total_calls"] += 1
    summary["total_latency_seconds"] = round(
        summary.get("total_latency_seconds", 0.0) + (latency_seconds or 0.0), 3
    )
    for field in _SUMMED_USAGE_FIELDS:
        summary[f"total_{field}"] = summary.get(f"total_{field}", 0) + (usage.get(field) or 0)
    if summary["first_call"] is None:
        summary["first_call"] = timestamp
    summary["last_call"] = timestamp

    os.makedirs(summary_path.parent, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def attempt_fallback(
    transcript: str,
    model: str = DEFAULT_MODEL,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    timeout: float = DEFAULT_TIMEOUT,
    log_path: str | Path | None = DEFAULT_LOG_PATH,
    enable_search: bool = ENABLE_SEARCH,
    system_instruction_override: str | None = None,
    memory_context: str | None = None,
    domain: str | None = None,
    short_memory: ShortMemoryResponse | None = None,
    source: str = "voice",
) -> FallbackOutcome:
    """Live entry point, called from listener/vad_listener.py's
    _handle_command() whenever the deterministic matcher returns NO_MATCH
    -- see the module docstring's LIVE note. Never raises: any failure
    (missing key, timeout, unreachable API, malformed output, a pick that
    doesn't validate) comes back as an outcome with bundle=answer=None,
    which the caller treats exactly like today's plain NO_MATCH -- same
    never-raises contract as both other fallback directories.

    system_instruction_override / memory_context / domain / short_memory
    are all purely additive, optional hooks -- omitting them reproduces the
    exact pre-domain-routing/pre-memory/pre-short-memory behavior of this
    function. `domain` is only ever a label for logging (see log_call());
    it does not affect what's sent to Gemini beyond whatever
    system_instruction_override the caller already composed for it.
    short_memory is passed straight through to build_prompt() -- see its
    docstring for what it is and isn't. This function does no reading or
    writing of short_term.json itself; that's the caller's job (see
    memory/short_term.py and its call sites in listener/vad_listener.py),
    same split memory_context already keeps between this module (per-call
    payload only) and memory/store.py (the actual I/O).

    source ("voice" or "chat", see chatbot/chat_listener.py) changes
    build_prompt()'s wording (see its docstring) and is stamped onto both
    the outcome and, when a command validates, the resulting IntentBundle
    -- validate_pick() is shared with the voice-only claude_code fallback
    and always produces a "voice"-sourced bundle, so this re-stamps it
    rather than threading source through that shared helper.
    """
    config = load_config(config_path)  # fresh load, see validate_pick's docstring
    prompt = build_prompt(
        transcript,
        config,
        enable_search=enable_search,
        memory_context=memory_context,
        short_memory=short_memory,
        source=source,
    )

    parsed = ParsedResult(None, {}, None, [], None, None, "")
    usage: dict = {}
    latency_seconds = None
    error = None
    bundle = None

    start = time.perf_counter()
    try:
        envelope = run_gemini(
            prompt,
            model=model,
            timeout=timeout,
            enable_search=enable_search,
            system_instruction_override=system_instruction_override,
        )
        latency_seconds = round(time.perf_counter() - start, 3)
        usage = envelope.get("usageMetadata", {})
        parsed = parse_result(envelope)
        bundle = validate_pick(transcript, parsed.intent, parsed.slots, config)
        if bundle is not None and source != "voice":
            # validate_pick() is shared with the voice-only claude_code
            # fallback and always stamps "voice" -- re-stamp here rather
            # than threading source through a helper that has no other
            # reason to know about input channels.
            bundle = dataclasses.replace(bundle, source=source)
        if bundle is None:
            # Either Gemini proposed intent=null (nothing to validate), or
            # it proposed an intent that failed re-validation -- in both
            # cases there's no command to run, so whatever answer it also
            # gave (if any) is still fair to speak. Same reasoning
            # claude_code.fallback.attempt_fallback() documents at the
            # identical point.
            pass
    except GeminiFallbackError as exc:
        latency_seconds = round(time.perf_counter() - start, 3)
        error = str(exc)

    outcome = FallbackOutcome(
        bundle=bundle,
        answer=parsed.answer,
        urls=parsed.urls,
        raw_intent=parsed.intent,
        raw_slots=parsed.slots,
        reason=parsed.reason,
        model=model,
        usage=usage,
        latency_seconds=latency_seconds,
        error=error,
        follow_up=parsed.follow_up,
        memory_candidate=parsed.memory_candidate,
        domain=domain,
        source=source,
    )
    if log_path is not None:
        log_call(outcome, transcript, log_path)
    return outcome


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "transcript",
        nargs="?",
        help="A transcript that already failed the deterministic matcher. Not needed with --usage.",
    )
    parser.add_argument(
        "--usage",
        action="store_true",
        help="Print the running token/latency total from logs/usage_summary.json and exit. "
        "No transcript needed, no API call made.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to the intent config (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Seconds to wait for Gemini before giving up (default: {DEFAULT_TIMEOUT}, uncalibrated).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request that would be sent, without calling the API "
        "(and without requiring GEMINI_API_KEY to be set).",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Don't append this call to logs/fallback.jsonl or logs/usage_summary.json.",
    )
    parser.add_argument(
        "--open-tabs",
        action="store_true",
        help="If the call produces an answer, actually launch "
        "open_research_tabs.ps1 (real Brave tabs), same as "
        "claude_code/fallback.py's --open-tabs. Off by default so manual "
        "testing doesn't pop a browser every run.",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Attach the google_search grounding tool. Off by default: "
        "confirmed against the live API that this tool has ZERO quota on "
        "a pure free-tier (no-billing) key -- attaching it gets an "
        "immediate 429 even as the first call of a session. Only pass "
        "this once a billing account is linked to the project behind "
        "GEMINI_API_KEY. See ENABLE_SEARCH's comment and README.md.",
    )
    parser.add_argument(
        "--domain",
        default=None,
        help="Force a domains/registry.yaml key (e.g. 'finance') for this "
        "call's system_instruction, bypassing routing/domains.py's anchor "
        "matcher -- for manually testing a domain's composed prompt "
        "against the real API without needing an anchor score to clear "
        "threshold first.",
    )
    parser.add_argument(
        "--memory",
        action="store_true",
        help="Read memory/MEMORY.md and inject it as per-call context "
        "(memory.schema.MemoryBundle.as_prompt_block()). Off by default "
        "for manual testing so a dry-run/real call isn't silently "
        "influenced by whatever's in the local memory file.",
    )
    args = parser.parse_args()

    def _resolve_domain_override(domain_key: str | None) -> str | None:
        if domain_key is None:
            return None
        from domains.loader import compose_system_instruction

        base = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        return compose_system_instruction(domain_key, base)

    def _resolve_memory_context(enabled: bool) -> str | None:
        if not enabled:
            return None
        from memory.store import read_memory

        return read_memory().as_prompt_block() or None

    if args.usage:
        summary = load_usage_summary()
        print(f"{DEFAULT_USAGE_SUMMARY_PATH}")
        print(f"  calls:                {summary['total_calls']}")
        avg = (
            summary["total_latency_seconds"] / summary["total_calls"]
            if summary["total_calls"]
            else 0.0
        )
        print(f"  total latency:        {summary['total_latency_seconds']:.2f}s")
        print(f"  avg latency/call:     {avg:.2f}s")
        print(f"  prompt tokens:        {summary.get('total_promptTokenCount', 0)}")
        print(f"  output tokens:        {summary.get('total_candidatesTokenCount', 0)}")
        print(f"  thinking tokens:      {summary.get('total_thoughtsTokenCount', 0)}")
        print(f"  cached tokens:        {summary.get('total_cachedContentTokenCount', 0)}")
        print(f"  first call:           {summary['first_call']}")
        print(f"  last call:            {summary['last_call']}")
        raise SystemExit(0)

    if not args.transcript:
        parser.error("a transcript is required unless --usage is given")

    domain_override = _resolve_domain_override(args.domain)
    memory_context = _resolve_memory_context(args.memory)

    if args.dry_run:
        cfg = load_config(args.config)
        built_prompt = build_prompt(args.transcript, cfg, enable_search=args.search, memory_context=memory_context)
        print(f"endpoint: {API_BASE}/{args.model}:generateContent")
        print(f"search:   {'on (google_search tool attached)' if args.search else 'off (default -- see --search help)'}")
        print(f"domain:   {args.domain or '(none -- flat SYSTEM_PROMPT.md)'}")
        print(f"system:   {'domains/loader.py composed bundle' if domain_override else SYSTEM_PROMPT_PATH}")
        if domain_override:
            print(f"\n--- system_instruction ---\n{domain_override}\n--- end system_instruction ---\n")
        print(f"prompt:\n{built_prompt}")
        raise SystemExit(0)

    result_outcome = attempt_fallback(
        args.transcript,
        model=args.model,
        config_path=args.config,
        timeout=args.timeout,
        log_path=None if args.no_log else DEFAULT_LOG_PATH,
        enable_search=args.search,
        system_instruction_override=domain_override,
        memory_context=memory_context,
        domain=args.domain,
    )
    print(f"Transcript: {args.transcript!r}")
    print(f"Model:      {args.model}")
    print(f"Domain:     {result_outcome.domain or '(none)'}")
    if result_outcome.error:
        print(f"Error:      {result_outcome.error}")
    print(f"Raw pick:   intent={result_outcome.raw_intent!r} slots={result_outcome.raw_slots} reason={result_outcome.reason!r}")
    if result_outcome.bundle is not None:
        print(f"Validated:  {result_outcome.bundle.describe()}")
    elif result_outcome.answer is not None:
        print(f"Answer:     {result_outcome.answer!r}")
        print(f"Follow-up:  {result_outcome.follow_up!r}")
        if result_outcome.memory_candidate:
            print(f"Memory:     {result_outcome.memory_candidate}")
        print(f"URLs:       {result_outcome.urls}")
        if args.open_tabs:
            tabs_error = open_research_tabs(args.transcript, result_outcome.urls)
            if tabs_error:
                print(f"Tabs error: {tabs_error}")
            else:
                # Same "give the reaper thread a moment before this process
                # exits" reasoning claude_code/fallback.py's __main__ block
                # documents at the identical point.
                print("Tabs:       launched (see reaper output below, if any)")
                time.sleep(min(DEFAULT_TABS_TIMEOUT, 3.0))
    else:
        print("Validated:  no match, no answer (nothing to run or say)")
    print(f"Latency:    {result_outcome.latency_seconds}s")
    print(f"Usage:      {result_outcome.usage}")
    raise SystemExit(0)
