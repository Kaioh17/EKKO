# Changelog

Notable changes to EKKO. Newest first. Dates are local (America/Chicago).

## Unreleased (2026-11-05): memory that holds a conversation

### The problem

Follow-ups in chat (and often in voice) were not working. You'd ask
something, EKKO would answer and suggest a next step, you'd reply "yes" or
"explain the third one", and EKKO would treat the reply as if it came out
of nowhere.

### How it used to work

Conversation continuity rested on `memory/short_term.json`, a single slot
holding one question, its answer, and the follow-up EKKO asked.

- **The slot was only written when the model offered a follow-up.** An
  answer without one cleared it, so the next message had nothing to attach
  to.
- **A 20-second timer cleared it.** That number was the voice popup's
  display timeout (`SESSION_DEFAULT_TIMEOUT_S`), reused by chat as its
  "session". Reading a long answer before replying took longer than 20
  seconds, so the follow-up was gone by the time you answered.
- **The wider 24-hour context cut every answer to 600 characters.** Short
  spoken answers were unaffected; long structured chat answers lost
  everything after the first few lines, so "item 3" often wasn't there.
- **Voice and chat each wrote the same slot**, from different processes,
  and could overwrite each other's pending follow-up. Chat also read stale
  turns that voice would have ignored.
- **Yesterday wasn't recallable until a day later.** A day was archived
  only once its last message was 24 hours old.
- **Replies were capped at 1,024 output tokens.** A long chat answer could
  be cut off mid-JSON, fail to parse, and come back as "Sorry, could you
  say that again?" even though the model had answered.

What the logs show for that design (2026-09-15 to 2026-11-04):

| Measure | Result |
|---|---|
| Follow-up questions EKKO asked | 96 |
| Cleared with no reply ever attached | 68 (71%) |
| Median time from follow-up written to cleared | 16 seconds |
| Chat answers longer than the 600-char context cap | 9 of 41 (22%), longest 1,847 chars |
| Voice answers longer than the cap | 0 of 248 |
| Chat replies lost as unparseable (cut off at the token cap) | 3 of 64 |

### How it works now

One conversation for voice, chat and file-request clarifications, in four
layers (details in `memory/README.md`):

1. **Live conversation:** the newest exchanges from the day log, each kept
   whole, up to about 4,000 tokens. Oldest exchanges drop off first. Your
   last answer and EKKO's follow-up question are always its final entry.
2. **Today so far:** exchanges that drop out of the live window are folded
   into a running summary of the day (`memory/today_summary.json`), so
   "what we talked about this morning" still has an answer.
3. **Past days:** each day is archived from its full log shortly after
   midnight (it used to wait 24 hours), and stays recallable on request.
4. **Long-term facts:** `MEMORY.md`, unchanged.

Supporting changes:

- No timer touches memory. Voice still uses its 20-second window, but only
  for how long the popup and mic stay open.
- Matched commands, declines and file-request clarifications are recorded
  in the same conversation, so the model knows what was just done.
- Only the backend writes the summary and the archive; voice and the file
  tool only append to the day log. No two processes write the same file.
- Reply cap raised to 4,096 tokens (DeepSeek, OpenAI, Claude API), with
  60-second timeouts. If a reply is still cut off, its complete sentences
  are kept and chat marks it "(This answer was cut off. Ask me to
  continue.)" instead of discarding it.
- The Memory panel shows every layer: **Now** (today's summary and the live
  window, with a size meter), **Today** (the raw 24-hour log), **Archive**,
  **Long Term**. The Today and Archive views existed but were never shown.

### How much it helps

- **Follow-ups no longer expire.** The 71% that went unanswered were lost
  to the slot being cleared, mostly by the timer. A follow-up now stays in
  context until about 4,000 tokens of newer conversation push it out, and
  even then it moves into today's summary instead of disappearing.
- **Long answers stay referable.** The 22% of chat answers that were cut to
  600 characters are now sent whole, so "explain item 3" can see item 3.
  `tests/test_memory_followup.py` checks this with three answers of 1,500+
  characters, a voice command in between and a 10-minute pause.
- **Voice and chat share context.** Ask by voice, follow up in chat (or the
  reverse), and the second sees the first.
- **Yesterday is recallable from just after midnight** instead of up to a
  day later.

### What it costs

- **More input tokens per call.** The context sent with a prompt grows
  from a median of about 650 estimated tokens (old 24-hour block, 71 logged
  calls) to about 3,100 (first 7 calls on the new window). On DeepSeek
  Flash that is a fraction of a cent per reply; prompt caching absorbs much
  of it. Each chat reply's analytics line shows the real tokens and cost.
- **Extra summary calls:** one per batch of evicted exchanges (at least
  800 tokens, or every 5 minutes), and one per day for the archive, all
  counted against the provider's spend cap.
- **Up to 5 minutes of lag:** an exchange that just left the live window
  can take up to one tick to reach today's summary.

Not measured yet: the new follow-up success rate. The old slot logged
unanswered follow-ups, and the new design has no slot to log. Watch
`memory/logs/context_metrics.jsonl` (`window_turns`, `evicted_turns`) and
the per-reply analytics instead.

### Removed or changed

- `memory/short_term.py`, `short_term.json` and the `/api/memory/short-term`
  endpoints are gone. The leftover `memory/short_term.json` and
  `memory/logs/short_memory.jsonl` files are no longer read and can be
  deleted.
- New endpoints: `GET /api/memory/window`,
  `GET/DELETE /api/memory/today-summary`, `POST /api/memory/today-summary/refresh`.
- `GET /api/overview/general`: `memory.pending` is now `memory.in_window`
  (turns in the live conversation). The sidebar's Memory badge is removed.
- The prompt's `conversation_24h` and `short_memory` fields are replaced by
  `conversation` and `today_summary`; `BASE_CONTRACT.md` is updated to match.
- `memory/logs/context_metrics.jsonl` fields renamed (see `memory/README.md`).

### Also in this release (chat)

- Chat answers are written for reading, not speech: structured markdown
  with headings, lists and tables, rendered in the chat window. Voice
  answers are unchanged.
- "What's happening today" in chat returns the briefing as a chat message
  (news links and a watchlist table) instead of narrating it aloud.
- The chat model picker works: a chosen provider answers alone, "Auto"
  uses your configured provider with failover.
- Chat never shows an em dash.
- Copy buttons on your messages and EKKO's replies.
- An analytics line under each reply: total and model time, tokens,
  cost, spend against your budget, failover, web search and research,
  archive recalls, and how much conversation was sent.
