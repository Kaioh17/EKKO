"""PROTOTYPE -- not called from anywhere. See README.md.

A local-model twin of `llm_fallback/claude_code/fallback.py`, built to answer
one question: does a model running on this machine's own GPU (Ollama) answer
a NO_MATCH transcript meaningfully faster than paying for a `claude -p`
process startup plus a network round trip? See README.md for the reasoning
and how to benchmark the two side by side.

Same job, same two possible outcomes, same output schema as the cloud
version:

  1. A command after all, just misheard or paraphrased past the embedding
     threshold -- pick from the same closed intent set, or
  2. A genuinely open-ended question or request ("tell me a joke") -- answer
     it directly, in text meant to be spoken.

Same safety contract too, deliberately reused rather than reimplemented:
`validate_pick()`, `parse_result()`, `_strip_markdown_fence()`, and
`_intents_for_prompt()` are imported straight from `claude_code.fallback`
instead of copy-pasted, so there is exactly one place that decides what a
model's JSON output is allowed to mean -- see validate_pick()'s docstring
there for why an intent name is never trusted without re-checking it against
a fresh `routing/intents.yaml` load. Importing from claude_code here does not
create a cycle: claude_code/ has no idea this directory exists.

What's genuinely different from the cloud version:
  - No tools. `--allowedTools "WebSearch"` has no local equivalent here; a
    factual question this model can't answer from its own weights should
    come back as an honest `answer` saying so (SYSTEM_PROMPT.md says this
    explicitly), not a guess.
  - No dollar cost, so nothing here tracks total_cost_usd. Usage tracking
    instead sums token counts and wall-clock duration -- Ollama's response
    envelope reports eval_count/eval_duration directly, which is exactly
    the "was this actually faster" number this prototype exists to answer.
  - Talks to a local HTTP server (Ollama's REST API on localhost:11434)
    instead of spawning a subprocess. stdlib `urllib.request` only,
    deliberately -- this is a prototype, not a reason to add `requests` to
    requirements.txt for one caller.

Usage:
    python llm_fallback/ollama/fallback_ollama.py "open the task thing"
    python llm_fallback/ollama/fallback_ollama.py "tell me a joke"
    python llm_fallback/ollama/fallback_ollama.py --dry-run "open the task thing"
    python llm_fallback/ollama/fallback_ollama.py --model qwen2.5:7b-instruct-q4_K_M "I'd like to calculate calorie deficit for a 70 kg girl at the age of 18 years old, 5 foot 4."
    python llm_fallback/ollama/fallback_ollama.py --usage
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# routing/ and llm_fallback/claude_code/ are siblings two and one
# directories up from here respectively. Same sys.path pattern
# claude_code/fallback.py uses for the same reason: resolve regardless of
# invocation cwd.
OLLAMA_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = OLLAMA_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from routing.bundle import IntentBundle  # noqa: E402
from routing.config import DEFAULT_CONFIG_PATH, IntentConfig, load_config  # noqa: E402

# Reused, not reimplemented -- see module docstring.
from llm_fallback.claude_code.fallback import (  # noqa: E402
    _intents_for_prompt,
    _strip_markdown_fence,
    validate_pick,
)

DEFAULT_LOG_PATH = OLLAMA_DIR / "logs" / "fallback.jsonl"
DEFAULT_USAGE_SUMMARY_PATH = OLLAMA_DIR / "logs" / "usage_summary.json"

SYSTEM_PROMPT_PATH = OLLAMA_DIR / "SYSTEM_PROMPT.md"

DEFAULT_HOST = "http://localhost:11434"
# A 3B instruct model (llama3.2:3b) was the starting guess for this machine
# (RTX 4070 Laptop GPU, 8GB VRAM; see README.md's hardware notes). Manual
# testing showed that guess was wrong: llama3.2:3b and phi4-mini (3.8B) both
# reliably re-match paraphrased commands but were unreliable at answering
# open-ended requests -- one hallucinated a command intent for plain
# arithmetic, the other hallucinated a `web_search` pick for "tell me a
# joke" that (before the trust_intent_match fix in
# claude_code/fallback.py's validate_pick()) would have validated as a real
# executable action. qwen2.5:7b-instruct-q4_K_M got all three re-tested
# cases right at comparable warm-call latency (~1.5s) -- see README.md's
# "Known issue" section for the full comparison. Must be pulled first:
# `ollama pull qwen2.5:7b-instruct-q4_K_M`. `--model` overrides freely for
# testing other sizes.

##phi4-mini
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"


# Same uncalibrated-guess caveat DEFAULT_TIMEOUT carries in claude_code/
# fallback.py, but measured up rather than guessed low: the *first* call
# against a freshly pulled model pays a one-time cold-load cost paging
# weights into VRAM -- 26.5s of a 48.8s total in manual testing on this
# machine's 3B model -- before warm calls drop to ~1.3s. A tight timeout
# here reads as "Ollama hung" when it's actually just loading. KEEP_ALIVE
# below is what keeps that cost from recurring on every call.
DEFAULT_TIMEOUT = 60.0

# Ollama unloads a model from VRAM after 5 minutes idle by default, which
# would put every first call after a pause back through the cold-load cost
# DEFAULT_TIMEOUT's comment describes. Keeping it resident for the length of
# a benchmarking session is what actually lets warm-call numbers be
# compared meaningfully. Passed as Ollama's own `keep_alive` request field
# (see run_ollama()), not something enforced independently here.
DEFAULT_KEEP_ALIVE = "30m"

_SUMMED_USAGE_FIELDS = (
    "prompt_eval_count",
    "eval_count",
)


@dataclass(frozen=True)
class OllamaFallbackOutcome:
    """Local-model counterpart to claude_code.fallback.FallbackOutcome. Same
    shape minus session_id/total_cost_usd (nothing here has either), plus
    total_duration_ns, which is the number this prototype exists to compare
    against the cloud version's wall-clock time.
    """

    bundle: IntentBundle | None
    answer: str | None
    raw_intent: str | None
    raw_slots: dict[str, str]
    reason: str
    model: str
    usage: dict
    total_duration_ns: int | None
    error: str | None

    @property
    def validated(self) -> bool:
        return self.bundle is not None


def build_prompt(transcript: str, config: IntentConfig) -> str:
    """Same payload shape as claude_code.fallback.build_prompt() -- the two
    are meant to be directly comparable, same transcript in, same intent
    list in, same schema demanded back.
    """
    payload = {
        "transcript": transcript,
        "status": "not_understood",
        "intents": _intents_for_prompt(config),
    }
    return (
        "A voice command didn't match anything in the closed intent set below "
        "(status: not_understood). First, decide whether it plausibly means "
        "one of these intents anyway, e.g. due to mishearing or paraphrase. "
        "If it doesn't, decide whether it's instead a genuine open-ended "
        "question or request you can just answer directly.\n\n"
        f"{json.dumps(payload)}\n\n"
        "Respond with ONLY this JSON object, no prose, no markdown fence: "
        '{"intent": "<name_from_the_list_above_or_null>", "slots": {}, '
        '"answer": "<spoken_text_or_null>", "reason": "<short>"}\n'
        "Exactly one of intent/answer may be non-null, never both. `answer`, "
        "when used, must be plain text meant to be read aloud by "
        "text-to-speech -- one or two sentences, no markdown, no lists."
    )


class OllamaFallbackError(Exception):
    """Anything that stops a call from producing a usable answer: Ollama
    unreachable, the model isn't pulled, a timeout, or output that couldn't
    be parsed. Same "never propagate into the caller" role
    claude_code.fallback.FallbackError plays -- attempt_fallback() below
    catches this and returns an outcome with bundle=answer=None instead.
    """


def run_ollama(
    prompt: str,
    model: str = DEFAULT_MODEL,
    host: str = DEFAULT_HOST,
    timeout: float = DEFAULT_TIMEOUT,
    keep_alive: str = DEFAULT_KEEP_ALIVE,
) -> dict:
    """POSTs to Ollama's /api/generate, non-streaming, and returns the
    parsed response envelope (response text plus eval_count/eval_duration/
    total_duration -- see https://github.com/ollama/ollama/blob/main/docs/api.md).
    `format: "json"` is Ollama's equivalent of claude_code/fallback.py's
    `--output-format json`: it constrains the model to emit a JSON object,
    though (unlike the cloud model) nothing here enforces the *shape* of
    that object -- parse_result() below still treats it as untrusted.
    """
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    body = json.dumps(
        {
            "model": model,
            "system": system_prompt,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "keep_alive": keep_alive,
            "options": {"temperature": 0.2},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Most common cause in practice: `model` was never pulled. Ollama's
        # error body (e.g. {"error": "model 'x' not found"}) is more useful
        # here than the bare HTTP status, so surface it when present.
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("error", body)
        except json.JSONDecodeError:
            detail = body
        raise OllamaFallbackError(f"Ollama returned HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise OllamaFallbackError(
            f"couldn't reach Ollama at {host} ({exc.reason}) -- is `ollama serve` running?"
        ) from None
    except TimeoutError:
        raise OllamaFallbackError(f"Ollama did not respond within {timeout}s") from None
    except json.JSONDecodeError as exc:
        raise OllamaFallbackError(f"Ollama response wasn't valid JSON: {exc}") from None

    if envelope.get("error"):
        # Most common cause in practice: the named model was never pulled.
        raise OllamaFallbackError(f"Ollama error: {envelope['error']}")
    return envelope


def parse_result(envelope: dict) -> tuple[str | None, dict[str, str], str | None, str]:
    """Same contract as claude_code.fallback.parse_result(), applied to
    Ollama's `response` field instead of the `claude -p` envelope's
    `result` field -- everything past that is identical reasoning (intent
    wins on a schema violation, malformed output collapses to
    intent=answer=None), so this stays a thin adapter rather than a copy.

    One local-model-specific wrinkle Haiku doesn't need: observed in manual
    testing, `llama3.2:3b` emitted the JSON *string* `"null"` for `intent`
    rather than the JSON literal `null`, despite SYSTEM_PROMPT.md asking for
    exactly the latter. Harmless as far as safety goes -- validate_pick()
    would reject `"null"` as an unknown intent name regardless -- but it's
    worth normalising here rather than relying on no config ever legitimately
    being named "null"/"none". Case-insensitive since a small model's exact
    casing isn't reliable either.
    """
    result_text = _strip_markdown_fence(envelope.get("response", ""))
    try:
        parsed = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return None, {}, None, f"unparseable response: {result_text!r}"

    if not isinstance(parsed, dict):
        return None, {}, None, f"response wasn't a JSON object: {parsed!r}"

    intent = parsed.get("intent")
    if intent is not None and not isinstance(intent, str):
        intent = None
    if intent is not None and intent.strip().lower() in ("null", "none"):
        intent = None
    slots = parsed.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    answer = parsed.get("answer") if intent is None else None
    if answer is not None and not isinstance(answer, str):
        answer = None
    reason = str(parsed.get("reason", ""))
    return intent, {str(k): str(v) for k, v in slots.items()}, (answer or None), reason


def log_call(
    outcome: OllamaFallbackOutcome, transcript: str, log_path: str | Path = DEFAULT_LOG_PATH
) -> None:
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "transcript": transcript,
        "model": outcome.model,
        "usage": outcome.usage,
        "total_duration_ns": outcome.total_duration_ns,
        "raw_intent": outcome.raw_intent,
        "raw_slots": outcome.raw_slots,
        "answer": outcome.answer,
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
        outcome.total_duration_ns,
        entry["timestamp"],
        summary_path=log_path.parent / DEFAULT_USAGE_SUMMARY_PATH.name,
    )


def _empty_usage_summary() -> dict:
    return {
        "total_calls": 0,
        "total_duration_ns": 0,
        "total_prompt_eval_count": 0,
        "total_eval_count": 0,
        "first_call": None,
        "last_call": None,
    }


def load_usage_summary(summary_path: str | Path = DEFAULT_USAGE_SUMMARY_PATH) -> dict:
    summary_path = Path(summary_path)
    if not summary_path.exists():
        return _empty_usage_summary()
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_usage_summary()


def update_usage_summary(
    usage: dict,
    total_duration_ns: int | None,
    timestamp: str,
    summary_path: str | Path = DEFAULT_USAGE_SUMMARY_PATH,
) -> dict:
    """Same read-modify-write shape as claude_code.fallback's counterpart,
    tokens and wall-clock duration instead of tokens and dollars -- this is
    the number "is local actually faster" gets answered from, without
    re-averaging the whole fallback.jsonl log by hand.
    """
    summary_path = Path(summary_path)
    summary = load_usage_summary(summary_path)
    summary["total_calls"] += 1
    summary["total_duration_ns"] += total_duration_ns or 0
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
    host: str = DEFAULT_HOST,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    timeout: float = DEFAULT_TIMEOUT,
    keep_alive: str = DEFAULT_KEEP_ALIVE,
    log_path: str | Path | None = DEFAULT_LOG_PATH,
) -> OllamaFallbackOutcome:
    """Standalone prototype entry point -- NOT called from vad_listener.py or
    anywhere else. Same never-raises contract as
    claude_code.fallback.attempt_fallback() for when/if this ever is wired
    up: any failure comes back as an outcome with bundle=answer=None rather
    than propagating.
    """
    config = load_config(config_path)  # fresh load, see validate_pick's docstring
    prompt = build_prompt(transcript, config)

    raw_intent: str | None = None
    raw_slots: dict[str, str] = {}
    answer: str | None = None
    reason = ""
    usage: dict = {}
    total_duration_ns = None
    error = None
    bundle = None

    try:
        envelope = run_ollama(prompt, model=model, host=host, timeout=timeout, keep_alive=keep_alive)
        total_duration_ns = envelope.get("total_duration")
        usage = {
            "prompt_eval_count": envelope.get("prompt_eval_count"),
            "eval_count": envelope.get("eval_count"),
            "prompt_eval_duration_ns": envelope.get("prompt_eval_duration"),
            "eval_duration_ns": envelope.get("eval_duration"),
            "load_duration_ns": envelope.get("load_duration"),
        }
        raw_intent, raw_slots, answer, reason = parse_result(envelope)
        bundle = validate_pick(transcript, raw_intent, raw_slots, config)
    except OllamaFallbackError as exc:
        error = str(exc)

    outcome = OllamaFallbackOutcome(
        bundle=bundle,
        answer=answer,
        raw_intent=raw_intent,
        raw_slots=raw_slots,
        reason=reason,
        model=model,
        usage=usage,
        total_duration_ns=total_duration_ns,
        error=error,
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
        help="Print the running duration/token total from logs/usage_summary.json and exit. "
        "No transcript needed, no Ollama call made.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model tag to use (default: {DEFAULT_MODEL}). Must already be pulled "
        "(`ollama pull <name>`).",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Ollama server URL (default: {DEFAULT_HOST}).",
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
        help=f"Seconds to wait for Ollama before giving up (default: {DEFAULT_TIMEOUT}, uncalibrated).",
    )
    parser.add_argument(
        "--keep-alive",
        default=DEFAULT_KEEP_ALIVE,
        help=f"How long Ollama keeps the model loaded in VRAM after this call "
        f"(default: {DEFAULT_KEEP_ALIVE}). Ollama's own default is 5m -- raised here so a "
        "benchmarking session doesn't keep re-paying the cold-load cost between calls.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request that would be sent, without calling Ollama.",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Don't append this call to logs/fallback.jsonl or logs/usage_summary.json.",
    )
    args = parser.parse_args()

    if args.usage:
        summary = load_usage_summary()
        print(f"{DEFAULT_USAGE_SUMMARY_PATH}")
        print(f"  calls:                {summary['total_calls']}")
        print(f"  total duration:       {summary['total_duration_ns'] / 1e9:.2f}s")
        avg = (summary["total_duration_ns"] / summary["total_calls"] / 1e9) if summary["total_calls"] else 0.0
        print(f"  avg duration/call:    {avg:.2f}s")
        print(f"  prompt tokens:        {summary['total_prompt_eval_count']}")
        print(f"  output tokens:        {summary['total_eval_count']}")
        print(f"  first call:           {summary['first_call']}")
        print(f"  last call:            {summary['last_call']}")
        raise SystemExit(0)

    if not args.transcript:
        parser.error("a transcript is required unless --usage is given")

    if args.dry_run:
        cfg = load_config(args.config)
        built_prompt = build_prompt(args.transcript, cfg)
        print(f"host:    {args.host}")
        print(f"model:   {args.model}")
        print(f"system:  {SYSTEM_PROMPT_PATH}")
        print(f"prompt:\n{built_prompt}")
        raise SystemExit(0)

    start = time.perf_counter()
    result_outcome = attempt_fallback(
        args.transcript,
        model=args.model,
        host=args.host,
        config_path=args.config,
        timeout=args.timeout,
        keep_alive=args.keep_alive,
        log_path=None if args.no_log else DEFAULT_LOG_PATH,
    )
    wall_clock = time.perf_counter() - start

    print(f"Transcript: {args.transcript!r}")
    print(f"Model:      {args.model}")
    if result_outcome.error:
        print(f"Error:      {result_outcome.error}")
    print(f"Raw pick:   intent={result_outcome.raw_intent!r} slots={result_outcome.raw_slots} reason={result_outcome.reason!r}")
    if result_outcome.bundle is not None:
        print(f"Validated:  {result_outcome.bundle.describe()}")
    elif result_outcome.answer is not None:
        print(f"Answer:     {result_outcome.answer!r}")
    else:
        print("Validated:  no match, no answer (nothing to run or say)")
    if result_outcome.total_duration_ns is not None:
        print(f"Ollama time: {result_outcome.total_duration_ns / 1e9:.2f}s (server-reported)")
    print(f"Wall clock: {wall_clock:.2f}s (this process, includes Python startup)")
    print(f"Usage:      {result_outcome.usage}")
    raise SystemExit(0)
