# ekko: what it can do today

Source material for the new landing page. Everything here is in the repo now; the "Not yet" list is what to avoid promising.

**One line:** a local voice assistant for Windows. Say "hey ekko", it checks it's you, runs known commands on your machine, and hands anything open-ended to the LLM of your choice. Audio never leaves your computer.

## Voice

- **Own wake word.** "Hey ekko" is a custom openWakeWord model trained with the open source pipeline. No paid service, no borrowed trigger phrase.
- **Speaker verification.** After the wake word, ekko checks the voice against your enrolled voiceprint (ECAPA-TDNN) before doing anything. Roommates and TVs don't get in. *(Enrollment is still a command-line step.)*
- **Local speech recognition.** faster-whisper; medium on a CUDA GPU, base.en on a CPU, and the Voice page picks which processor to use. English only.
- **Spoken replies.** Piper TTS runs locally by default; OpenAI voices are optional.
- **Always-on but cheap.** Silero VAD gates every stage, so the heavy models only run when needed.
- **Hotkeys.** `Ctrl+Alt+W` skips straight to listening. `Ctrl+Alt+Q` cuts speech and aborts the current action.

## Commands (a closed, hand-written set)

| Say | ekko does |
|---|---|
| "Open Chrome / Discord / VS Code / Cursor / Steam / Brave" | Opens the app |
| "Open task manager" | Opens it |
| "Open Apple Music" | Opens the web player, then offers to search |
| "How's my system?" / "How much RAM am I using?" | Runs diagnostics or answers a specific metric, with the real numbers spoken |
| "Check memory activity" | Reports what is using memory |
| "Daily briefing" | Stocks, top news (spoken and opened as tabs) and your watchlist, rendered as a report |
| "How's my watch list?" | Spoken tickers with price and % change |
| "What time is it?" | Date and time |
| "Search the web for…" | Opens the search |
| "Make a folder called…" / "what files do I have" | Opens a file gateway that asks you to approve before touching disk. It has no delete tool at all |

Add your own commands with a script and a YAML entry in `personal/`.

## Open-ended questions

- Anything not in the command set goes to an LLM. It can also catch a misheard or paraphrased command and route it, but only to an intent from the closed set.
- **Six providers with automatic failover:** Gemini (free), DeepSeek, Claude API, OpenAI, Claude Code (your subscription, no key) and Ollama (fully offline).
- **Failover order you control**, a cooldown on failing providers, and **spend caps** on every paid one.
- **Domain expert mode.** A finance persona with its own guardrails swaps in when the question matches.
- **Research mode** can open relevant tabs.

## Memory

- Keeps the last 24 hours of conversation (voice and chat) in every prompt.
- A daily summarizing pass files older days into an archive. The model can recall an old day when it needs it, so context never runs out.
- Long-term memory is a plain markdown file you can read and edit. Writes follow a policy (explicit "remember this", corrections and stated preferences, repeated facts) and every accept or reject is logged.

## The desktop app

Tauri shell with a React UI. Panels:

- **Overview**, **Activity** and live status (idle, listening, speaking), pushed over a WebSocket
- **Chat**, typed conversation with the same brain as voice
- **Briefing**
- **Memory** (today, short-term, long-term, archive)
- **Routing**, to see how an utterance was matched
- **Models**, to set keys, order, caps and models per provider
- **Voice**, **General** (thresholds, Whisper size) and **Hotkeys**
- **Help**
- Optional floating avatar
- Starts at sign-in, signed one-click updates, setup banner on first run

## Privacy and security

- Audio is processed on your machine. Only the text of a request ekko can't handle goes to the LLM you picked, plus recent context.
- The local API binds to 127.0.0.1 with a per-launch token, and checks origin.
- Commands run from a closed set of scripts, without a shell.
- API keys are write-only through the API.
- Updates are signature-checked, and builds use hash-locked dependencies with CI audits (CodeQL, OpenSSF Scorecard).
- MIT licensed, with no paid services in the default path.

## Runs on a laptop

CPU only is fine (CPU PyTorch is about 200 MB). The backend starts in under a second, and about 65 MB of voice models download on first launch. No admin rights or Python are needed to install.

## Architecture (the story)

**Principle: deterministic first, AI only where the answer is genuinely ambiguous.** Each stage runs only if the one before it passes, so the expensive and risky steps never run unless they have to.

```
Silence
 → Voice activity detection      always on, cheap
 → Wake word "hey ekko"          only when speech is heard
 → Speaker verification          only when the wake word matched
 → Whisper transcription         only when it's you
 → Intent matching               embedding similarity against a closed set
      ├─ match   → a fixed script runs, ekko speaks the result
      └─ no match → LLM (failover chain) → spoken answer
```

**Why it's built this way**

- **Identity before action.** A wake word alone isn't a security boundary, so verification comes before transcription or command matching.
- **A closed set, not a model, decides what runs.** `routing/intents.yaml` is the entire list of things ekko can ever execute. Matching is similarity-based, so it copes with Whisper's phrasing, but it is deterministic and can only return an intent from the list. Even when the LLM suggests a command, it is re-validated against the same list.
- **Local-first.** Wake word, verification, transcription, routing and speech all run on the CPU.

**How it runs**

```
ekko.exe  (Tauri: Rust shell + React UI)
   └─ ekko-backend  (FastAPI, localhost only, token-guarded)
         ├─ settings DB, keys, memory, logs  → per-user data folder
         ├─ supervises the voice listener (restarts it if it crashes)
         └─ one WebSocket for live status
```

The app attaches to an already-running backend (for example one started at sign-in) or starts its own. Settings are validated once, on save, and most apply immediately.

## Not yet (don't promise)

- macOS and Linux installers (the code runs there, but there is no installer)
- In-app voice enrollment
- Languages other than English
- A signed installer (SmartScreen will warn)
- Adding commands without editing files
- Streaming replies
- Vision and presence detection (planned, not built)
