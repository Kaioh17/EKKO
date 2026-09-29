# ekko

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Kaioh17/EKKO/badge)](https://scorecard.dev/viewer/?uri=github.com/Kaioh17/EKKO)
[![CI](https://github.com/Kaioh17/EKKO/actions/workflows/ci.yml/badge.svg)](https://github.com/Kaioh17/EKKO/actions/workflows/ci.yml)

A local voice assistant with a desktop app.
🌐 [ekko.usemaison.io](https://ekko.usemaison.io)

<video src="https://github.com/Kaioh17/EKKO/raw/main/docs/demo/check_battery_3.mp4" controls width="640"></video>

[▶ Watch the demo](docs/demo/check_battery_3.mp4) - asking ekko for a battery check, wake word to spoken answer.

## What it does

ekko listens for its own wake word, "hey ekko".
That is a custom model trained with openWakeWord's open source pipeline, so there is no paid service and no borrowed trigger phrase.
It checks that it's really you speaking, transcribes the command, and runs it if it belongs to a fixed set of known actions (open an app, check system stats, search the web).
Anything more open-ended goes to an LLM of your choice (Gemini, DeepSeek, Claude, OpenAI, Claude Code or a local Ollama), with automatic failover between them.
It talks back out loud.

One rule holds throughout: don't add intelligence where a simple, predictable rule already works.
See [docs/DESIGN.md](docs/DESIGN.md).

## Install (Windows)

Download the installer from the [latest release](https://github.com/Kaioh17/EKKO/releases/latest) and run it.
No admin rights or Python are needed.
See [docs/HOST_PROTOCOL.md](docs/HOST_PROTOCOL.md) for what to expect, how updates arrive, and how to fix common problems.

The first launch downloads about 65 MB of voice models, then ekko is ready.
Add an API key for the LLM you want under Models in the app.

## Develop

```powershell
.\dev.ps1               # Windows: app + backend + voice listener in one command
.\dev.ps1 -NoListener   # without the microphone
```

```bash
./dev.sh                # Linux / macOS / WSL
```

Requirements: Python 3.12+, Node 22 with pnpm, and Rust (for the Tauri shell).
See [CONTRIBUTING.md](CONTRIBUTING.md) for tests and lint, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the parts fit together.

## Runs on a laptop

Every component runs on a CPU.
Speech recognition picks its own size: medium on a CUDA GPU, small on a CPU.
The default PyTorch install is the CPU build (about 200 MB instead of 3 GB).

### GPU

If you have an NVIDIA GPU and want PyTorch to use it, install the CUDA build of `torch` and `torchaudio` from [pytorch.org](https://pytorch.org/get-started/locally/) over the CPU one.
Speech recognition already uses the GPU without that, through CTranslate2.

## Settings and keys

- Everything you might want to change (LLM provider and failover order, spend caps, thresholds, speech engine, Whisper model) is in the app and stored in a local database.
- API keys live in a `.env` file in the data folder (repo root in development, `%APPDATA%\io.usemaison.ekko` when installed), and are set from the app's Models panel.
- Anything specific to you or your machine (extra voice commands, scripts, a briefing watchlist) goes in `personal/`, which is gitignored. See [scripts/README.md](scripts/README.md).

## Privacy

Audio is processed on your computer.
Only the text of a request that ekko can't handle itself goes to the LLM you chose, plus your recent conversation for context.
If you pick OpenAI for speech, the spoken replies are sent there too.

## Security

See [SECURITY.md](SECURITY.md) to report a problem.

## License

MIT, see [LICENSE](LICENSE).
