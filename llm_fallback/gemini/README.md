# llm_fallback/gemini — live, free-tier NO_MATCH fallback

A full-feature twin of `llm_fallback/claude_code/`, built on the Gemini
API's free tier instead of a `claude -p` subprocess. Same job as that
module and `llm_fallback/ollama/`: answer a NO_MATCH transcript with either
a re-checked command pick or a spoken answer, in one call, without
loosening "no LLM in the routing decision" (see `intent_routing.md`,
`routing/README.md`, and `claude_code/fallback.py`'s module docstring for
that reasoning in full).

**This is what `listener/vad_listener.py` actually calls today.**
`fallback_gemini.py`'s `attempt_fallback()` is imported directly by
`_handle_command()` (as of 2026-08-22, replacing `claude_code.fallback`'s
version) — full replace, not a Claude-first-then-Gemini chain: a Gemini
error/timeout degrades straight to today's plain "didn't catch that", same
as any other `attempt_fallback()` failure always has.
`llm_fallback/claude_code/` stays in the repo as a reference/rollback
path, but nothing in the live listener calls it anymore. Still genuinely
open from before this was wired in: no rate-limit backoff/retry, and no
systematic replay of `claude_code/logs/fallback.jsonl`'s 120 transcripts
through this path for an accuracy comparison — only a couple dozen manual
spot calls so far.

## Why this exists

`claude_code/logs/fallback.jsonl`'s real usage (120 calls, Aug 11–20) shows
two things worth fixing that have nothing to do with cost:

- **15% of calls (18/120) timed out** at 20s (`claude did not finish within
  20.0s`).
- **91% of every call's output tokens were unused extended-thinking
  tokens** (avg 601 of 657 output tokens) spent reasoning toward a one-line
  JSON classification.

Total spend over those 9 days was $1.41 and falling as `routing/`'s
embedding threshold gets tuned — cost was never the problem. Latency and
reliability are, and this prototype tests two independent fixes for that at
once: no CLI process to cold-start (a direct HTTPS call instead of
`subprocess.run(["claude", "-p", ...])`), and `thinkingConfig.thinkingBudget:
0` to stop paying for reasoning this call doesn't need.

## What's migrated (full parity with claude_code/, not the stripped-down
## ollama/ prototype)

- The same one-call, merged `{intent, slots, answer, urls, reason}` schema.
- **Search grounding** (`google_search` tool) — Gemini's equivalent of the
  cloud version's `WebSearch`, wired up and working, but **off by default**
  (`ENABLE_SEARCH = False`, `--search` to turn it on). See "Known issue:
  search grounding needs billing" below before turning it on.
- **Research tabs**: `open_research_tabs()` is imported directly from
  `claude_code.fallback` and reused as-is — same script, same trust split
  (the model returns an opinion, `open_research_tabs.ps1` independently
  re-validates every URL before it ever reaches `Start-Process`).
- **The safety contract**: `validate_pick()` is imported, not reimplemented
  — an intent pick from Gemini is re-checked against a fresh
  `routing/intents.yaml` load exactly the same way a Claude pick is, with
  the same `trust_intent_match=False` fail-closed behavior for slots.
- **Usage tracking**: `logs/fallback.jsonl` (one line per call) and
  `logs/usage_summary.json` (running total), same shapes as
  `claude_code/logs/`, with `latency_seconds` in place of `total_cost_usd`
  since the free tier has no dollar cost to track.

## What's genuinely different

- **Transport**: `urllib.request` (stdlib only, same reasoning
  `ollama/fallback_ollama.py` gives for not adding a dependency for one
  caller) directly against `generativelanguage.googleapis.com`, not a CLI
  subprocess.
- **Model**: `gemini-3.5-flash-lite` by default — Gemini's fastest/cheapest
  tier, chosen for the same reason `THINKING_BUDGET` is set as low as the
  API allows: this call is meant to be a fast classification, not a
  reasoning task. Confirmed by real testing: with that combination, real
  calls run in well under 1s with **zero** thinking tokens, against
  Haiku's measured 601 avg thinking tokens/call and 15% timeout rate (see
  "Why this exists" above). `--model` overrides freely to compare against
  `gemini-3.5-flash` or a later version — model names on the free tier
  move fast; `gemini-2.5-flash-lite` and `gemini-2.5-flash`, this
  prototype's original guesses, were both already retired for new users as
  of 2026-08-20, redirecting to the `3.x` line.
- **Structured output isn't strictly schema-enforced**. Gemini's
  `responseSchema` (forced JSON schema) is not requested here — this leans
  on `responseMimeType: application/json` plus the prompt's explicit
  schema instruction, with the same tolerant parsing
  (`_strip_markdown_fence()` + `parse_result()`'s malformed-input handling)
  the cloud version already needs for Haiku's occasional markdown fence.
  Worth trying `responseSchema` directly in a future pass, now that search
  grounding (the thing that made combining the two unreliable) is off by
  default anyway.
- **Grounding URLs preferred over the model's own `urls` field**:
  `_grounding_urls()` pulls real, already-fetched result URLs out of
  `groundingMetadata.groundingChunks` when the search tool actually ran,
  falling back to the model's self-reported `urls` only when grounding
  metadata is absent. Same "don't trust the model's own claim" instinct
  `validate_pick()` applies to an intent pick, extended to URLs.

## Setup

1. Get a free API key at <https://aistudio.google.com/apikey> — no billing
   account required for the free tier.
2. Set it, either way:

   - **`.env` file (recommended)**: copy `.env.example` (project root) to
     `.env` in the project root and fill in `GEMINI_API_KEY=<your key>`.
     `fallback_gemini.py` loads it automatically at import time
     (`_load_dotenv()`, stdlib-only parsing, no `python-dotenv` dependency
     added for one caller). `.env` is listed in the root `.gitignore` —
     never committed.
   - **Environment variable**, if you'd rather not have the key on disk at
     all:

     ```powershell
     $env:GEMINI_API_KEY = "<your key>"      # current session
     setx GEMINI_API_KEY "<your key>"        # persists across new shells
     ```

     An explicit environment variable always wins over `.env` if both are
     set.

## Usage

```
# Compare against the cloud version on the same transcript
python llm_fallback/gemini/fallback_gemini.py      "open the task thing"
python llm_fallback/claude_code/fallback.py        "open the task thing"

# See the exact request without calling the API (no key needed)
python llm_fallback/gemini/fallback_gemini.py --dry-run "tell me a joke"

# Try a different model
python llm_fallback/gemini/fallback_gemini.py --model gemini-3.5-flash "tell me a joke"

# Turn on search grounding (needs a billing account linked -- see Known
# issue below; every other example above runs with it off)
python llm_fallback/gemini/fallback_gemini.py --search "what's today's weather"

# Running token/latency totals so far (mirrors --usage on both other
# fallback directories; latency instead of dollars, same reason ollama/'s
# --usage tracks duration instead of cost)
python llm_fallback/gemini/fallback_gemini.py --usage
```

## Known issue: search grounding needs a billing account

Confirmed against the live API on 2026-08-20: the `google_search`
grounding tool has **zero quota on a pure free-tier (no-billing) key**. A
plain `generateContent` call succeeds; the identical call with
`tools: [{"google_search": {}}]` attached gets an immediate `429
RESOURCE_EXHAUSTED`, even as the very first call of a session — this is
not a rate limit that clears on retry, the free tier simply doesn't
include it. `ENABLE_SEARCH` defaults to `False` (and `--search` is opt-in
on the CLI) specifically so this doesn't silently degrade every open-ended
answer's quality — with search off, `SYSTEM_PROMPT.md` and `build_prompt()`
both tell the model plainly that it has no search tool for that call, so
it says "I don't have that" for anything needing live/current data instead
of quietly making something up.

To actually use grounding: link a billing account to the Google Cloud
project behind `GEMINI_API_KEY` at <https://aistudio.google.com/apikey>,
re-verify quota at <https://ai.google.dev/gemini-api/docs/rate-limits>,
then pass `--search` (or `enable_search=True`).

Two other things found while getting a first real call working, worth
knowing if this drifts again:
- `gemini-2.5-flash-lite` and `gemini-2.5-flash` — this prototype's
  original model guesses — were both already retired for new users as of
  2026-08-20 (`404`, redirecting to `gemini-3.5-flash-lite` /
  `gemini-3.6-flash`). `DEFAULT_MODEL` now points at `gemini-3.5-flash-lite`.
- `thinkingConfig.thinkingBudget: 0` (the documented "off" value for
  Gemini's 2.5-generation models) is rejected outright by
  `gemini-3.5-flash-lite` with a bare `400 INVALID_ARGUMENT`. `1` is the
  lowest value the API accepts, and was observed producing zero thinking
  tokens in practice (`thoughtsTokenCount` absent from `usageMetadata`
  entirely) — `THINKING_BUDGET` is set to `1`, not `0`.

## Free tier limits

Gemini's free (no-billing-account) tier enforces per-minute and per-day
request caps that vary by model and change over time — check current
numbers at <https://ai.google.dev/gemini-api/docs/rate-limits> before
relying on this for anything beyond manual benchmarking. For scale: peak
real usage on the Claude path was 35 calls in a single day (Aug 11); the
current run rate is 1–2 calls/day as `routing/`'s embedding threshold has
improved. Free-tier daily quotas comfortably cover both.

## Privacy

**This is the one place this prototype is not a drop-in equivalent of the
cloud version, and it's worth reading before wiring anything in.**
`readme.md` and `claude_code/fallback.py`'s own module docstring describe
this project's transcripts as data that "never leaves this machine except
as the call itself" — true for the Claude path specifically, because
Anthropic's API does not train on API data by default. Gemini's *free*
tier (no billing account attached) does not carry the same guarantee:
Google's terms for the unpaid API tier permit using inputs to improve
Google's products, unlike Gemini's paid tier. These are things said out
loud in the user's home, captured here specifically because they failed
the deterministic matcher (so they skew toward unusual or ambiguous
phrasing) — that tradeoff should be a deliberate choice before this
prototype becomes anything more than a benchmarking exercise, not
something that rides in silently because the free tier was convenient.

Otherwise, same handling as both other fallback directories:
`logs/fallback.jsonl` is a local, plaintext, append-only record of things
said out loud that failed the deterministic matcher (plus what was
answered), and it never leaves this machine except as the API call itself.

## What's not done here

- No systematic replay of `claude_code/logs/fallback.jsonl`'s 120 real
  transcripts through this path for an accuracy comparison — this was
  wired into the live listener off a couple dozen manual spot calls, not
  a full side-by-side benchmark. Worth doing retroactively.
- No calibration of `DEFAULT_TIMEOUT`, `THINKING_BUDGET`, or the `0.2`
  temperature in `client.generate_content()` — all guessed starting
  points, same uncalibrated-default caveat every other fallback
  directory's own defaults carry.
- No handling of Gemini's free-tier rate limits beyond surfacing a `429`'s
  error message through `GeminiFallbackError` — no backoff/retry, since a
  single-user voice assistant's call pattern is not expected to need one,
  but that's an assumption, not something tested under real quota
  pressure.

## client.py — shared transport, also used by scripts/daily_briefing.py

`client.py` holds the auth (.env + env var), the raw `generateContent`
HTTP call, and the shared model defaults (`DEFAULT_MODEL`,
`THINKING_BUDGET`) -- split out of `fallback_gemini.py` so
`scripts/daily_briefing.py`'s news-narration and stock-commentary
summarization calls (previously `claude -p`) could reuse the same
auth/transport without importing this module's intent-routing-specific
machinery (`FallbackOutcome`, `validate_pick()`, the NO_MATCH schema),
which those calls have no use for -- they only ever produce
spoken/displayed text, never an `IntentBundle`. `fallback_gemini.py`'s
`run_gemini()` is now a thin wrapper over `client.generate_content()` that
fixes the intent-routing-specific choices (`SYSTEM_PROMPT.md` as
`system_instruction`, the optional `google_search` tool);
`GeminiFallbackError` is kept as a plain alias of `client.GeminiError` so
existing `except` clauses didn't need renaming.
