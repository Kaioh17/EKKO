# ekko

[![CI](https://github.com/Kaioh17/EKKO/actions/workflows/ci.yml/badge.svg)](https://github.com/Kaioh17/EKKO/actions/workflows/ci.yml)
![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)   
A local voice assistant with a desktop app.
🌐 [ekko.usemaison.io](https://ekko.usemaison.io)

## What it does

ekko listens for its own wake word, "hey ekko".
That is a custom model trained with openWakeWord's open source pipeline, so there is no paid service and no borrowed trigger phrase.
It checks that it's really you speaking, transcribes the command, and runs it if it belongs to a fixed set of known actions (open an app, check system stats, search the web).
Anything more open-ended goes to an LLM of your choice (Gemini, Groq, DeepSeek, Claude, OpenAI, Claude Code or a local Ollama), with automatic failover between them.
It talks back out loud.
If you'd rather not say the wake word, you can hold a key and talk instead (optional, off by default).

One rule holds throughout: don't add intelligence where a simple, predictable rule already works.
See [docs/DESIGN.md](docs/DESIGN.md).

## Install (Windows)

Download the installer from the [latest release](https://github.com/Kaioh17/EKKO/releases/latest) and run it.
No admin rights or Python are needed.
See [docs/HOST_PROTOCOL.md](docs/HOST_PROTOCOL.md) for what to expect, how updates arrive, and how to fix common problems.

The first launch downloads about 65 MB of voice models, then ekko is ready.
Add an API key for the LLM you want under Models in the app.

**A note on the installer and antivirus/SmartScreen:** the release builds aren't code-signed yet (a Windows code-signing certificate costs money, which this project doesn't have right now), so Windows Defender, SmartScreen, or other antivirus software may flag or block the `.exe` as an unknown publisher. This is a false positive, not a compromised build — releases are built directly from this repo's source by GitHub Actions ([release.yml](.github/workflows/release.yml)) and carry build provenance attestation. We'll keep publishing signed releases once that's feasible. Until then, if you'd rather not click through a security warning, clone the repo and run it yourself instead — see [Develop](#develop) below.

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
Speech recognition has two engines, Whisper and Parakeet, and chooses for you: Whisper `medium` on a GPU that works, otherwise Parakeet once you've downloaded it (about 660 MB, from the app), otherwise Whisper `small`.
You can pick either yourself under General & Voice.
The default PyTorch install is the CPU build (about 200 MB instead of 3 GB).

### GPU

If you have an NVIDIA GPU and want PyTorch to use it, install the CUDA build of `torch` and `torchaudio` from [pytorch.org](https://pytorch.org/get-started/locally/) over the CPU one.
Whisper reaches the GPU through CTranslate2, which needs NVIDIA's CUDA libraries.
The CUDA build of `torch` brings them, which is why a source install gets GPU speech recognition this way.
The installer doesn't include them (they add about 1.4 GB), so an installed copy uses the processor and, once downloaded, Parakeet.
General & Voice shows which one is running.

## Settings and keys

- Everything you might want to change (LLM provider and failover order, spend caps, thresholds, speech engine and model, microphone and speaker, hold to talk) is in the app and stored in a local database.
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
