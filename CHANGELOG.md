# Changelog

Notable changes to EKKO. Newest first. Dates are local (America/Chicago).

## Unreleased (2026-11-09): the daily briefing is editable from the app

The briefing's topics, tickers, tabs, limits and wording were hardcoded or
in a YAML file. They now live on the Briefing page.

- **Everything is editable:** section order and on/off, news topics and how
  far back to look, how many stories are listed, read aloud and opened as
  tabs (no longer one shared number), tickers and spoken names, health
  metrics and limits, narration prompts, the spoken phrases, delivery
  (screen, voice or both).
- **Plain or AI narration.** Plain reads the facts as fetched; AI narration
  rewrites them and shows the tokens it used. A Preview button runs both so
  you can compare. Narration now goes through your provider order and
  spending caps instead of calling Gemini directly.
- **One builder.** Voice, chat and Preview share `build()` in
  `scripts/daily_briefing.py`; adding a briefing section is one id, its
  settings and one function.
- `personal/daily_briefing.yaml` moves into the settings automatically;
  `config/daily_briefing.yaml` is gone (its values are the defaults).
- The health limits are shared with "system diagnosis".

## Unreleased (2026-11-07): speech recognition you can see, choose and measure

### The problem

Speech recognition worked, but you couldn't see it, tune it or tell whether
a change helped.

- **Saved audio settings were ignored.** The microphone, speaker, processor
  and thread-count settings saved fine, but the listener never read them, so
  the pickers did nothing.
- **A missing GPU library went unnoticed.** In 9 of 11 installed-app launches
  in the logs, the GPU couldn't start (`cublas64_12.dll` not found) and
  speech recognition quietly ran on the CPU.
- **Requests were clipped or split.** Speech detection fires a moment after
  you start talking, so first words were lost. The 500 ms pause setting
  probably cut some commands at their pauses too: the rejected "no speech"
  segments ran a median of 0.93 s, but that is a hint, not proof. Of 101
  activations in the dev logs, 35 ended with no speech segment at all.
- **ekko sometimes heard itself.** Its own "Access denied." and "Command
  confirmed." were accepted as commands 7 times.
- **Nothing was measured.** The baseline in
  `listener/STT_IMPROVEMENTS.md` had to be rebuilt by hand from console logs.

### What's new

- **A second speech engine, Parakeet** (NVIDIA, through `onnx-asr`). It's
  optional: about 660 MB, downloaded from the Audio page with a progress
  bar. **Automatic** uses Whisper on a GPU that works, otherwise Parakeet
  once downloaded, otherwise Whisper `small`. If Parakeet can't start, ekko
  falls back to Whisper and says why.
- **The Audio page controls what it advertises:** microphone, speaker,
  engine, model, processor, thread count (with Calibrate), custom words and
  the fine-tuning numbers, plus "Running now" and "Recent requests". Four
  Help guides explain the choices.
- **Hold to talk** (optional, off by default): hold `Ctrl+Alt+Space` (or a key
  you choose), speak, let go. No wake word, no greeting, no wait for a pause.
- **A chime or silence instead of the spoken greeting** after "hey ekko".
- **450 ms of pre-roll**, so a request keeps its first syllable, and the
  default pause before a request ends is now 800 ms (was 1500 ms).
- **Custom words:** near-misses of `Ekko`, `Chrome`, `Brave` and `Spotify`
  are corrected after transcription.
- **ekko's own voice is rejected** when a transcript exactly matches
  something it says.
- **A segment log** (`listener/logs/segments.jsonl`) and
  `python -m listener.stt_report`, so every change can be checked.

### How much it helps

- **The acknowledgement** takes about 0.7 s as a chime, including the audio
  device's own delay, against a median of about 2.4 s for a spoken line
  (measured on the dev machine).
- **The rest is too early to call.** The first live session logged 8
  activations. Before-and-after rows will accumulate in the Check-backs table
  of `listener/STT_IMPROVEMENTS.md`.

### What it costs

- **Parakeet is a 660 MB optional download**, and `onnx-asr` joins the
  dependencies. `locks/` was regenerated; no existing pin moved. It also
  gained `mcp` and `keyring`, which `requirements.txt` already listed but the
  lock was missing.
- **The installed app still runs on the processor.** The CUDA libraries would
  add about 1.4 GB, so they aren't bundled. A source install gets the GPU
  through the CUDA build of PyTorch.
- **The hold-to-talk key isn't blocked**, so the app you're in also sees it.

### Fixed

- `/api/stt/hardware` and `/api/stt/calibrate` always returned 404: the
  router was never registered in `backend/main.py`. The Calibrate button
  depends on it.
- Automatic on a PC with a GPU it couldn't use landed on slow CPU Whisper.
  It now goes to Parakeet when that is downloaded.
- `listener/calibrate.py` imported a constant that didn't exist.

### Not measured yet

- **Parakeet against Whisper `small` on a CPU**, for both speed and
  accuracy. Nothing in the docs or the app claims it is faster.
- **Parakeet inside a built installer.** The build spec now collects it, but
  no installer has been built from this version.

### Removed or changed

- `command_min_silence_ms` defaults to 800 (was 1500). A saved value is kept.
- New settings in `general`: `preroll_ms`, `min_command_ms`,
  `max_no_speech_prob`, `min_avg_logprob`, `stt_engine`, `parakeet_model`,
  `custom_words`, `word_correction_threshold`, `push_to_talk`,
  `push_to_talk_hotkey`, `ack_style`. Each is in the settings reference in
  `listener/README.md`.
- New endpoints: `GET /api/stt/models`, `POST /api/stt/models/{id}/download`,
  `GET /api/stt/status`.
- `GET /api/overview/general` lists "Hold to talk" among the hotkeys.
- The Hotkeys page now shows each key's real state instead of a fixed list.

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
