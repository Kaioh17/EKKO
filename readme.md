# EKKO

## Demo

<video src="https://github.com/Kaioh17/EKKO/raw/main/docs/demo/check_battery_2.mp4" controls width="640"></video>

[▶ Watch the demo](docs/demo/check_battery_2.mp4) — asking EKKO for a battery check, wake word to spoken answer.

---

Right now, EKKO listens for a wake word (currently "hey jarvis," since training a custom "hi ekko" model isn't in the budget yet), checks that it's actually me speaking and not a roommate or a recording, transcribes the command, and runs it if it falls into a fixed, known set of actions, opening an app, checking system stats, searching the web, that kind of thing.

For anything more ambiguous ("how many Marvel movies are there?"), it falls back to an LLM. Right now that's Gemini; swap in Llama if you want to keep things fully local and private. It talks back out loud. No camera yet.

## Where this is headed

It's currently built to run only on Windows. Making it OS agnostic is the next real step, so the same core (wake word, speaker verification, transcription, routing) can run on Linux and Mac without a rewrite.

Past that, the same assistant, but with vision added, aware of the room and not just the mic, eventually able to act on its own within the boundaries it's given rather than only responding to spoken commands.

One rule stays constant through all of it: don't add intelligence where a simple, predictable rule already works.
# Project Brief

This document is the reference point for anyone (or any Claude Code
session) picking up this project. It captures what EKKO is, why it's
built the way it is, what's done, and what's still open. Treat it as
the source of truth for intent and architecture. Update it as
decisions get made, don't let it drift out of sync with the code.

## What EKKO is

A voice-and-vision assistant for a personal work lab setup. Long-term
goal is a fully autonomous system: eyes on the room, voice control
over the laptop, security tight enough that only the owner can issue
commands. Short-term goal is much narrower, get one piece working
end to end before adding the next.

Owner: Mubaraq, CS student, building this
solo, on a Windows laptop with an RTX 4070, on a zero budget. Every
tool choice in this project is free or open source. That constraint
is permanent, not temporary, don't propose paid services or APIs as
the default path.

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
   without confirming it came from Mubaraq specifically, not just
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
  → Matched PowerShell script executes
```

The staged structure is deliberate. Each stage only runs when the
one before it passes, so the expensive steps (speaker embedding,
transcription) never run continuously. This is also the security
gate: the two most consequential checks, "is this a real trigger"
and "is this actually Mubaraq," both happen before any transcription
or command matching occurs.

## Stack

| Layer | Tool | Why | Cost |
|---|---|---|---|
| Voice activity detection | `silero-vad` | Tiny, always-on, catches "someone is speaking" | Free |
| Wake word | `openWakeWord` | Open source, trainable on a custom phrase, CPU-light | Free |
| Speaker verification | `speechbrain` (ECAPA-TDNN, pretrained on VoxCeleb) | Pretrained, don't train from scratch, sub-1% EER on standard benchmarks | Free |
| Transcription | `faster-whisper` (local, CTranslate2, model size `small`) | Runs on the RTX 4070 (falls back to CPU if CUDA isn't available), no cloud STT dependency | Free |
| Intent routing | `sentence-transformers` (`all-MiniLM-L6-v2`) cosine similarity against a closed intent set | Regex proved too brittle for Whisper's phrasing variance. Still deterministic (same input, same output) and still a closed set, so no LLM in the routing decision, see `intent_routing.md` | Free |
| Command execution | `subprocess` calling PowerShell scripts in `scripts/` | Simple, auditable, and constrained: only files in that one directory can run | Free |
| Compute (CV, later) | To be decided | Deterministic CV (OpenCV/YOLO) preferred over VLM unless semantic understanding is required | Free |
| Audio feedback | Piper TTS + `sounddevice` | Local, CPU-fast, zero cost. Synthesizes every response at runtime through one code path, fixed system text now, LLM-generated text later, no separate pre-recorded-WAV tier | Free |
| NO_MATCH fallback | LLM, see `llm_fallback/` | One call re-checks for a paraphrased/misheard command and, if there isn't one, answers an open-ended question directly; closed-set and independently re-validated before anything runs, no path to execution for an answer, not a loosening of "no LLM in the routing decision" | Any free-tier LLM works; Gemini's free tier gives the best results, self-hosted Llama is the best option if you have the hardware for it |

## Setup

1. `python -m venv venv` (or `pvenv` on Windows) and activate it, then
   `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env` in the project root and fill in
   `GEMINI_API_KEY` (free tier, see `llm_fallback/gemini/README.md`).
   `.env` is gitignored, never commit real keys.
3. Voice models aren't in the repo (large binary files, gitignored):
   `feedback/models/` needs a Piper `.onnx` voice (see
   `feedback/README.md`), and `voice_auth/` needs its own enrollment
   run (see `voice_auth/auedio.md`) before speaker verification works.
4. `scripts/` (the PowerShell handlers `routing/` calls into) is
   gitignored too, it's this machine's local automation and has
   hardcoded Windows paths. Recreate the ones you need, or ask for
   them, before wiring up execution end to end.

## Environment

Windows laptop, RTX 4070 GPU. Original plan was native Windows
Python end to end, to avoid WSL2's audio handling. WSL2 has no native
audio hardware access, WSLg bridges it via PulseAudio over RDP, which
works but adds latency and setup fragility, not ideal for an
always-on VAD listener.

In practice, recording and enrollment were run from a WSL shell
(with the venv living on the Windows-mounted drive) and worked fine.
That's a useful data point but not yet a final decision, the
always-on listening loop is a different latency profile than a
one-off recording, and hasn't been tested yet. **Open decision:**
confirm whether native Windows or WSL2 (via WSLg) is the long-term
execution environment before building the continuous listening loop.
If WSL2 turns out fine under real always-on load, there's no need to
force a native-Windows-only rule.

## Status

Build progress, what's working, known issues: see `status.md`
(gitignored, local-only, not part of the public repo).

## Constraints to respect in any future work on this project

- Zero budget. Every dependency and service must be free.
- Deterministic before AI, for any new feature, default to
  hardcoded logic and only escalate to a model if the task can't be
  solved that way.
- Local-first for audio/video, no cloud processing by default.
- Execution is a fixed set of pre-approved actions (see `status.md`'s
  Stage 3 section for how that's enforced). An action exists only if
  it's a script in `scripts/` named by an intent in
  `routing/intents.yaml`. Arbitrary shell access is not on the roadmap.
- No destructive or state-reversing command without a separate,
  explicit decision. That one is still unmade, and the routing layer's
  inability to tell "close X" from "open X" is a concrete reason to
  keep it that way until there's a real answer.
