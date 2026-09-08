# memory/

Persistent, user-editable memory for EKKO's conversational (Gemini-fallback)
layer. Greenfield as of this writing -- there was no memory system of any
kind in this codebase before (`transcripts.jsonl`/`routing.jsonl`/
`fallback.jsonl` are write-only logs, never read back).

## What's here

- `MEMORY.md` -- the actual store. Plain markdown, sectioned (`Identity`,
  `Preferences`, `Active Projects`, `Recent Events`, `Known Facts`,
  `Open Threads`), dated bullet lines. Open and edit it directly any time;
  `store.read_memory()` is tolerant of hand edits and a missing/malformed
  file (degrades to empty, never raises).
- `schema.py` -- types only, no I/O: `MemoryBundle`, `MemoryCandidate`,
  `ScoredDecision`.
- `store.py` -- `read_memory()` / `write_memory()`. Read-modify-write,
  atomic (`.tmp` + `os.replace`).
- `scoring.py` -- `propose_and_score()`, the write policy: explicit
  remember-request > correction/stated preference > fact repeated across
  two sightings > tied to an active project > reject (transient one-off).
  Never trusts a candidate's self-reported confidence alone -- mirrors
  `llm_fallback/claude_code/fallback.py`'s `validate_pick()` "never trust
  the model blindly" pattern, to the extent that's possible for free text
  instead of a closed vocabulary.
- `logs/decisions.jsonl` -- one JSON object per `propose_and_score()` call,
  accepted or rejected, written by `store.log_decision()` from the same
  call site in `listener/vad_listener.py` that calls `write_memory()`. This
  is the answer to "is memory actually being written": every candidate
  Gemini ever proposed, and why it was or wasn't promoted into `MEMORY.md`,
  not just whatever happened to still be on screen in `print()` output.
  Tailed live (tag `MEMORY`) by `system/dev_tail.py`. The sibling domain
  version of this is `routing/logs/domains.jsonl` (see `routing/domains.py`'s
  `log_match()`) -- same question, for domain attribution instead of memory.

## How a candidate gets here

`llm_fallback/gemini/fallback_gemini.py`'s JSON contract carries an
optional `memory_candidate` field the model may populate. That candidate
is **not** written anywhere automatically -- `propose_and_score()` decides
accept/reject/section first, and only an accepted decision reaches
`write_memory()`. See that module's `SYSTEM_PROMPT.md` for what the model
is told about when to populate this field (rarely -- most turns should
leave it null).

## Privacy, read this before assuming this data stays local

`llm_fallback/gemini/README.md` already documents that Gemini's free
(no-billing) tier does **not** carry Anthropic's no-training-on-API-data
guarantee -- inputs may be used to improve Google's products. That caveat
now applies transitively to this file, not just to one-off transcripts:
`MemoryBundle.as_prompt_block()` is injected back into a future
`system_instruction`, so anything written here is sent to Gemini again on
a later call, in addition to having been sent once when it was first
proposed. If that's not an acceptable tradeoff for a given fact, don't
say "remember that" about it -- or edit it out of `MEMORY.md` directly,
since it's a plain text file.

## Open design question (flagged, not decided)

Right now `as_prompt_block()` includes memory content regardless of which
domain (if any) is active for a given fallback call -- a fact noticed
during a finance conversation can surface as context on an unrelated later
question. This is deliberately simple for the first build, capped in size
per section rather than filtered by domain. Revisit if that turns out to
leak framing across topics in a way that reads as odd rather than helpful.
