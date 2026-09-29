# llm_fallback — Stage 3.5, one constrained pass on NO_MATCH, any provider

`routing/` decides most commands deterministically: embedding similarity
against a closed intent set, no LLM in that decision (see
`intent_routing.md` and `routing/README.md`). This module is what runs
*after* that decision comes back `NO_MATCH`, before EKKO gives up and says
"Sorry, I didn't catch that."

## Why this exists

Whisper's phrasing variance or an unlucky embedding score can sink a command
a human would recognise instantly. Rather than widen the embedding
threshold (which trades false negatives for false accepts across every
intent), this asks a model whether the transcript plausibly means one of the
same known intents and, if not, whether it's a genuine open-ended question
worth just answering — and only runs a command if a non-LLM check
independently confirms the pick against the real `routing/intents.yaml`.

This is **not** a loosening of "no LLM in the routing decision." The model
here can only narrow among options a human already vetted and wrote down in
`routing/intents.yaml` — it cannot invent an intent, and every pick is
re-validated against a fresh load of that file (`brain/validate.py`'s
`validate_pick()`) before it's allowed anywhere near `routing/execute.py`.
That's the same safety property the embedding matcher already has, just
applied a second time with a fuzzier front end. An open-ended answer, the
other possible outcome, never produces an `IntentBundle` at all, so it has no
path to `execute()` regardless of what the transcript asks for.

## One shared brain, any provider

```
llm_fallback/
  brain/            # provider-agnostic: schema, validate_pick(), failover.py, domain/
                     # memory/short_memory/research wiring, logging,
                     # attempt_fallback() itself
  BASE_CONTRACT.md   # the shared prompt contract every provider gets
  <provider>_delta.md   # only genuine, tested differences in how one
                         # model needs to be told something -- optional,
                         # near-empty by default
  gemini/provider.py       # ~80 lines: Gemini API transport only
  claude_code/provider.py  # ~110 lines: `claude -p` subprocess transport only
  ollama/provider.py       # ~110 lines: local Ollama HTTP transport only
  deepseek/provider.py     # DeepSeek API transport + $ spend cap
  claude_api/provider.py   # Anthropic Messages API (`anthropic` SDK) + $ spend cap
  openai/provider.py       # OpenAI chat completions + $ spend cap
  logs/<provider>/         # one fallback.jsonl + usage_summary.json per provider
```

A provider directory holds nothing but transport: given a fully-built
prompt and system instruction, make the call, return raw text plus
usage/latency. It never touches JSON parsing, `validate_pick()`, domain
composition, memory, research, or logging — `brain/core.py`'s
`attempt_fallback()` does all of that once, uniformly, regardless of which
provider answered. See `brain/providers.py`'s `Provider` Protocol.

**Adding a new provider (OpenAI, DeepSeek, ...):**
1. Write `llm_fallback/<name>/provider.py` implementing `Provider`
   (`call()` + `capability_note()`, same shape as `gemini/provider.py`;
   raise `ProviderError` on any transport failure, and use
   `brain/transport.py` for a plain HTTP JSON API).
2. Call `register_provider(YourProvider())` at that module's import time.
3. Add its row to `llm_fallback/catalog.py`. That one list feeds the
   settings schema, the API keys endpoint, the `--provider` flag and the
   Models panel.
4. Add its usage fields to `_USAGE_FIELDS` in `backend/routers/overview.py`.
5. Only write `<name>_delta.md` if real testing shows that model needs
   different prompt wording than `BASE_CONTRACT.md` already gives every
   provider — most providers need none.

`tests/test_catalog.py` fails if steps 2-4 are inconsistent.

## One call, not two

Every outcome — a re-checked command pick, a spoken answer to an
open-ended question, a decline, or noise — comes from a **single** call
per NO_MATCH transcript, regardless of provider. An open-ended answer can
also carry up to 3 URLs the model found via search, when a search
capability was available for that call; see "Research tabs" below.

```json
{"intent": "<name_or_null>", "slots": {}, "answer": "<text_or_null>", "urls": [],
 "follow_up": "<grounded_next_step_or_null>",
 "memory_candidate": {"text": "...", "category": "...", "confidence": "..."} | null,
 "reason": "<short>"}
```

`intent` set (and re-validated) means a command; `answer` set means a spoken
reply; both null means neither applied (noise, an unclear fragment) and EKKO
falls back to "didn't catch that." Exactly one of the two may be non-null.
See `BASE_CONTRACT.md` for the full decision rule, and `brain/prompt.py`/
`brain/parsing.py` for how the per-call prompt is built and the reply parsed.

Every call's usage is tracked regardless of outcome, including a
malformed/error response, since real tokens (or real seconds) were spent
producing it either way — see `brain/logging.py`.

## Provider selection

`listener/vad_listener.py` and `chatbot/chat_listener.py` both take a
`--provider {gemini,claude_code,ollama,deepseek,claude_api,openai}` CLI flag (default `gemini`, or
`$EKKO_LLM_PROVIDER` when set). `--provider` picks where the chain *starts*; the order itself is
`EKKO_LLM_FAILOVER_ORDER` in `.env`, a comma-separated list (default
`gemini,deepseek,claude_api,openai,claude_code,ollama`). Leave a provider out
of the list to never fail over to it; unknown names are ignored with a
warning. On a transport failure (a 503,
a timeout, unreachable, missing key, DeepSeek's spend cap) `brain/failover.py`'s
`attempt_with_failover()` - what both listeners call - hands the same
transcript to the next provider in that order and returns one ordinary
`FallbackOutcome`. `--provider ollama` starts at ollama, so with the default
order it runs alone; a provider not in the list also runs alone.

- An outcome with no error but no answer and no command is genuine noise and
  is **not** retried elsewhere.
- A provider that just failed is skipped for `EKKO_LLM_FAILOVER_COOLDOWN_S`
  seconds (default 120), so a Gemini outage costs one slow call, not one per
  command. It is retried automatically afterwards.
- Every hop is printed and appended to `logs/failover.jsonl` (`handoff`,
  `skipped`, `recovered`, `exhausted` events, with the reason). Each attempt is
  still also logged in its own `logs/<provider>/fallback.jsonl`. If every
  provider fails, the outcome's `error` lists each provider's reason.
- The domain system instruction is rebuilt per provider, so a fallback model
  gets its own delta, not Gemini's; `model` is only applied to the first hop.
- `EKKO_LLM_FAILOVER=0` disables failover entirely (manual selection only).
- Worst case, every provider timing out is roughly the sum of their timeouts.

- `gemini` — cloud API, free tier, no subscription needed. See
  `llm_fallback/gemini/client.py`'s module docstring for the current
  default model and known free-tier quirks (grounding tool quota,
  `thinkingConfig` behavior) — these move fast on Google's side, so
  re-verify before trusting a stale comment.
- `claude_code` — spawns a `claude -p` subprocess; needs the `claude` CLI
  on `PATH` and an active Claude subscription to authenticate.
- `ollama` — local model via a running `ollama serve` on this machine;
  needs the configured model already pulled (`ollama pull
  qwen2.5:7b-instruct-q4_K_M` by default, see `ollama/provider.py`).

- `deepseek`, `claude_api`, `openai` - pay-as-you-go cloud APIs, keys in
  `.env` (`DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). Models:
  `deepseek-v4-flash`, `claude-haiku-4-5` (`EKKO_CLAUDE_API_MODEL`),
  `gpt-4.1-mini` (`EKKO_OPENAI_MODEL`). `claude_api` is the Anthropic API via
  the `anthropic` package and is separate from `claude_code`, which uses the
  `claude` CLI on a subscription. Each provider computes its call's cost from
  token usage (DeepSeek at peak rates, a deliberate over-estimate; a model
  missing from a provider's `_PRICING` table is priced high, not free) and
  reports it in `extras.cost_usd`, which sums into
  `logs/<provider>/usage_summary.json`. `brain/budget.py` refuses to start a
  call once that total reaches the provider's cap (`EKKO_DEEPSEEK_BUDGET_USD`,
  `EKKO_CLAUDE_API_BUDGET_USD`, `EKKO_OPENAI_BUDGET_USD`, default `2.0` each),
  raising `ProviderBlocked`, which `brain/core.py` turns into a spoken answer
  saying the provider is paused - no request is sent. A call already in flight
  can't be cut off, but `max_tokens` bounds what it can add. To resume, raise
  the cap or reset that usage summary.

## Research tabs

An open-ended `answer` can carry up to 3 `urls` a provider's own
search/grounding mechanism (or EKKO's own live-web research pipeline,
`control_center/hands_on/research`) found. `brain/research.py`'s
`open_research_tabs()` (shared across every provider) opens a Brave Search
tab for the transcript plus those curated URLs via
`scripts/open_research_tabs.ps1`. Best-effort and non-blocking: a missing
Brave install, a script error, or a timeout is logged and otherwise
ignored, never affects what gets spoken. `--no-research-tabs` disables
both the tabs and the spoken aside that follows a successful launch.

`brain/research.py`'s `run_research()`/`wants_research()` is EKKO's own
live-web research (page fetch + read, not a provider's search tool) —
gated by `EKKO_RESEARCH=0` to disable, and by a domain's
`domains/<key>/research.yaml` (finance's ticker/news/filing lookup, today)
or a generic current-events phrase match otherwise.

## Privacy

Like `routing/logs/routing.jsonl`, each provider's `logs/<provider>/
fallback.jsonl` is a local, plaintext, append-only record of things said
out loud that failed the deterministic matcher (and whatever they were
answered with), plus what was spent on each call. It never leaves this
machine except as that provider's own call.

## A different kind of caller: raw completions

`scripts/daily_briefing.py` calls `llm_fallback/gemini/client.py`'s
`generate_content()`/`extract_text()` directly for plain text
summarization — no intent schema, no `FallbackOutcome`, none of the
machinery `brain/` centralizes. That's intentional: a caller that just
wants raw text back from a specific provider, with no intent-routing
contract, should keep talking to that provider's own `client.py`/thin
transport directly rather than going through `attempt_fallback()`. If a
second such caller appears, that's the trigger to extract a shared
"just give me text" helper — until then, this is the correct minimal-
surface choice for that kind of call.
