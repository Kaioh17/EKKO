# llm_fallback — Stage 3.5, one constrained pass on NO_MATCH

`routing/` decides most commands deterministically: embedding similarity
against a closed intent set, no LLM in that decision (see `intent_routing.md`
and `routing/README.md`). This module is what runs *after* that decision
comes back `NO_MATCH`, before EKKO gives up and says "Sorry, I didn't catch
that."

## Why this exists

Whisper's phrasing variance or an unlucky embedding score can sink a command
that a human would recognise instantly. Rather than widen the embedding
threshold (which trades false negatives for false accepts across every
intent), this asks a model whether the transcript plausibly means one of the
same known intents and, if not, whether it's a genuine open-ended question
worth just answering — and only runs a command if a non-LLM check
independently confirms the pick against the real `routing/intents.yaml`.

This is **not** a loosening of "no LLM in the routing decision." The model
here can only narrow among options a human already vetted and wrote down in
`routing/intents.yaml` — it cannot invent an intent, and every pick is
re-validated against a fresh load of that file (see `claude_code/fallback.py`'s
`validate_pick()`) before it's allowed anywhere near `routing/execute.py`.
That's the same safety property the embedding matcher already has, just
applied a second time with a fuzzier front end. An open-ended answer, the
other possible outcome, never produces an `IntentBundle` at all, so it has no
path to `execute()` regardless of what the transcript asks for.

## One call, not two

Both outcomes — a re-checked command pick, or a spoken answer to an
open-ended question — come from a **single** `claude -p` call per NO_MATCH
transcript, on Haiku. An open-ended answer can also carry up to 3 URLs the
model found via its `WebSearch` tool while researching it; see "Research
tabs" below.

```
claude -p "<prompt>" --model haiku --allowedTools "WebSearch" \
    --permission-mode dontAsk --output-format json
```

always with `cwd=llm_fallback/claude_code/` and `--allowedTools "WebSearch"`
as the *only* tool granted — no file, shell, or execution tool is ever on the
list. `claude_code/CLAUDE.md` is the only project context that session gets;
it never sees `readme.md` or anything else in the repo, and it's told
explicitly that it can't read, write, or run anything outside a JSON
response.

This used to be two sequential calls: a Haiku re-check first, then, only if
that came back empty, a second call on the default model to answer. Real
usage showed that was the dominant source of latency on exactly the turns
most worth a fast reply — two full `claude` process startups and prompt-cache
round trips back to back before EKKO said anything at all. Merging them into
one call with one combined response schema fixed that directly:

```json
{"intent": "<name_or_null>", "slots": {}, "answer": "<text_or_null>", "urls": [], "reason": "<short>"}
```

`intent` set (and re-validated) means a command; `answer` set means a spoken
reply; both null means neither applied (noise, an unclear fragment) and EKKO
falls back to "didn't catch that." Exactly one of the two may be non-null.
See `claude_code/CLAUDE.md` for the full decision rule Claude follows, and
`claude_code/fallback.py`'s `build_prompt()`/`parse_result()` for how it's
built and parsed on this side.

`--output-format json` is also how token/cost spend gets tracked: every
call's `usage` and `total_cost_usd` (see the `claude -p` JSON envelope) is
appended to `claude_code/logs/fallback.jsonl`, whether or not anything
validated or answered — including a `claude` `is_error: true` response,
which still spent real tokens producing it and isn't excluded from the
count just because it wasn't usable.

`claude_code/logs/usage_summary.json` is the running total on top of that
per-call log: total calls, total cost, and total tokens by kind
(input/output/cache-write/cache-read), updated after every single call so
"how much has this cost so far" never means re-summing the whole JSONL log
by hand. `python llm_fallback/claude_code/fallback.py --usage` prints it.

## Integration

**As of 2026-08-22, `listener/vad_listener.py`'s `_handle_command()` calls
`llm_fallback/gemini/fallback_gemini.py`'s `attempt_fallback()`, not this
directory's.** The rest of this file (written before that move) still
describes the design accurately -- one merged call, the same validation
contract, the same integration shape -- just against Claude specifically;
see `gemini/README.md` for why Gemini replaced it (real usage here showed
the `claude` CLI's cold-start and Haiku's extended-thinking token spend
were the actual latency source) and what's genuinely different about that
path (no `WebSearch` equivalent enabled by default -- see its "Known
issue" section). This directory (`claude_code/`) stays in the repo as a
reference/rollback path; nothing in the live listener calls it directly
anymore except `open_research_tabs()`, which both fallback directories
share (see `gemini/fallback_gemini.py`'s own import of it).

Called from `_handle_command()` only when `routing/route.py` returns
`RoutingStatus.NO_MATCH`. A validated command pick becomes an ordinary
`MATCHED` `IntentBundle` and flows through the existing `execute()` →
`response_key()` → `feedback.speech.say()` path exactly like a direct
embedding match. An answer is spoken directly via `feedback.speech`'s
support for arbitrary text (the same `text_override` mechanism
`routing/execute.py`'s `spoken_override()` uses for `system_diagnosis.ps1`)
and the turn ends there — `bundle` never becomes anything but `NO_MATCH` in
that case, and no handler ever runs. If neither applies, or the fallback
call fails outright, today's `"not_understood"` speech fires exactly as it
did before this module existed. `--no-llm-fallback` on `vad_listener.py`
disables the whole step, regardless of which fallback directory is wired
in.

## Research tabs

An open-ended `answer` can carry up to 3 `urls` the model found via its
one allowed tool, `WebSearch`, while researching it — see `CLAUDE.md`'s
`urls` rule for when it's expected to fill this in (a factual or "show me
X" question with a real page worth reading) versus leave it empty (a
joke, anything answered from general knowledge without searching).

`vad_listener.py`'s `_handle_command()` calls `fallback.open_research_tabs()`
right after speaking the answer: it opens a Brave Search tab for the
transcript plus those curated URLs, via `scripts/open_research_tabs.ps1`.
Same trust split as everywhere else in this module — the model only ever
returns an opinion about which URLs are worth opening, `open_research_tabs()`
(trusted code, not the sandboxed `claude` call) decides to act on it, and
the script itself independently re-validates every URL as absolute
http/https before anything reaches `Start-Process`, the same way
`validate_pick()` never trusts a proposed intent without re-checking it
against the real config. Best-effort and non-blocking: a missing Brave
install, a script error, or a `powershell` timeout is logged and otherwise
ignored, never affects what gets spoken.

Only once Brave actually launched, EKKO follows the spoken answer with a
short aside from `feedback.speech.RESPONSES`' `research_tabs_opened` key
(e.g. "I'm also pulling up some helpful sites for you.") -- skipped
entirely on a missing-Brave or script-error outcome, so it never claims a
tab opened that didn't. `--no-research-tabs` on `vad_listener.py` disables
both the tabs and that aside, keeping just the spoken answer;
`--no-llm-fallback` disables all of it, same as today.

## Privacy

Like `routing/logs/routing.jsonl`, `claude_code/logs/fallback.jsonl` is a
local, plaintext, append-only record of things said out loud that failed the
deterministic matcher (and whatever they were answered with), plus what was
spent on each call. It never leaves this machine except as the `claude -p`
call itself.

## ollama/ — local-model prototype, not wired up

`ollama/fallback_ollama.py` is a standalone prototype exploring whether a
model running locally via Ollama answers a NO_MATCH transcript faster than
a cloud API call, by skipping the network round trip entirely. Benchmarked
for real against both cloud paths (see its README for numbers): warm
Ollama calls (`qwen2.5:7b`) land at 1.65–2.1s server-side, slower than
Gemini's ~0.7–0.9s but far ahead of the old `claude -p` CLI path's
15-20s+. It reuses this module's `validate_pick()` and parsing helpers
rather than reimplementing the safety contract, but nothing calls it —
`vad_listener.py` calls `gemini.fallback_gemini.attempt_fallback()` (see
"Integration" above). See `ollama/README.md` for setup, usage, and what's
still unproven.
