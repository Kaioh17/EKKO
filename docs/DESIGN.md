# Design

This document captures what ekko is and why it's built the way it is.
Treat it as the source of truth for intent and architecture, and update it as decisions get made.
For how the pieces run together, see `docs/ARCHITECTURE.md`.

## What EKKO is

A voice assistant for controlling a computer, in the spirit of "hey Google" but running locally.
It is not a chat assistant like ChatGPT, Gemini or Claude.
The chat panel answers questions and runs commands, but the point is that ekko is there when you think "Play a song by Rema on Spotify", "How is my watchlist looking?", "What is up with the Outlook downtime?", "Search my laptop for duplicates" or "Run Claude".
Running locally also means the cost to users can stay near nothing if downloadable releases ever ship.
It is at an early stage and will do much more.

We have provided a layer for voice authentication, so only the owner can issue commands.

ekko is not free by claim, but it can be free: set it up with a free tier (Gemini, Groq) or a local model (Ollama) and it costs nothing.
Some providers in the pipeline (DeepSeek, Claude, OpenAI) are cheap but paid.
Each user brings their own key, so cost stays low and goes to the provider, not to us.
Don't make a paid service the only path, and don't describe ekko as completely free.

## Design philosophy

Deterministic first. Reach for AI only where the task is genuinely
ambiguous, not because it's the modern default. Concretely:

- Known voice commands get matched against a closed, hand-written intent
  set, not routed through an LLM. The matching itself is embedding
  similarity rather than the string/regex logic originally planned,
  Whisper's phrasing varies too much for fixed patterns, but the
  properties that mattered are kept: same input always gives the same
  output, and the system can only ever return an intent from the set it
  was given. See `intent_routing.md` and `routing/README.md`.
- Presence and motion detection is computer vision, not a
  vision-language model, unless the task actually requires semantic
  understanding of a scene.
- AI/LLM involvement is reserved for the parts that don't have a
  clean deterministic answer: open-ended requests, scene
  interpretation that goes beyond "something moved."

This keeps cost near zero, keeps latency low, and keeps the system's
behavior predictable and debuggable, which matters a lot once it has
the ability to execute commands on the machine.

## Why security gets extra weight

This system will eventually have a camera, a microphone, and the
ability to run commands on the laptop. That's a real attack surface,
bigger than anything else built so far. Two principles guide every
decision here:

1. **Local-first.** Audio and video should not leave the device
   unless there's a specific, deliberate reason. Local processing
   avoids an entire class of privacy and interception risk.
2. **Identity before action.** No command reaches the execution layer
   without confirming it came from the user specifically, not just
   that a wake word was said. A wake word alone can be triggered by
   anyone in earshot, a roommate, a video call, a TV. That's not a
   security boundary on its own.

Open question, not yet addressed: replay/spoofing resistance. Right
now the pipeline checks whether a voice sample matches the enrolled
embedding, but does not check for liveness (is this a live voice or
a played-back recording). Worth revisiting before this gates anything
higher-stakes than convenience commands.

## Architecture

Full pipeline, once all stages are built:

```
Silence
  → Voice Activity Detection (always running, cheap)
  → Wake word check (fires only when VAD detects speech)
  → Speaker verification (fires only when wake word matches)
  → Speech recognition, Whisper or Parakeet (fires only when speaker is confirmed)
  → Intent matching against the closed intent set
  → Matched scripts/<os>/ handler executes
```

The staged structure is deliberate. Each stage only runs when the
one before it passes, so the expensive steps (speaker embedding,
transcription) never run continuously. This is also the security
gate: the two most consequential checks, "is this a real trigger"
and "is this actually the user," both happen before any transcription
or command matching occurs.

There is an optional second way in. Hold to talk replaces the first three
stages with a held key: hold it, speak, let go, and the audio goes straight
to speech recognition. It skips speaker verification the same way manual
wake does, because someone at the keyboard can already touch the mic and
the scripts. Everything from transcription onward is unchanged, so the
closed intent set still decides what can run.

## Stack

| Layer | Tool | Why | Cost |
|---|---|---|---|
| Voice activity detection | `silero-vad` | Tiny, always-on, catches "someone is speaking" | Free |
| Wake word | `openWakeWord`, custom "hey ekko" model (`listener/models/hey_ekko.onnx`) | Open source end to end: its training pipeline built the model from synthetic samples, no recorded dataset and no paid service. CPU-light at inference | Free |
| Speaker verification | `speechbrain` (ECAPA-TDNN, pretrained on VoxCeleb) | Pretrained, don't train from scratch, sub-1% EER on standard benchmarks | Free |
| Transcription | `faster-whisper` (local, CTranslate2) and, optionally, NVIDIA Parakeet TDT 0.6B through `onnx-asr` (local, int8 ONNX, CPU). `stt_engine` `auto` is Whisper `medium` on a GPU whose CUDA libraries load, otherwise Parakeet once downloaded, otherwise Whisper `small` (int8) | Both run on a laptop CPU, and Whisper uses a GPU when it can. Parakeet is an optional 660 MB download so the installer stays small. It has no prompt to steer it, so `listener/text_correct.py` (a port of Handy's matcher) restores ekko's own spellings afterwards. No cloud STT dependency. Its speed against Whisper `small` is not yet measured, see `listener/STT_IMPROVEMENTS.md`. NVIDIA publishes the model under CC-BY-4.0; confirm the licence on the ONNX export's model card before ever bundling it in an installer | Free |
| Intent routing | `sentence-transformers` (`all-MiniLM-L6-v2`) cosine similarity against a closed intent set | Regex proved too brittle for Whisper's phrasing variance. Still deterministic (same input, same output) and still a closed set, so no LLM in the routing decision, see `intent_routing.md` | Free |
| Command execution | `subprocess` calling scripts under `scripts/<os>/` (`.ps1` on Windows, `.sh` on Linux; picked by the `system.os` setting, see `routing/host.py`; user-specific handlers live in `personal/scripts/<os>/`) | Simple, auditable, and constrained: only files in that one directory can run | Free |
| Compute (CV, later) | To be decided | Deterministic CV (OpenCV/YOLO) preferred over VLM unless semantic understanding is required | Free |
| Audio feedback | Piper TTS + `sounddevice` | Local, CPU-fast, zero cost. Synthesizes every response at runtime through one code path, fixed system text now, LLM-generated text later, no separate pre-recorded-WAV tier | Free |
| NO_MATCH fallback | LLM, see `llm_fallback/` | One call re-checks for a paraphrased/misheard command and, if there isn't one, answers an open-ended question directly; closed-set and independently re-validated before anything runs, no path to execution for an answer, not a loosening of "no LLM in the routing decision" | Bring your own key. Free options exist (Gemini, Groq, Ollama); paid ones (DeepSeek, Claude, OpenAI) are cheap and capped. Any free-tier LLM works; Gemini's free tier gives the best results, self-hosted Llama is the best option if you have the hardware for it |

## Setup

See the readme for install and dev commands.
Voice models (wake word, Piper voice) are downloaded on first run by `models.py`, not stored in the repo.
Speaker verification needs your own enrollment (`voice_auth/auedio.md`); without it the listener runs on the wake word alone.
API keys go in `.env` (or the app's Models panel); every other setting is in the settings database.

## Status

Build progress and known issues are tracked in GitHub issues.

## Constraints to respect in any future work on this project

- Cheap, and free where possible. Local components (voice, routing, speech) must be free if       possible or cheap and open source.
  LLM providers are bring-your-own-key: at least one free or offline option must always work, paid ones are optional and capped.
  Never claim ekko is completely free.
- Deterministic before AI, for any new feature, default to
  hardcoded logic and only escalate to a model if the task can't be
  solved that way.
- Local-first for audio/video, no cloud processing by default.
- Execution is a fixed set of pre-approved actions (enforced by
  `routing/config.py` and `routing/execute.py`). An action exists only if
  it's a script in `scripts/` named by an intent in
  `routing/intents.yaml`. Arbitrary shell access is not on the roadmap.
- No destructive or state-reversing command without a separate,
  explicit decision. That one is still unmade, and the routing layer's
  inability to tell "close X" from "open X" is a concrete reason to
  keep it that way until there's a real answer.
