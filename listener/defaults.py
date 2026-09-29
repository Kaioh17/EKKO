"""User-tunable listener defaults, importable without torch/whisper (the
backend's settings schema reads them; importing vad_listener.py takes
~20s). vad_listener.py re-exports every name here."""

import os

import models
from paths import data

# User data, so under paths.DATA_DIR (the repo itself in dev).
DEFAULT_SAVE_DIR = str(data("listener", "captures"))
DEFAULT_WAKE_WORD = str(models.path("hey_ekko.onnx"))  # "hey ekko", custom-trained; fetched on first run
DEFAULT_VAD_THRESHOLD = 0.5
# Silence that ends a segment while idle (the wake word phrase). Short so
# activation stays snappy.
DEFAULT_MIN_SILENCE_MS = 300
# Silence that ends a segment during active listening (the command).
# Longer so a mid-sentence pause doesn't split the command in two.
DEFAULT_COMMAND_MIN_SILENCE_MS = 1500
DEFAULT_WAKE_THRESHOLD = 0.5
DEFAULT_ACTIVE_WINDOW_S = 10.0
# Unlikely to already be bound system-wide and awkward to hit by
# accident. Physical-access bypass, not an identity check (see manual
# wake handling in listen()), so it only needs to avoid misfires.
#
# Must end in a non-modifier trigger key ('w'), same as the hard stop
# chord below: `keyboard` only enforces the full chord when the last key
# is a real trigger. An all-modifier chord like "ctrl+windows" matches
# loosely (fires on bare ctrl, refires on repeat), which misfired on
# every unrelated Ctrl shortcut.
DEFAULT_MANUAL_WAKE_HOTKEY = "ctrl+alt+w"
# Same "unlikely to collide, awkward to hit by accident" bar as manual
# wake, but must stay distinct from it since one starts a listen and the
# other kills one. Not ctrl+fn (Fn is handled by keyboard firmware on
# most hardware and never reaches Windows as a scancode, so `keyboard`
# has no mapping and raises rather than failing to fire) and not
# ctrl+space (Windows' own IME/language-switch shortcut).
DEFAULT_HARD_STOP_HOTKEY = "ctrl+alt+q"
# Enrolled voice print: user data, like the captures.
DEFAULT_REFERENCE = str(data("voice_auth", "reference_embedding.pt"))
# "auto" = pick_whisper() below: medium on a CUDA GPU, small on CPU.
DEFAULT_WHISPER_MODEL = "auto"
DEFAULT_TRANSCRIPT_LOG = str(data("listener", "captures", "transcripts.jsonl"))
# How long to keep ignoring the mic after EKKO's audio actually stops.
# Room decay only -- speak() measures the output device's own buffering
# and reports the real end-of-playback time. See MicGate.
DEFAULT_FEEDBACK_TAIL_MS = 300
# A segment shorter than this is a click, a door, or an ack tail, not a
# command -- drops transients, not a length filter (real commands can be
# short, e.g. "mute"). Duration alone can't tell speech from decay
# anyway; that's what the identity/confidence checks below are for.
DEFAULT_MIN_COMMAND_MS = 300
# Cosine similarity floor for the *command* segment, far below the wake
# word's. See _screen_command_segment().
#
# Measured on captures/: enrolled speaker scores 0.379-0.714, EKKO's own
# ack tail scores 0.050-0.086. 0.25 sits near the bottom of that gap so a
# tighter cutoff doesn't cost real commands.
#
# Exception: a near-silent tail scored 0.274, past this by only 0.024 --
# embeddings of near-noise audio are unstable, which is why identity
# isn't the only check (see the confidence thresholds below).
DEFAULT_COMMAND_VERIFY_THRESHOLD = 0.25
# Whisper's own confidence there was speech at all (from _transcribe()).
#
# Measured on the same captures: real commands ran no_speech_prob
# 0.004-0.342, avg_logprob -0.35 to -0.85; a false-accept tail sat at
# 0.571/-1.03. These defaults sit between the two, biased toward
# rejecting since a false reject just costs a repeat while a false accept
# spends the turn on a sound the user didn't make.
#
# Limited layer: one echo tail scores 0.323/-0.90 while a genuine command
# ("Have a great day, Javis.") scores 0.342/-0.83 -- worse on
# no_speech_prob than the tail. They interleave, so tightening these
# further only costs real commands; identity verification is what
# actually separates them (0.086 vs 0.379+). Under --no-verify this is
# the only check left and will let some tails through.
DEFAULT_MAX_NO_SPEECH_PROB = 0.45
DEFAULT_MIN_AVG_LOGPROB = -0.95
# Biases Whisper's decoding toward EKKO's vocabulary via initial_prompt
# (prior context, not a transcript prefix) -- matters most for short,
# easily-confused commands ("mute" vs "moot").
DEFAULT_INITIAL_PROMPT = (
    "Hey Ekko, run system analysis. Open task manager. Lock the screen. "
    "Play. Pause. Next track. Previous track. Volume up. Volume down. Mute. "
    "Open Chrome. Open Brave. Open Spotify. Search Brave for the weather. "
    "Already done. Command confirmed. Access denied."
)


def pick_whisper(model: str, cuda_devices: int) -> tuple[str, str, str]:
    """(model size, device, compute_type). "auto" trades accuracy for speed
    on CPU-only laptops, where medium is too slow for live commands."""
    if cuda_devices > 0:
        return ("medium" if model == "auto" else model), "cuda", "float16"
    return ("small" if model == "auto" else model), "cpu", "int8"


def whisper_cpu_threads() -> int:
    # Half the logical cores: leaves room for VAD, wake word and TTS.
    return max(1, (os.cpu_count() or 2) // 2)
