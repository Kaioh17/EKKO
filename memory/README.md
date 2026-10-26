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
  `llm_fallback/brain/validate.py`'s `validate_pick()` "never trust
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

## Conversation memory: 24h day log, daily spin cycle, archive

Experimental. Replaces "the last few follow-up turns" (`short_term.json`)
as the LLM's conversational context. Goal: the model always sees what was
said recently, and past days cost almost nothing until they're needed, so
the context window is never the limit.

```
every exchange ──► day_log/YYYY-MM-DD.jsonl ──(24h)──► spin_cycle ──► archive.json
 (voice + chat,      whole last 24h goes into          1 LLM call/day   "date | short summary"
  commands too)      every prompt (token-capped)                         -> {summary, topics, details}
                                                                              ▲
 prompt = conversation_24h + archive_index (keys only) ── model replies recall:[key] ──┘
                                      one extra call, value attached as recalled_archive
```

- `day_log.py` -- `record_turn()` appends one JSON line per exchange to the
  file for that local date; `recent_turns()` reads the last 24h;
  `render_block()` turns them into the prompt text under a token budget
  (`TOKEN_BUDGET`, chars/4 estimate). Over budget, the **oldest turns are
  dropped from the prompt only** -- disk is untouched -- and the count is
  logged as `truncated_turns`. Recorded from `listener/vad_listener.py` and
  `chatbot/chat_listener.py` for *every* turn: LLM answers, matched
  commands, and unrecognized input. No `follow_up` gate, no session gate.
- `archive.py` -- `archive.json`: key = `YYYY-MM-DD | <=10-word summary`,
  value = `{summary, topics, turns, details}`. `index_keys()` gives the
  newest `KEY_CAP` keys (sent with every prompt, ~20 tokens each);
  `fetch()` returns the full entries for the keys the model asks for,
  capped at `FETCH_TOKEN_CAP`. A key matches by its date prefix, so the
  model only has to get the date right.
- `spin_cycle.py` -- for each date file whose newest entry is >24h old:
  summarize (`llm_fallback/brain/summarize.py`, same provider order and
  spend-cap accounting as normal calls), write the archive entry, *then*
  delete the day file. Any failure leaves the file in place and the next
  tick retries. Runs at backend startup (catches up after the PC was off)
  and hourly (thread in `backend/main.py`). Manual: `python -m memory.spin_cycle`.
- `context.py` -- `gather()` (day block + archive keys, called by both
  listeners before the LLM call) and `log_metrics()`.
- Recall: the reply schema has `recall: []`
  (`llm_fallback/BASE_CONTRACT.md`). If the user refers to a past day, the
  model returns up to 3 index keys with `intent`/`answer` null;
  `attempt_fallback()` fetches them and calls again once with
  `recalled_archive`. At most one extra call, only on those turns.
- `short_term.py` still exists, but only for follow-up reply mechanics
  (`follow_up_answer`, session windows, and the `earlier_turns` the
  file-operation clarification flow validates against). It is no longer the
  source of conversational context.

Why keys + fetch and not vector embeddings: one key per day is ~20 tokens
(a year is ~7k), and a key you can read in a log is debuggable in a way a
similarity score isn't. If archive keys pass ~200 or `recall_hits` shows
misses, embed the keys and pre-filter to the top-k (`routing/` already has
the embedding matcher).

### Desktop app (Memory panel) and API

Tabs: **Today** (the 24h log, with the size sent per question), **Archive**,
**Long Term**, **Follow-up** (the old `short_term.json` turn), plus a
"How EKKO remembers" explainer. Everything except the follow-up turn can be
deleted from the UI; only Long Term lines can be added and edited.

`backend/routers/memory.py`: `GET/DELETE /api/memory/day-log` (+ `DELETE
/day-log/turn?ts=`), `GET /api/memory/archive` + `DELETE ?key=`, and
`POST/PUT/DELETE /api/memory/long-term/lines`. Long-term lines are addressed
by section + index plus `expected` (the text the client saw); a mismatch is a
409 because scoring can change the file while the UI is open. An edit keeps
the line's date; an add is dated today and skips scoring/dedup.

### Logs (tail live with `system/dev.ps1`, tags `CONTEXT` and `SPIN`)

- `logs/context_metrics.jsonl` -- one line per LLM call: `day_turns`,
  `day_tokens_est`, `truncated_turns`, `budget_hit`, `archive_keys_sent`,
  `archive_key_tokens_est`, `recall_keys`/`recall_hits`/`recall_tokens_est`,
  `prompt_chars`, `prompt_tokens_est`, `llm_latency_s`,
  `recall_extra_latency_s`, `usage` (provider's real token counts), `error`.
- `logs/spin_cycle.jsonl` -- one line per day compacted: `date`, `turns_in`,
  `tokens_in`, `key`, `summary_tokens`, `total_s`, `provider`, `ok`, `error`.
- The summary call also lands in `llm_fallback/logs/<provider>/fallback.jsonl`
  (`source: "spin"`) and the provider's usage totals.

Tuning knobs are module constants: `TOKEN_BUDGET` and `ANSWER_CHAR_CAP`
(`day_log.py`), `KEY_CAP` and `FETCH_TOKEN_CAP` (`archive.py`). Read the
metrics after a few days of real use before changing them.

Privacy: every exchange is now stored on disk (in `EKKO_DATA_DIR`) and the
last 24h is sent to the LLM provider on each fallback call. Same caveat as
the section below, but for everything said, not just remembered facts.
Delete a date file in `day_log/` or an `archive.json` entry to forget it.

## How a candidate gets here

`llm_fallback/BASE_CONTRACT.md`'s JSON contract carries an
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
