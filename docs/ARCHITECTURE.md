# Architecture

This page explains how the pieces of ekko run together, how the local API is secured, and where the performance work goes next.
For why the voice pipeline is built the way it is, see [DESIGN.md](DESIGN.md).

## Processes

```
ekko.exe  (Tauri desktop app: Rust shell + React UI)
   │  picks a free 127.0.0.1 port and a random 32-byte token
   │  spawns, or attaches to, the backend
   ▼
ekko-backend  (FastAPI control plane, one process)
   ├─ settings database, API keys (.env), memory, logs   -> per-user data folder
   ├─ supervises the voice listener as a child process
   │     - restarts it if it crashes (capped exponential backoff)
   │     - the UI's Wake button reaches it by long-poll (GET /api/listener/next)
   │     - its state (idle, listening, speaking...) is pushed to POST /api/status/push
   └─ one WebSocket, /ws/status, for live status and reports
```

The voice listener is the same executable started with the `listener` argument.
In development the same roles are played by `python -m backend` and `python -m listener.vad_listener`.

### One backend, one listener

The backend writes `runtime.json` (port, token, PID) to the data folder, readable only by the user.
When the app starts it looks there first.
If a backend is already running (for example one started at sign-in by the Task Scheduler task), the app attaches to it.
Otherwise the app starts its own and stops it on exit, through `POST /api/shutdown` so the listener stops cleanly too.
A backend the app started also watches the app's PID and exits if the app dies.

Each backend writes its own version into `runtime.json`.
The app compares that to its own version before attaching; a mismatch (a stale backend still running right after an update replaced its files) is asked to shut down instead, and the app starts a fresh one rather than talk to it.
One version number covers the app, the backend and the installer (`version.py`, kept equal to the three `ekko-ui` manifests by `tests/test_version.py`), since they ship and update as one signed unit.

Nothing in these processes reads from standard input.
On Windows, a thread blocked on a stdin pipe deadlocks later native DLL loads (torch, onnxruntime), which froze the backend during development.
Control messages go over HTTP instead.

## Data

Everything ekko writes goes under one folder (`paths.DATA_DIR`).
In development that is the repo itself, so paths are unchanged.
Installed, it is `%APPDATA%\io.usemaison.ekko`.

| What | Where |
|---|---|
| Settings | `backend/ekko.db` (SQLite, one key-value table) |
| API keys | `.env` (keys only) |
| Memory (long-term facts, day log, today's summary, archive, review state) | `memory/MEMORY.md`, `day_log/`, `today_summary.json`, `archive.json`, `review.json` |
| Recordings, logs, caches | `listener/captures/`, `**/logs/`, `routing/.index_cache.pt` |
| Downloaded models | `models/` (checksummed, fetched on first run by `models.py`) |
| Parakeet speech models | `models/stt/<model id>/`, downloaded from the app on request (`listener/stt_models.py`), not by `models.py` |
| Speech recognition log | `listener/logs/segments.jsonl`, one line per event (see `listener/README.md`) |
| Which speech engine is running | `listener/stt_runtime.json`, rewritten each time the listener starts |
| Your own commands and scripts | `personal/` |

Code and the files it reads (intents, prompts, handler scripts) stay in the install folder and are never written to.

## Settings

Every setting a user might change lives in the database and is edited through `/api/settings/<section>`.
A section is one pydantic model in `backend/schemas/settings.py`, so validation happens once, on save.
The voice pipeline reads its settings through `backend/config.py` on each use, so most changes apply immediately.
Saving `general` or `voice` restarts the listener, because it only reads those at startup.

Sections: `general` (thresholds, speech engine and model, audio devices, hold to talk, overrides), `llm` (provider, failover order, cooldown, models, spend caps, research), `voice` (speech engine and voice), `system` (which OS's command scripts to use).

## Speech recognition

The listener turns each request into text with one engine, chosen at startup by `listener/stt.py` from the `general` settings (`stt_engine`, `parakeet_model`, `whisper_model`, `whisper_device`, `whisper_cpu_threads`).
`auto` means Whisper on a GPU whose CUDA libraries load, otherwise Parakeet if it has been downloaded, otherwise Whisper on the CPU.
If the chosen engine can't start, the listener falls back to Whisper rather than failing, and records why.

| Route | What it does |
|---|---|
| `GET /api/stt/hardware` | Whether a GPU is visible, the core count, and the automatic thread count |
| `POST /api/stt/calibrate` | Times Whisper on the CPU at several thread counts and returns the fastest |
| `GET /api/stt/models` | Each Parakeet model: size, downloaded or not, progress |
| `POST /api/stt/models/{id}/download` | Starts a download on a background thread. A second start while one runs returns 409 |
| `GET /api/stt/status` | The engine the listener loaded and why it fell back, if it did, plus a summary of the last 50 requests |

A download's progress is the bytes on disk against the model's known size, so it keeps working if the app is reopened mid-download.
`GET /api/stt/status` reads `listener/stt_runtime.json` (what the listener loaded) and `listener/logs/segments.jsonl` (what happened since).
Both are written by the listener, and the backend only reads them.

## Listener flow

`listener/vad_listener.py` owns the microphone loop. Every stage runs only if the one before it passed, so the expensive steps never run continuously.

```
mic ─► Silero VAD (32 ms blocks, 450 ms pre-roll kept)
        │ speech heard; every block also feeds openWakeWord
        ▼
   "hey ekko" ──no──► idle
        │ yes
        ▼
   speaker check on the wake clip (ECAPA-TDNN) ──mismatch──► idle
        │ match
        ▼
   acknowledge (spoken line, chime or nothing), mic gated until playback ends + room decay
        │
        ▼
   active listening, up to `active_window_s` (10 s)
        │ each candidate segment is screened:
        │   long enough · your voice (loose, 0.25) · not empty · not ekko's own words
        │   · Whisper only: confident it was speech
        │ a failing segment is dropped with its reason; listening continues
        ▼
   transcribe (Whisper or Parakeet) ─► custom-word correction ─► router
        │ matched: run the handler        │ NO_MATCH: one LLM fallback call
        ▼                                 ▼
   speak the outcome ─► "Anything else I can help with?" and listen again
```

How a turn ends: a decline ("no", "that's all") gets a sign-off. Silence for the active window gets "Never mind, going back to sleep". An outcome ekko stayed quiet about also ends it.
The follow-up loop lives in the listener, not in `routing/intents.yaml`: that file is the boundary for what ekko can *do*, and "no" does nothing.

Three other ways in, none of which go through the wake word:

| Entry | Skips | Notes |
|---|---|---|
| Manual wake (`Ctrl+Alt+W`, or the app's Wake button via `GET /api/listener/next`) | Wake word and speaker check | Goes straight to active listening |
| Hold to talk (off by default, `Ctrl+Alt+Space`) | Wake word, acknowledgement, speaker check, waiting for a pause | Release ends the request. The pre-roll is kept. Capped at 30 s. An empty press is dropped (`nothing said`) |
| Hard stop (`Ctrl+Alt+Q`) | n/a | Cuts speech and any hold, returns to idle |

**The mic is gated while ekko speaks.** Without that it transcribes its own greeting as your command.
`MicGate` closes for the playback's real end (the output stream's measured latency) plus `feedback_tail_ms` for room decay, and the pre-roll is cleared when the gate closes so the end of the greeting can't lead the next segment.

Nothing is transcribed or saved from a segment that was not preceded by a verified wake word, hold, or manual wake.
Each accepted or rejected segment is one line in `listener/logs/segments.jsonl`; accepted commands are also appended to `listener/captures/transcripts.jsonl`.
Settings and tuning for every step are in `listener/README.md`.

## Memory

Everything ekko remembers, for voice and chat alike, in four layers.
Each LLM call (a `NO_MATCH` turn) gets them in this order:

| Layer | File | Lifetime | What the model sees |
|---|---|---|---|
| Long-term facts | `memory/MEMORY.md` | Until you delete them | All of it, every call |
| Past days | `memory/archive.json` | Until you delete them | One-line keys. The full entry only on `recall` |
| Today so far | `memory/today_summary.json` | Today | A running summary of turns that left the window |
| Live conversation | `memory/day_log/*.jsonl` | 24 h on disk | The newest turns, whole, up to about 4k tokens |

```
every exchange ──► day_log/YYYY-MM-DD.jsonl
 (voice + chat)        ├─ newest turns, whole, ≤ 4k tokens ──► conversation
                       ├─ today's turns that scrolled out ──► today_summary.json
                       └─ any date before today ──► spin cycle ──► archive.json ──► archive_index
```

- **Window, not a timer.** The live conversation is bounded by size, never by how long you paused. The newest turn is always kept, so a "yes" or "the second one" resolves however long the last answer was.
- **Today's summary.** Turns that fall out of the window are folded into one running summary by an LLM call. Only the backend writes it. It is nudged after a chat turn once about 800 tokens are pending, and forced on every spin-cycle tick.
- **Spin cycle.** The backend runs it at startup and every 5 minutes. Each finished day is scored first: one real LLM answer is worth 3.0, a matched command 0.5, a miss 0, and a day under 3.0 is dropped without a summary call. A day that passes is summarized into an archive entry, and the raw file is deleted only once its newest turn is over 24 h old, so the window can still span midnight. A failure leaves everything for the next tick.
- **Recall.** The model can answer `recall: [key]` instead. The fallback fetches those days (capped) and asks once more. Keys, not embeddings, so a key is readable in a log.
- **Long-term writes.** The model only proposes a `memory_candidate`. `propose_and_score()` decides: an explicit "remember", a correction or preference, a fact seen twice, or something tied to an active project is accepted. Everything else is rejected. Every decision is logged to `memory/logs/decisions.jsonl`.
- **Review queue.** Instructions and recipes still in the 24 h log are offered in the app before they age out, to save as a file, keep as a Long Term line, or discard. Nothing is stored for a pending item. Only what you already resolved is (`memory/review.json`).

The app's Memory panel and `/api/memory/*` read and edit all of this. A Long Term edit that no longer matches what the app saw returns 409, because scoring can change the file while the panel is open.
Metrics are in `memory/logs/context_metrics.jsonl` (one line per LLM call) and `memory/logs/spin_cycle.jsonl`. Tuning constants are listed in `memory/README.md`.

Privacy: every exchange is stored on disk, and each fallback call sends the live window, today's summary and the archive keys to the provider you chose.
Long-term facts are re-sent on later calls. Delete a turn or a day in the panel to forget it.

## Security model

| Threat | Control |
|---|---|
| Another machine reaches the API | The server binds to `127.0.0.1` only, and rejects other `Host` headers. |
| Another local process calls it | Every request needs the per-launch token. It lives only in memory, in the app and the backend, and in `runtime.json` (owner-only). |
| A web page in the user's browser calls it | The `Origin` header must be the app's own origin, and CORS allows only that. |
| Token leaks through logs or history | The token travels in a header, never a URL. WebSockets send it as a subprotocol. Request logging is off. |
| The desktop app is used to run code | The webview has no shell, file-system or network permissions beyond the local API. A strict content security policy blocks remote scripts. |
| Links in scraped news pages | Only `http` and `https` links open, in the system browser. |
| API keys | Stored only in the data folder. Set-only through the API: it reports whether a key is set and its last four characters, never the key. |
| Voice command runs arbitrary code | Handlers run by bare name, from `scripts/<os>/` or `personal/scripts/<os>/` only, without a shell, from a closed set of intents. |
| Tampered update | Updates are signed. The app checks the signature against the public key in its config before installing. |
| Compromised dependency | Builds install from hash-locked files, and CI audits them. |

Remaining, known limits: the token is readable by any process running as the same user (a local attacker with that access already owns the session), and the installer is unsigned until a code-signing certificate is bought (see HOST_PROTOCOL.md).

## Performance

Done:

- The backend starts in under a second (it used to take about 24 seconds), because the routing models load on first use and warm up in the background.
- The UI shares one WebSocket instead of one per panel.
- Speech recognition picks CPU or GPU and a matching size, and uses half the CPU cores so the microphone, voice detection and speech synthesis keep up. Each request keeps 450 ms of audio from before speech was detected, and hold to talk skips the wait for a pause.

Next, in order of payoff:

1. **One process owns the models.**
   Chat and the voice listener each load their own copy of the routing model.
   Having the listener answer chat requests too would halve memory and remove the second warm-up.
2. **Streaming answers.**
   Speak the LLM's reply while it is still being generated.
3. **Choose Ollama by hardware.**
   Detect RAM and GPU, and only put a local model in the failover chain when the machine can run it well.
