"""Shared low-level Gemini API transport: auth (.env + env var), the raw
`generateContent` HTTP call, and the handful of client-side defaults every
Gemini caller in this project shares. Extracted out of `fallback_gemini.py`
so `scripts/daily_briefing.py` -- a generic summarization caller with no
intent-routing schema, no `IntentBundle`, no `validate_pick()` -- can reuse
the same auth/transport/model defaults without pulling in that module's
intent-routing-specific machinery it has no use for. Same "small,
self-contained helper, not a heavier import" reasoning
`scripts/daily_briefing.py`'s own `fetch_commentary()` docstring already
gives for not importing `llm_fallback/claude_code/fallback.py`'s
`run_claude()` -- this module is that same idea, just shared at the
transport layer instead of duplicated per caller, since two full copies of
"POST to generateContent, parse the envelope, handle the .env file" would
drift the moment one caller's error handling got a fix the other didn't.

Callers today:
  - `fallback_gemini.py`'s `run_gemini()` -- intent re-check + open-ended
    answers, `system_instruction=SYSTEM_PROMPT.md`, optional `google_search`
    tool.
  - `scripts/daily_briefing.py`'s `fetch_news_narration()` /
    `fetch_commentary()` -- plain summarization from given text, no system
    instruction, no tools, same "give it content, let it rewrite" job
    confirmed working in manual testing before this was wired in for real.

Stdlib-only (`urllib.request`), same reasoning `ollama/fallback_ollama.py`
gives for not adding `requests` as a dependency for a handful of callers.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

GEMINI_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = GEMINI_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# .env loading moved to routing/host.py (same stdlib-only KEY=value parser,
# same "explicit env wins over file" rule) so EKKO_OS is readable there
# without importing this module -- see that file's docstring. Importing it
# here for the side effect keeps every caller of this module working
# exactly as before, with no per-caller load_dotenv() call needed.
from routing.host import load_dotenv  # noqa: E402

load_dotenv()

API_KEY_ENV = "GEMINI_API_KEY"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# flash-lite over flash: this project's whole reason for touching a second
# provider (see llm_fallback/README.md) is that Haiku's real usage log
# showed 91% of output tokens going to extended thinking on calls that
# should be fast classification/summarization -- flash-lite is Gemini's
# fastest/cheapest tier and, combined with THINKING_BUDGET below, is the
# closest available match to "don't think hard, just do the job."
# gemini-2.5-flash-lite (the original guess here) was retired for new users
# -- confirmed against the live API on 2026-08-20, whichever version is
# current should be re-checked at
# https://ai.google.dev/gemini-api/docs/models before trusting this default
# blindly, model names on the free tier move faster than this file does.
DEFAULT_MODEL = "gemini-3.5-flash-lite"

# Lowest value that asks gemini-3.5-flash-lite to skip extended thinking.
# `0` (the documented "off" value for Gemini's 2.5-generation models) is
# REJECTED outright by gemini-3.5-flash-lite with a bare 400
# INVALID_ARGUMENT -- confirmed against the live API on 2026-08-20. `1` is
# the lowest value the API accepts and was observed producing
# thoughtsTokenCount: 0 (absent from usageMetadata entirely) on manual
# testing, i.e. it behaves as "off" in practice even though the literal
# `0` doesn't work. Re-verify against
# https://ai.google.dev/gemini-api/docs/thinking if the default model
# changes -- this is a per-model-version quirk, not a documented contract.
THINKING_BUDGET = 1

DEFAULT_TIMEOUT = 15.0


class GeminiError(Exception):
    """Anything that stops a call from producing a usable response: a
    missing API key, an unreachable/erroring endpoint, a timeout, or output
    that couldn't be parsed as the outer JSON envelope. Every caller is
    expected to catch this and degrade (empty result / plain "didn't work"
    log line), never let it propagate into the always-on listening loop or
    crash a briefing window over one bad fetch -- same
    "never raises past this point" contract `claude_code/fallback.py`'s
    `FallbackError` and `ollama/fallback_ollama.py`'s
    `OllamaFallbackError` both already keep.
    """


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise GeminiError(
            f"{API_KEY_ENV} is not set. Get a free key at "
            "https://aistudio.google.com/apikey and set it as an "
            f"environment variable ({API_KEY_ENV})."
        )
    return key


def generate_content(
    prompt_text: str | None = None,
    *,
    contents: list[dict] | None = None,
    system_instruction: str | None = None,
    tools: list[dict] | None = None,
    response_mime_type: str | None = "application/json",
    thinking_budget: int | None = THINKING_BUDGET,
    temperature: float = 0.2,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """POSTs to Gemini's `generateContent` endpoint and returns the parsed
    response envelope. Every caller-specific choice (system prompt, tools,
    schema) is a parameter here rather than baked in, since the two real
    callers want different combinations: `fallback_gemini.py` wants a
    system instruction and sometimes a `google_search` tool,
    `daily_briefing.py` wants neither, just a plain user-turn prompt.

    `prompt_text` is a convenience for the common one-user-turn case;
    pass `contents` directly for anything more structured. Exactly one of
    the two must be given.

    Raises GeminiError on anything that isn't a clean HTTP 200 with valid
    JSON -- never returns a partial/guessed result. `is_error`-shaped
    envelopes at the JSON level (a blocked response, an empty candidate
    list) are NOT raised here, same reasoning `claude_code/fallback.py`'s
    `run_claude()` gives for its own `is_error: true` handling: the caller
    still needs the raw envelope to decide what "nothing usable" means for
    its own schema (extract_text() below returns "" for these, not an
    exception).
    """
    if contents is None:
        if prompt_text is None:
            raise ValueError("generate_content() needs either prompt_text or contents")
        contents = [{"role": "user", "parts": [{"text": prompt_text}]}]

    key = _api_key()
    url = f"{API_BASE}/{model}:generateContent?key={key}"

    generation_config: dict = {"temperature": temperature}
    if response_mime_type:
        generation_config["responseMimeType"] = response_mime_type
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    request_body: dict = {"contents": contents, "generationConfig": generation_config}
    if system_instruction:
        request_body["system_instruction"] = {"parts": [{"text": system_instruction}]}
    if tools:
        request_body["tools"] = tools

    body = json.dumps(request_body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Most common causes in practice: a bad/expired key (401/403), or
        # free-tier rate limits (429) -- Gemini's error body names which,
        # more useful here than the bare HTTP status.
        detail_body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail_body).get("error", {}).get("message", detail_body)
        except json.JSONDecodeError:
            detail = detail_body
        raise GeminiError(f"Gemini returned HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise GeminiError(f"couldn't reach Gemini API ({exc.reason})") from None
    except TimeoutError:
        raise GeminiError(f"Gemini did not respond within {timeout}s") from None
    except json.JSONDecodeError as exc:
        raise GeminiError(f"Gemini response wasn't valid JSON: {exc}") from None


def strip_markdown_fence(text: str) -> str:
    """`responseMimeType: application/json` doesn't guarantee an unfenced
    response in practice -- observed in manual testing (same as Haiku's
    occasional markdown fence noted in `claude_code/fallback.py`). Strips a
    single leading/trailing triple-backtick fence, with or without a
    language tag; a no-op if the text wasn't fenced.
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


def extract_text(envelope: dict) -> str:
    """Concatenates every text part of the envelope's first candidate,
    fence-stripped. Text can arrive split across multiple parts (observed
    when a tool call happens mid-response). Returns "" for a blocked or
    empty response (missing `candidates`) rather than raising -- that's a
    real, if unusual, outcome (a `promptFeedback.blockReason`), not a
    transport failure, so it's the caller's job to decide what "no text
    came back" means for its own schema, same as `claude_code/fallback.py`'s
    `parse_result()` treating a malformed `result` field as
    intent=answer=None rather than crashing.
    """
    candidates = envelope.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return strip_markdown_fence("".join(p.get("text", "") for p in parts))
