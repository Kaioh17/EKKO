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
| Memory, recordings, logs, caches | `memory/`, `listener/captures/`, `**/logs/`, `routing/.index_cache.pt` |
| Downloaded models | `models/` (checksummed, fetched on first run by `models.py`) |
| Your own commands and scripts | `personal/` |

Code and the files it reads (intents, prompts, handler scripts) stay in the install folder and are never written to.

## Settings

Every setting a user might change lives in the database and is edited through `/api/settings/<section>`.
A section is one pydantic model in `backend/schemas/settings.py`, so validation happens once, on save.
The voice pipeline reads its settings through `backend/config.py` on each use, so most changes apply immediately.
Saving `general` or `voice` restarts the listener, because it only reads those at startup.

Sections: `general` (thresholds, Whisper model, overrides), `llm` (provider, failover order, cooldown, models, spend caps, research), `voice` (speech engine and voice), `system` (which OS's command scripts to use).

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
- Speech recognition picks CPU or GPU and a matching size, and uses half the CPU cores so the microphone, voice detection and speech synthesis keep up.

Next, in order of payoff:

1. **One process owns the models.**
   Chat and the voice listener each load their own copy of the routing model.
   Having the listener answer chat requests too would halve memory and remove the second warm-up.
2. **Streaming answers.**
   Speak the LLM's reply while it is still being generated.
3. **Choose Ollama by hardware.**
   Detect RAM and GPU, and only put a local model in the failover chain when the machine can run it well.
