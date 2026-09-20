"""One `claude -p` call, made only when `routing/route.py` returns NO_MATCH,
that answers both questions a rejected transcript could resolve to at once:

  1. A command after all, just misheard or paraphrased past the embedding
     threshold -- pick from the same closed intent set, or
  2. A genuinely open-ended question or request, not a command at all
     ("tell me a joke") -- answer it directly, in text meant to be spoken.

This used to be two sequential calls (a Haiku re-check, then, only if that
found nothing, a second full-model call to answer). Merging them into one
call with one combined schema was a direct fix for real, measured latency:
every open-ended question was paying for two full `claude` process
startups plus two prompt-cache round trips back to back, when the model
asked to re-check could just as easily have answered in the same breath.
One call, one round trip, always -- see readme.md's Stage 3 notes for the
before/after. Both jobs now run on Haiku, same reasoning as the timing fix
itself: speed matters more here than squeezing out a marginally better
joke.

This does NOT loosen "no LLM in the routing decision" (see readme.md,
intent_routing.md, routing/route.py). The model can only ever pick from the
exact closed set in routing/intents.yaml, never invent one, and every pick
is re-validated against a *fresh* load of that file before it's allowed
anywhere near execute() -- the prompt telling Claude what's legal is never
trusted on its own, same reasoning as routing/execute.py re-resolving a
handler path instead of trusting config validation already ran. An `answer`
never produces an IntentBundle at all, so it has no path to execute()
regardless of what it's asked -- see attempt_fallback()'s docstring.

The actual enforcement is structural, not just documented:
  - `claude` is always invoked with cwd=CLAUDE_CODE_DIR (this directory), so
    even a compromised or confused session has nothing outside it to read,
    per its own working directory.
  - `--allowedTools "WebSearch"` is the only tool granted. No file, shell, or
    execution tool is ever on the list.
  - `validate_pick()` re-checks the intent name and every slot value against
    a fresh `routing.config.load_config()`, independent of whatever the
    prompt echoed. A hallucinated or stale intent name is rejected, not
    trusted.

Cost/token tracking: `--output-format json` is what makes this possible, see
the module's example `claude -p` output. Every call's usage and
total_cost_usd is appended to logs/fallback.jsonl regardless of outcome.

PRIVACY: like routing/logs/routing.jsonl, logs/fallback.jsonl is a plaintext
record of things said out loud (and what was answered), plus what was spent
on them. Local only, never uploaded anywhere but this file's own subprocess
call.

Usage:
    python llm_fallback/claude_code/fallback.py "open the task thing"
    python llm_fallback/claude_code/fallback.py "tell me a joke"
    python llm_fallback/claude_code/fallback.py --dry-run "open the task thing"
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# routing/ is a sibling package two directories up from here. Same pattern as
# listener/vad_listener.py: put the repo root on sys.path so this resolves
# regardless of invocation cwd.
CLAUDE_CODE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = CLAUDE_CODE_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from routing import host  # noqa: E402
from routing.bundle import IntentBundle  # noqa: E402
from routing.config import DEFAULT_CONFIG_PATH, IntentConfig, IntentSpec, load_config  # noqa: E402
from routing.slots import find_free_text, find_value  # noqa: E402

DEFAULT_LOG_PATH = CLAUDE_CODE_DIR / "logs" / "fallback.jsonl"
# A running total, separate from the per-call fallback.jsonl above: that log
# is one line per call, exact but not something you want to eyeball or sum
# by hand to answer "how much has this cost so far." This is the answer to
# that question, updated in place after every call regardless of outcome.
DEFAULT_USAGE_SUMMARY_PATH = CLAUDE_CODE_DIR / "logs" / "usage_summary.json"

# The usage dict fields worth accumulating, see the module docstring's
# example `claude -p` output for their shape. token/cost tracking is the
# whole reason --output-format json is used at all (see readme.md's Stack
# table), so this list is deliberately exhaustive of what's cheap to sum
# rather than picking just input/output.
_SUMMED_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

# Guessed starting point, not a measured one -- same caveat routing/execute.py
# and vad_listener.py's --routing-threshold both carry for their own
# uncalibrated defaults. `claude -p` latency depends on network/API
# conditions this project doesn't control, so there's nothing to calibrate
# against the way routing/calibrate.py calibrates the embedding threshold.
# Revisit if real usage shows it's too tight or needlessly loose.
DEFAULT_TIMEOUT = 20.0

CLAUDE_COMMAND = ["claude", "-p"]
# Haiku for the whole call, command re-check and open-ended answer alike --
# see the module docstring for why this used to be two calls on two models
# and isn't anymore. --output-format json is what makes cost tracking
# possible.
CLAUDE_FLAGS = [
    "--model", "haiku",
    "--allowedTools", "WebSearch",
    "--permission-mode", "dontAsk",
    "--output-format", "json",
]

# Sentinel score for an LLM-assisted match, distinct from any real cosine
# similarity the embedding matcher could produce (those are always in
# [-1, 1] but this project's real scores cluster in [0.3, 1.0], see
# routing/README.md). 0.0 reads as "no confidence signal was measured here,"
# which is honest -- there is no embedding score for this bundle.
LLM_MATCH_SCORE = 0.0
LLM_MATCH_EXAMPLE = "(llm_fallback)"

# scripts/<os>/open_research_tabs.{ps1,sh} -- resolved via routing/host.py's
# script(), the same OS-aware resolution routing/execute.py's
# resolve_handler() uses on the deterministic path. Not a validated
# IntentBundle handler (it's not named by any routing/intents.yaml
# intent), so it's resolved directly here rather than through
# execute.py's build_command()/resolve_handler(), which are shaped around
# a bundle's handler+slots specifically.
RESEARCH_TABS_SCRIPT = host.script("open_research_tabs")
# Generous relative to DEFAULT_TIMEOUT: this only ever waits on the
# interpreter (powershell.exe / bash) long enough for it to launch Brave
# and return, not on Brave itself to open, so it should return in well
# under a second in practice. Wide margin here is cheap insurance against
# a slow process start, not an expectation of it actually taking this long.
DEFAULT_TABS_TIMEOUT = 10.0


@dataclass(frozen=True)
class FallbackOutcome:
    """Everything one attempt_fallback() call produced, for logging and for
    the caller to inspect. Exactly one of `bundle` / `answer` is non-None on
    a clean success (see validate_pick()'s docstring for why `bundle` can
    still end up None even when Claude proposed an intent); both are None
    when nothing was usable -- error, timeout, or Claude genuinely found
    neither a command nor a real question to answer -- and the caller falls
    through to today's plain "not_understood".
    """

    bundle: IntentBundle | None
    answer: str | None
    urls: list[str]
    raw_intent: str | None
    raw_slots: dict[str, str]
    reason: str
    session_id: str | None
    usage: dict
    total_cost_usd: float | None
    error: str | None

    @property
    def validated(self) -> bool:
        return self.bundle is not None


def _intents_for_prompt(config: IntentConfig) -> list[dict]:
    """The closed intent set, shaped for the prompt: name, examples, and
    slot vocabulary, nothing else -- no handler paths, since Claude never
    needs to know what a matched intent actually runs.
    """
    described = []
    for intent in config.intents:
        slots = []
        for slot in intent.slots:
            if slot.type == "closed_vocabulary":
                slots.append({"name": slot.name, "type": slot.type, "values": list(slot.values)})
            else:
                slots.append({"name": slot.name, "type": slot.type, "triggers": list(slot.triggers)})
        described.append({"intent": intent.key, "examples": list(intent.examples), "slots": slots})
    return described


def build_prompt(transcript: str, config: IntentConfig) -> str:
    """The per-call prompt. Repeats the output schema from CLAUDE.md
    deliberately -- that's the one instruction that must never drift, so
    it's stated in both places rather than assumed to carry over.

    One schema, two mutually exclusive outcomes: `intent` set means a
    command (`answer` must be null), `answer` set means an open-ended
    reply (`intent` must be null), both null means neither -- nothing
    plausible to run and nothing worth answering (noise, an unclear
    fragment). See CLAUDE.md for the full decision rule.
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
        '"answer": "<spoken_text_or_null>", "urls": [], "reason": "<short>"}\n'
        "Exactly one of intent/answer may be non-null, never both. `answer`, "
        "when used, must be plain text meant to be read aloud by "
        "text-to-speech -- one or two sentences, no markdown, no lists. "
        "`urls` is 0-3 real WebSearch result URLs worth reading further, "
        "only when answer is non-null and you actually searched for it -- "
        "never invented, [] otherwise."
    )


class FallbackError(Exception):
    """Anything that stops a call from producing a usable answer: a timed
    out or unreachable `claude` process, or output that couldn't be parsed
    as the outer --output-format json envelope. Never lets these propagate
    into the always-on listening loop -- attempt_fallback() catches this and
    returns a FallbackOutcome with bundle=answer=None instead.
    """


def run_claude(prompt: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Runs `claude -p <prompt> ...`, cwd pinned to this directory, and
    returns the parsed outer JSON envelope (the one with usage/total_cost_usd
    /result, see the module docstring's example). Raises FallbackError for
    anything that isn't a clean success -- a hung process, a non-JSON
    stdout, or is_error: true in the envelope itself.
    """
    command = [*CLAUDE_COMMAND, prompt, *CLAUDE_FLAGS]
    try:
        completed = subprocess.run(
            command,
            cwd=CLAUDE_CODE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise FallbackError(f"claude did not finish within {timeout}s") from None
    except FileNotFoundError:
        raise FallbackError("claude CLI not found on PATH") from None

    if completed.returncode != 0:
        raise FallbackError(
            f"claude exited {completed.returncode}: {completed.stderr.strip()[:300]}"
        )
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FallbackError(f"claude output wasn't valid JSON: {exc}") from None

    # is_error: true is NOT raised here, deliberately: the envelope still
    # carries real usage/total_cost_usd (tokens were spent producing it) and
    # this project tracks spend precisely so it can be seen, see
    # attempt_fallback()'s call to log_call()/update_usage_summary(). The
    # caller still ends up with nothing usable either way -- `result` on an
    # error response isn't the expected JSON, so parse_result() fails to
    # parse it and reports that in `reason`, same bug-tolerant outcome as
    # any other malformed response, just without discarding the cost data.
    return envelope


def _strip_markdown_fence(text: str) -> str:
    """CLAUDE.md and the prompt both say "no markdown fence," and Haiku
    still wraps its JSON in ```json ... ``` often enough in practice
    (observed in manual testing) that this can't be treated as a
    theoretical case. Strips a single leading/trailing triple-backtick
    fence, with or without a language tag, and leaves anything else
    untouched -- if the model wasn't fenced, this is a no-op.
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


_MAX_URLS = 3


def parse_result(envelope: dict) -> tuple[str | None, dict[str, str], str | None, list[str], str]:
    """Pulls (intent, slots, answer, urls, reason) out of the envelope's
    `result` field, which is itself supposed to be the JSON string
    CLAUDE.md's schema describes. Malformed or missing -> treated the same
    as intent=answer=None, urls=[], same bug-tolerant shape as
    routing/execute.py's spoken_override() falling back rather than
    raising.

    Both `intent` and `answer` present is a schema violation (the prompt
    says exactly one), not a malformed response worth discarding outright --
    `intent` wins, since it's the one branch that gets independently
    re-validated before anything trusts it, `answer` doesn't.

    `urls` is only ever kept alongside a non-null `answer` -- same reasoning
    as CLAUDE.md's rule that it's meaningless otherwise -- filtered here to
    strings only and capped at _MAX_URLS. Nothing about a URL's scheme or
    shape is checked at this layer: that's open_research_tabs.ps1's job
    (see open_research_tabs() below), same "re-validate at the boundary
    that actually acts on it" split validate_pick() uses for intent picks.
    """
    result_text = _strip_markdown_fence(envelope.get("result", ""))
    try:
        parsed = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return None, {}, None, [], f"unparseable result: {result_text!r}"

    if not isinstance(parsed, dict):
        return None, {}, None, [], f"result wasn't a JSON object: {parsed!r}"

    intent = parsed.get("intent")
    if intent is not None and not isinstance(intent, str):
        intent = None
    slots = parsed.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    answer = parsed.get("answer") if intent is None else None
    if answer is not None and not isinstance(answer, str):
        answer = None
    raw_urls = parsed.get("urls") or []
    urls = [u for u in raw_urls if isinstance(u, str)][:_MAX_URLS] if answer is not None else []
    reason = str(parsed.get("reason", ""))
    return intent, {str(k): str(v) for k, v in slots.items()}, (answer or None), urls, reason


def validate_pick(
    transcript: str,
    intent_name: str | None,
    proposed_slots: dict[str, str],
    config: IntentConfig,
) -> IntentBundle | None:
    """The re-validation step. intent_name must be an exact key in a
    freshly loaded config (never trust the prompt echo), and every declared
    slot must independently pass the same exact matcher routing/slots.py
    uses for the deterministic path -- proposed_slots is only ever used to
    know *which* slot the model thought applied, the value that ends up in
    the bundle always comes back out of find_value()/find_free_text() run
    against the real transcript, never out of the model's own text.

    Returns None on any failure: unknown intent, or any declared slot that
    doesn't independently validate. A MATCHED bundle only comes back when
    every check passes, same "fail rather than guess" rule routing/slots.py
    documents for the deterministic path.

    find_free_text(..., trust_intent_match=False) here, not the default:
    that function's "no trigger phrase -> fall back to the whole transcript"
    behavior is only sound when the caller's `intent` came from a real
    embedding score, which is what the deterministic path (routing/route.py
    -> extract()) has and this path does not -- `intent_name` here is a
    model's own guess, exactly the thing being re-validated, not yet-earned
    confidence. Found in manual testing against a local fallback model (see
    llm_fallback/ollama/): a weak model asked to re-check "tell me a joke"
    guessed `web_search` with no real basis, and the trusting fallback
    filled `query` with the whole transcript anyway, producing a MATCHED
    bundle -- an executable search from a wrong guess, not a rejected one.
    `trust_intent_match=False` makes "no trigger phrase found" MISSING_SLOT
    here instead, closing that gap for every caller of this function,
    Claude's own picks included.
    """
    if intent_name is None:
        return None
    intent = config.get(intent_name)
    if intent is None:
        return None

    filled: dict[str, str] = {}
    for slot in intent.slots:
        if slot.name not in proposed_slots:
            continue
        value = (
            find_free_text(slot, transcript, trust_intent_match=False)
            if slot.type == "free_text"
            else find_value(slot, transcript)
        )
        if value is None:
            # Claude thought this slot applied but the deterministic
            # extractor, run independently against the real transcript,
            # disagrees. Fail closed rather than trust Claude's value.
            return None
        filled[slot.name] = value

    return IntentBundle.matched(
        transcript=transcript,
        intent=intent.key,
        score=LLM_MATCH_SCORE,
        matched_example=LLM_MATCH_EXAMPLE,
        handler=intent.handler,
        slots=filled,
    )


def _reap_research_tabs(process: subprocess.Popen, timeout: float) -> None:
    """The completion half of open_research_tabs(), split out onto its own
    daemon thread so the launch half can return the moment the process
    exists rather than once the script (and Brave) have actually finished.
    Only ever prints -- there is no caller left to hand a result to by the
    time this runs, the turn that launched it already moved on. Same
    [llm_fallback] console line vad_listener.py used to print synchronously
    when this was one blocking call.

    Never lets an exception escape onto the thread: a background thread
    that dies from an uncaught exception just prints a traceback to stderr
    and vanishes, which is a worse silent failure than the thing it's
    trying to report.
    """
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[llm_fallback] research tabs: did not finish within {timeout}s")
        return
    except Exception as exc:  # noqa: BLE001 - see docstring, this must not propagate
        print(f"[llm_fallback] research tabs: reaper failed: {exc}")
        return
    if process.returncode != 0:
        print(f"[llm_fallback] research tabs: exited {process.returncode}: {stderr.strip()[:300]}")


def open_research_tabs(
    query: str,
    urls: list[str],
    timeout: float = DEFAULT_TABS_TIMEOUT,
) -> str | None:
    """Launches scripts/open_research_tabs.ps1 and returns as soon as the
    process exists, without waiting for it (or Brave) to finish. Opens a
    Brave Search tab for `query` plus up to 3 curated `urls` the fallback
    model already found via WebSearch (see parse_result()'s cap). Only
    ever called by the caller alongside a spoken `answer` -- same split
    attempt_fallback() already has between "the model returns an opinion"
    and "trusted code decides whether to act on it" (see validate_pick()'s
    docstring), just extended to a browser side effect instead of an
    IntentBundle. The model's `urls` are not trusted blindly even here: the
    script itself re-checks each one is an absolute http/https URI before
    it ever reaches Start-Process, same "re-validate at the boundary that
    acts on it" reasoning as validate_pick() re-checking an intent pick
    against the real config instead of the prompt's echo.

    Deliberately doesn't wait: this used to be a single blocking
    subprocess.run(), and the caller (listener/vad_listener.py's
    _handle_command()) only spoke "pulling up some helpful sites" *after*
    that returned -- so the confirmation always trailed the browser
    launch by however long the round trip to powershell.exe took, instead
    of landing alongside it. Nothing about the spoken line depends on the
    script having finished, only on the process having been started, so
    the wait was pure dead air. Popen here returns as soon as the process
    exists; _reap_research_tabs() picks up watching it from a daemon
    thread so a real failure (Brave missing, a bad exit code) still gets
    logged, just after the fact instead of gating the speech.

    Never raises -- this is a nice-to-have alongside the spoken answer, not
    something that should ever crash or block the caller (the always-on
    listening loop) if Brave is missing, the script errors, or powershell
    fails to start at all. Returns an error string only for a failure
    this function can detect *synchronously* -- the script not existing,
    or the process failing to even start -- since those are the only
    failures the caller can know about before it would otherwise speak;
    None means "launched", not "succeeded". Anything that goes wrong after
    that point is the reaper thread's problem to report, not this
    function's, same as a fire-and-forget log write.

    query is passed as-is to -File's -Query argument, which subprocess
    passes as a single argv entry (shell=False, same as run_claude() and
    routing/execute.py's build_command()) -- there is no shell for it to
    inject into, same reasoning routing/execute.py's module docstring gives
    for slot values and web_search.ps1 gives for its own -Query.

    urls is joined with '|' into a single -Urls/--urls argv entry, not
    passed as several separate ones -- see open_research_tabs's own -Urls
    doc for why a comma-joined string (the delimiter PowerShell's CLI
    binder actually splits on for a [string[]] parameter) isn't safe here:
    a URL's query string can itself contain a literal comma.
    """
    if not RESEARCH_TABS_SCRIPT.is_file():
        return f"open_research_tabs script not found at {RESEARCH_TABS_SCRIPT}"

    slots = {"query": query}
    if urls:
        slots["urls"] = "|".join(urls)
    command = host.command(RESEARCH_TABS_SCRIPT, slots)

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        return f"{command[0]} not found on PATH"

    threading.Thread(target=_reap_research_tabs, args=(process, timeout), daemon=True).start()
    return None


def log_call(outcome: FallbackOutcome, transcript: str, log_path: str | Path = DEFAULT_LOG_PATH) -> None:
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "transcript": transcript,
        "session_id": outcome.session_id,
        "usage": outcome.usage,
        "total_cost_usd": outcome.total_cost_usd,
        "raw_intent": outcome.raw_intent,
        "raw_slots": outcome.raw_slots,
        "answer": outcome.answer,
        "urls": outcome.urls,
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
        outcome.total_cost_usd,
        entry["timestamp"],
        summary_path=log_path.parent / DEFAULT_USAGE_SUMMARY_PATH.name,
    )


def _empty_usage_summary() -> dict:
    return {
        "total_calls": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_creation_input_tokens": 0,
        "total_cache_read_input_tokens": 0,
        "first_call": None,
        "last_call": None,
    }


def load_usage_summary(summary_path: str | Path = DEFAULT_USAGE_SUMMARY_PATH) -> dict:
    """The running total, or a fresh zeroed one if the file doesn't exist
    yet or somehow got corrupted -- a summary file that can't be read is a
    reason to start counting again, not a reason to crash the listener.
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
    total_cost_usd: float | None,
    timestamp: str,
    summary_path: str | Path = DEFAULT_USAGE_SUMMARY_PATH,
) -> dict:
    """Accumulates one call's usage/cost into the running total and writes
    it back. Called from log_call() for every call regardless of outcome
    (a timed-out or malformed-JSON call still spent real tokens producing
    whatever it produced, see run_claude()'s is_error handling), so this is
    the one place "how much has llm_fallback cost so far" can be answered
    without re-summing the whole of fallback.jsonl by hand.

    Read-modify-write, not append-only, on purpose: unlike fallback.jsonl,
    which is a record of individual calls worth keeping one line per call,
    this file only ever needs to answer "what's the running total," so
    there is nothing to gain from growing it forever.
    """
    summary_path = Path(summary_path)
    summary = load_usage_summary(summary_path)
    summary["total_calls"] += 1
    summary["total_cost_usd"] = round(summary["total_cost_usd"] + (total_cost_usd or 0.0), 6)
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
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    timeout: float = DEFAULT_TIMEOUT,
    log_path: str | Path | None = DEFAULT_LOG_PATH,
) -> FallbackOutcome:
    """The one function listener/vad_listener.py calls, one `claude`
    subprocess per invocation. Never raises: any failure (timeout,
    unreachable claude, malformed output, a pick that doesn't validate)
    comes back as a FallbackOutcome with bundle=answer=None, which the
    caller treats exactly like today's plain NO_MATCH.
    """
    config = load_config(config_path)  # fresh load, see validate_pick's docstring
    prompt = build_prompt(transcript, config)

    raw_intent: str | None = None
    raw_slots: dict[str, str] = {}
    answer: str | None = None
    urls: list[str] = []
    reason = ""
    session_id = None
    usage: dict = {}
    total_cost_usd = None
    error = None
    bundle = None

    try:
        envelope = run_claude(prompt, timeout=timeout)
        session_id = envelope.get("session_id")
        usage = envelope.get("usage", {})
        total_cost_usd = envelope.get("total_cost_usd")
        raw_intent, raw_slots, answer, urls, reason = parse_result(envelope)
        bundle = validate_pick(transcript, raw_intent, raw_slots, config)
        if bundle is None:
            # Either Claude proposed intent=null (nothing to validate), or
            # it proposed an intent that failed re-validation -- in both
            # cases there's no command to run, so whatever answer it also
            # gave (if any) is still fair to speak. A rejected command pick
            # doesn't retroactively make the transcript not a real question.
            pass
    except FallbackError as exc:
        error = str(exc)

    outcome = FallbackOutcome(
        bundle=bundle,
        answer=answer,
        urls=urls,
        raw_intent=raw_intent,
        raw_slots=raw_slots,
        reason=reason,
        session_id=session_id,
        usage=usage,
        total_cost_usd=total_cost_usd,
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
        help="Print the running cost/token total from logs/usage_summary.json and exit. "
        "No transcript needed, no claude call made.",
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
        help=f"Seconds to wait for `claude` before giving up (default: {DEFAULT_TIMEOUT}, uncalibrated).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command and prompt that would be sent, without calling claude.",
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
        "listener/vad_listener.py does on a live answer. Off by default so "
        "manual testing doesn't pop a browser every run.",
    )
    args = parser.parse_args()

    if args.usage:
        summary = load_usage_summary()
        print(f"{DEFAULT_USAGE_SUMMARY_PATH}")
        print(f"  calls:                {summary['total_calls']}")
        print(f"  total cost:           ${summary['total_cost_usd']}")
        print(f"  input tokens:         {summary['total_input_tokens']}")
        print(f"  output tokens:        {summary['total_output_tokens']}")
        print(f"  cache write tokens:   {summary['total_cache_creation_input_tokens']}")
        print(f"  cache read tokens:    {summary['total_cache_read_input_tokens']}")
        print(f"  first call:           {summary['first_call']}")
        print(f"  last call:            {summary['last_call']}")
        raise SystemExit(0)

    if not args.transcript:
        parser.error("a transcript is required unless --usage is given")

    if args.dry_run:
        cfg = load_config(args.config)
        built_prompt = build_prompt(args.transcript, cfg)
        print(f"cwd:     {CLAUDE_CODE_DIR}")
        print(f"command: {[*CLAUDE_COMMAND, '<prompt>', *CLAUDE_FLAGS]}")
        print(f"prompt:\n{built_prompt}")
        raise SystemExit(0)

    result_outcome = attempt_fallback(
        args.transcript,
        config_path=args.config,
        timeout=args.timeout,
        log_path=None if args.no_log else DEFAULT_LOG_PATH,
    )
    print(f"Transcript: {args.transcript!r}")
    if result_outcome.error:
        print(f"Error:      {result_outcome.error}")
    print(f"Raw pick:   intent={result_outcome.raw_intent!r} slots={result_outcome.raw_slots} reason={result_outcome.reason!r}")
    if result_outcome.bundle is not None:
        print(f"Validated:  {result_outcome.bundle.describe()}")
    elif result_outcome.answer is not None:
        print(f"Answer:     {result_outcome.answer!r}")
        print(f"URLs:       {result_outcome.urls}")
        if args.open_tabs:
            tabs_error = open_research_tabs(args.transcript, result_outcome.urls)
            if tabs_error:
                print(f"Tabs error: {tabs_error}")
            else:
                # open_research_tabs() no longer waits for the script to
                # finish (see its docstring) -- launched here, completion
                # (or a late failure) is reported by its background reaper
                # thread, which needs a moment to run before this process
                # exits, or it never gets the chance to print anything.
                print("Tabs:       launched (see reaper output below, if any)")
                time.sleep(min(DEFAULT_TABS_TIMEOUT, 3.0))
    else:
        print("Validated:  no match, no answer (nothing to run or say)")
    print(f"Cost:       ${result_outcome.total_cost_usd}  usage={result_outcome.usage}")
    raise SystemExit(0)
