# Design

This document captures what ekko is and why it's built the way it is.
Treat it as the source of truth for intent and architecture, and update it as decisions get made.
For how the pieces run together, see `docs/ARCHITECTURE.md`.

## What EKKO is

A voice-and-vision assistant for a personal work lab setup. Long-term
goal is a fully autonomous system: eyes on the room, voice control
over the laptop, security tight enough that only the owner can issue
commands. Short-term goal is much narrower, get one piece working
end to end before adding the next.

It was built solo on a zero budget, so every tool choice is free or open source.
That constraint is permanent: don't propose paid services as the default path.

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
  → Whisper transcription (fires only when speaker is confirmed)
  → Intent matching against the closed intent set
  → Matched scripts/<os>/ handler executes
```

The staged structure is deliberate. Each stage only runs when the
one before it passes, so the expensive steps (speaker embedding,
transcription) never run continuously. This is also the security
gate: the two most consequential checks, "is this a real trigger"
and "is this actually the user," both happen before any transcription
or command matching occurs.

## Stack

| Layer | Tool | Why | Cost |
|---|---|---|---|
| Voice activity detection | `silero-vad` | Tiny, always-on, catches "someone is speaking" | Free |
| Wake word | `openWakeWord`, custom "hey ekko" model (`listener/models/hey_ekko.onnx`) | Open source end to end: its training pipeline built the model from synthetic samples, no recorded dataset and no paid service. CPU-light at inference | Free |
| Speaker verification | `speechbrain` (ECAPA-TDNN, pretrained on VoxCeleb) | Pretrained, don't train from scratch, sub-1% EER on standard benchmarks | Free |
| Transcription | `faster-whisper` (local, CTranslate2). Model `auto`: medium on a CUDA GPU, small (int8) on a CPU | Runs on a laptop CPU without a GPU, and uses one when present. No cloud STT dependency | Free |
| Intent routing | `sentence-transformers` (`all-MiniLM-L6-v2`) cosine similarity against a closed intent set | Regex proved too brittle for Whisper's phrasing variance. Still deterministic (same input, same output) and still a closed set, so no LLM in the routing decision, see `intent_routing.md` | Free |
| Command execution | `subprocess` calling scripts under `scripts/<os>/` (`.ps1` on Windows, `.sh` on Linux; picked by the `system.os` setting, see `routing/host.py`; user-specific handlers live in `personal/scripts/<os>/`) | Simple, auditable, and constrained: only files in that one directory can run | Free |
| Compute (CV, later) | To be decided | Deterministic CV (OpenCV/YOLO) preferred over VLM unless semantic understanding is required | Free |
| Audio feedback | Piper TTS + `sounddevice` | Local, CPU-fast, zero cost. Synthesizes every response at runtime through one code path, fixed system text now, LLM-generated text later, no separate pre-recorded-WAV tier | Free |
| NO_MATCH fallback | LLM, see `llm_fallback/` | One call re-checks for a paraphrased/misheard command and, if there isn't one, answers an open-ended question directly; closed-set and independently re-validated before anything runs, no path to execution for an answer, not a loosening of "no LLM in the routing decision" | Any free-tier LLM works; Gemini's free tier gives the best results, self-hosted Llama is the best option if you have the hardware for it |

## Setup

See the readme for install and dev commands.
Voice models (wake word, Piper voice) are downloaded on first run by `models.py`, not stored in the repo.
Speaker verification needs your own enrollment (`voice_auth/auedio.md`); without it the listener runs on the wake word alone.
API keys go in `.env` (or the app's Models panel); every other setting is in the settings database.

## Status

Build progress and known issues are tracked in GitHub issues.

## Constraints to respect in any future work on this project

- Zero budget. Every dependency and service must be free.
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
