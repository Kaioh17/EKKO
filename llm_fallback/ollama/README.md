# llm_fallback/ollama — prototype, not wired up

A local-model twin of `llm_fallback/claude_code/`, built to answer one
question: does running the NO_MATCH fallback on this laptop's own GPU
(via [Ollama](https://ollama.com)) answer meaningfully faster than a
`claude -p` subprocess plus a network round trip to Anthropic's API?

**This is a standalone prototype.** `fallback_ollama.py` is not imported by
`listener/vad_listener.py` or anything else in the repo — the only caller is
its own `__main__` block, run by hand from a terminal. `attempt_fallback` in
`llm_fallback/claude_code/fallback.py` (the one thing `vad_listener.py`
actually calls, see its `_handle_command()`) is untouched. Wiring this in —
picking local vs. cloud per call, or trying local first and falling back to
cloud — is a later decision, made after there's real benchmark data from
this prototype to make it with, not before.

## Why local might help here specifically

The cloud fallback's latency has two parts that have nothing to do with
model quality: a `claude` CLI process cold-starts on every call, and the
request round-trips to Anthropic's API over the network. Both disappear
with Ollama — it runs as a persistent local server (already running on this
machine at `localhost:11434`) with the model resident in VRAM, so a call is
one local HTTP request to an already-warm process. For the kind of output
this step produces (a small JSON object, or one or two spoken sentences),
that's most of the latency budget.

The tradeoff is quality, not speed: a 3B–8B local model will follow the
strict "exactly one of intent/answer, spelled exactly as given" schema less
reliably than Haiku, and is more prone to being talked out of its
instructions by an adversarial transcript. `validate_pick()` (imported from
`claude_code/fallback.py`, not reimplemented — see `fallback_ollama.py`'s
module docstring) is what keeps that safe either way: a hallucinated or
injected intent name is rejected against a fresh `routing/intents.yaml`
load regardless of which model proposed it, same as the cloud path.

## This machine's hardware

- CPU: Intel Core Ultra 9 185H
- GPU: NVIDIA RTX 4070 Laptop (8GB VRAM)
- RAM: 16GB

Comfortably fits a 3B–8B parameter model at 4-bit quantization resident in
VRAM. That's the ceiling this prototype is scoped to — nothing here has
been tried against a model too big to fit, and a model that spills out of
VRAM into system RAM would likely erase the speed advantage this is meant
to test.

## Setup

Ollama is already installed and running on this machine (`ollama serve` is
up at `localhost:11434`).

```
ollama pull qwen2.5:7b-instruct-q4_K_M   # default in fallback_ollama.py -- see "Known issue" below for why
```

`llama3.2:3b` and `phi4-mini` were tried first as faster, smaller options
but ruled out — see "Known issue found in manual testing" below. Pull them
too only if you want to reproduce that comparison yourself:

```
ollama pull llama3.2:3b
ollama pull phi4-mini
```

## Usage

```
# Compare against a known NO_MATCH-worthy transcript
python llm_fallback/ollama/fallback_ollama.py "open the task thing"
python llm_fallback/claude_code/fallback.py    "open the task thing"

# See the exact request without calling Ollama
python llm_fallback/ollama/fallback_ollama.py --dry-run "tell me a joke"

# Try a different pulled model
python llm_fallback/ollama/fallback_ollama.py --model llama3.2:3b "tell me a joke"

# Running duration/token totals so far (mirrors --usage on the cloud version,
# duration instead of dollars -- there's no API cost for a local model)
python llm_fallback/ollama/fallback_ollama.py --usage
```

Each run prints both the server-reported inference time (`total_duration`
from Ollama's response envelope) and this process's own wall-clock time
(which also includes Python/import startup) — the two numbers to compare
against the cloud fallback's `total_cost_usd`/latency when deciding whether
this is worth wiring up for real.

## Logs

Same shape as `claude_code/logs/`, kept separate:
- `logs/fallback.jsonl` — one line per call: transcript, model, raw pick,
  validated bundle or answer, token counts, duration, any error. Same
  privacy note as the cloud version applies: this is a local, plaintext
  record of things said out loud, and it never leaves this machine.
- `logs/usage_summary.json` — running total (calls, duration, tokens),
  updated after every call, read by `--usage`.

## What's not done here

- No tool support (the cloud fallback's `WebSearch` has no local
  equivalent used here — see `SYSTEM_PROMPT.md`, which tells the model to
  say plainly it doesn't know rather than guess at anything current).
- No calibration of `DEFAULT_MODEL`, `DEFAULT_TIMEOUT`, or the `0.2`
  temperature in `run_ollama()` — all guessed starting points, same
  uncalibrated-default caveat the cloud version's `DEFAULT_TIMEOUT` carries.
- No wiring into `vad_listener.py`, no local/cloud routing decision, no
  answer to "is this actually faster/good enough in practice" yet — that's
  what running this side by side with the cloud fallback is for.

## Known issue found in manual testing: small models default to refusal

`llama3.2:3b` (3B) and `phi4-mini` (3.8B) reliably re-match a paraphrased
command (`open the task thing` -> `open_task_manager`), but were unreliable
at the second half of this module's job — genuinely answering an
open-ended request. `SYSTEM_PROMPT.md` was tightened to explicitly demand
an attempt ("tell the joke, do the arithmetic") rather than accepting a
hedge like "can you rephrase?", but that alone didn't fix it on either
small model: `llama3.2:3b` started guessing `open_app`/`chrome` for plain
arithmetic it previously left alone, and `phi4-mini` guessed `web_search`
for "tell me a joke" with a query that didn't correspond to anything in
the real transcript. In both cases these were wrong picks from a model too
small to reliably arbitrate "is this a command or a question," not wrong
prompt wording.

**Confirmed**: `qwen2.5:7b-instruct-q4_K_M` does not have this problem.
Same three transcripts, all three correct: "tell me a joke" got an actual
joke (`intent: null`, real `answer`), "what is 2 plus 2" got `The answer is
4.` instead of a hallucinated `open_app` guess, and "open the task thing"
still re-matched `open_task_manager` correctly. Warm-call latency was
~1.5s server-side, comparable to the smaller models. The only cost is a
one-time ~33s cold-load the first time it's called after being pulled or
after Ollama's idle unload (see `DEFAULT_KEEP_ALIVE`) — consistent with
the load-time pattern already documented above for the 3B model. On this
machine's hardware, 7B-class instruct models look like the realistic floor
for this module's dual job, not the 3-4B class.

This surfaced a second, more important finding, now fixed independently of
model choice: `phi4-mini`'s wrong `web_search` guess above **validated as a
real, executable bundle** — `validate_pick()` filled the free_text `query`
slot with the whole transcript ("tell me a joke") via
`find_free_text()`'s fallback for "no trigger phrase found," a behavior
that's only sound when `intent` came from a real embedding score (the
deterministic path) rather than an unverified model guess (this path).
`routing/slots.py`'s `find_free_text()` now takes a
`trust_intent_match` flag (default `True`, unchanged for the deterministic
path), and `claude_code/fallback.py`'s `validate_pick()` — imported and
shared by both fallback directories — passes `trust_intent_match=False`,
so "no trigger phrase found" is MISSING_SLOT (fail closed) for any
LLM-proposed pick, cloud or local. Confirmed by direct test: the exact
`phi4-mini` pick above now returns `None` instead of a matched bundle, a
real trigger-backed web_search pick still validates, and the deterministic
path's own fallback behavior is untouched.
