"""
Always-on voice activity detection. First stage of the listening pipeline:
runs continuously on the mic, cheap enough to leave on all the time, and
only reacts when it actually hears speech.

Silero VAD processes fixed-size chunks (512 samples at 16kHz, ~32ms) and
outputs a speech probability per chunk. VADIterator turns that stream of
probabilities into speech-start / speech-end events, with padding and a
minimum silence gap so it doesn't chop a sentence into fragments on every
short pause.

Every chunk is also fed to openWakeWord, continuously, not just while VAD
thinks someone's talking, that's the intended usage pattern and it avoids
clipping the wake word's onset while VAD is still deciding a segment has
started.

Identity is checked at the wake word, not after it: once "hey jarvis"
fires and that speech segment ends, *that* segment is what gets run
through speaker verification, right there, before anything else happens.
Only a match arms active listening: it waits (up to --active-window
seconds) for the *next* speech segment and treats that one as command
content. A non-match drops straight back to idle,
same as if the wake word had never fired. This inverts an earlier design
that verified the command segment instead of the wake word segment, on
the theory that "hey jarvis" (two words, fixed phrasing) was too short
and acoustically narrow for the speaker embedding to be reliable. That's
no longer the constraint it was: verify.py now has a short-clip reference
bucket and a calibrated threshold for exactly this length of audio (see
voice_auth/verify.py's SHORT_BUCKET). It was calibrated on natural short
commands, though, not on "hey jarvis" specifically, acoustically a
narrower, more repetitive phrase, so re-tune --verify-threshold (see
--skip-wake below) actually saying the wake word during tuning, don't
assume the existing default transfers as-is.

Checking identity at the wake word also means a verified user gets
audible acknowledgment (see --no-feedback) immediately, the same "go
ahead, I'm listening" cue Alexa/Google Assistant give after their wake
word, rather than only after the whole command has been said and
verified. The mic is gated shut until that acknowledgment has genuinely
stopped coming out of the speakers, plus a short room-decay tail
(--feedback-tail-ms), otherwise EKKO hears itself say it and transcribes
the greeting back as your command, which is exactly what it did until
MicGate existed, see that class for the details.

Active listening does not hand the first thing it hears to the router.
The segment is screened first (see _screen_command_segment): long enough
to be speech, from the enrolled speaker, and something Whisper is
actually confident was said. A segment that fails is discarded and
active listening simply continues, so a cough, a door, or a stray echo
costs nothing rather than costing the user their turn, which is what
happened while any first segment ended it. Only --active-window elapsing
drops back to idle. Note the identity check here is a second, much
looser one than the wake word's (--command-verify-threshold): it exists
to reject audio that isn't you, not to authorize you, since the wake
word already did that.

A segment that passes is transcribed locally with faster-whisper. That
transcript then goes to routing/ (Whisper -> intent match -> slot
extraction -> IntentBundle), and a matched bundle's PowerShell handler is
run. Segments where the wake word never fires are dropped without ever
touching the speaker model, that's the whole point of gating on it.

This is where EKKO's permission boundary actually takes effect, so it's
worth being precise about how narrow it is. A speech segment only reaches
the router if the wake word fired AND the speaker embedding matched. What
the router can return is bounded by routing/intents.yaml, and what that
can name is bounded to .ps1 files under scripts/. There is no path from
speech to an arbitrary command, by construction rather than by check.
--no-execute keeps everything above but stops short of running anything.

A transcript the router can't place (NO_MATCH) gets one more constrained
attempt via llm_fallback/ before EKKO gives up on it: a single direct HTTPS
call to Gemini's API (llm_fallback/gemini/fallback_gemini.py, no CLI
subprocess, no filesystem/shell access), that either picks from the same
routing/intents.yaml set (re-validated against that file independently
before it's trusted, same bound as the router's own) or, if it's not a
command at all, answers the transcript directly as an open-ended question
and speaks the answer, with no IntentBundle and no path to execute() at all.
One call handles both, see llm_fallback/README.md for why that used to be
two sequential calls and no longer is (that reasoning predates the move
from Claude to Gemini but still holds -- see
llm_fallback/gemini/README.md for why Gemini replaced Claude here: real
usage showed Claude's CLI cold-start and Haiku's extended-thinking token
spend were the actual latency source, not the model choice itself).
--no-llm-fallback skips this and goes straight to "didn't catch that", as
if llm_fallback/ didn't exist. An open-ended answer also pops a Brave
Search tab for the transcript, plus up to 3 pages the fallback model found
via search while answering, when search is enabled (see
scripts/open_research_tabs.ps1 and llm_fallback/gemini/SYSTEM_PROMPT.md's
`urls` field) -- best-effort and non-blocking, never affects what gets
spoken. Google Search grounding is OFF by default for the live Gemini path
(see llm_fallback/gemini/README.md's "Known issue" section: it has zero
quota on a free, no-billing API key), so `urls` is empty and this tab is
just a plain search for the transcript today, not curated results -- worth
knowing before assuming this feature is doing more than it currently can.
EKKO follows the answer with a short "pulling up some helpful sites" aside
(feedback.speech.RESPONSES' research_tabs_opened key), only once Brave
actually launched, never on a missing-Brave or script-error outcome.
--no-research-tabs skips
both the tabs and that aside, keeping just the spoken answer.

Depends on voice_auth (enroll.py's speaker model, verify.py's comparison)
for the identity-check stage, but voice_auth has no dependency back on
this module: VAD/wake-word listening and speaker verification are
separate concerns, this is the pipeline that consumes that library.

Usage (run from anywhere, paths below resolve relative to this file):
    python listener/vad_listener.py
    python listener/vad_listener.py --threshold 0.6 --save-dir captures
    python listener/vad_listener.py --no-save            # don't keep permanent captures
    python listener/vad_listener.py --no-verify           # wake word only, skip identity check
    python listener/vad_listener.py --no-transcribe       # verify only, skip transcription
    python listener/vad_listener.py --wake-threshold 0.6  # stricter wake word matching
    python listener/vad_listener.py --skip-wake           # tuning mode, see below
    python listener/vad_listener.py --no-feedback         # skip the audio acknowledgment
    python listener/vad_listener.py --no-execute          # route and say the outcome, run nothing
    python listener/vad_listener.py --no-llm-fallback     # skip the NO_MATCH second pass
    python listener/vad_listener.py --manual-wake-hotkey ctrl+alt+e  # rebind manual wake
    python listener/vad_listener.py --no-manual-wake      # disable the manual wake hotkey
    python listener/vad_listener.py --hard-stop-hotkey ctrl+pause  # rebind hard stop
    python listener/vad_listener.py --no-hard-stop        # disable the hard stop hotkey

--skip-wake is the test functionality for tuning --verify-threshold: it
skips the wake word requirement entirely and verifies every speech
segment directly and immediately, as if each one were the wake word
segment, so you can repeat "hey jarvis" (or whatever you want to test)
back-to-back and watch similarity scores without waiting on active
listening or a command each time.

Manual wake (--manual-wake-hotkey, default ctrl+alt+w) is a different
door into active listening, and doesn't go through the wake word or
speaker verification at all: pressing it jumps straight to the same
state a verified "hey jarvis" produces. That's deliberate, not a gap --
being able to press a key on this machine already meets the same
physical-presence bar the mic and scripts/ are behind, so gating the
hotkey behind a voice check it doesn't need would just be theater. It's
useful in a noisy room where the wake word keeps missing, or for
exercising routing/intents.yaml changes without saying anything out
loud. Requires the `keyboard` package (not in every venv, since nothing
else here needs a global key hook); its absence disables the hotkey with
a warning rather than failing the listener.

Hard stop (--hard-stop-hotkey, default ctrl+alt+q) is the other
direction: it kills an active listen rather than starting one. One press
does three things immediately -- stops any TTS audio already coming out
of the speakers (sd.stop(), not waiting for it to finish the sentence),
reopens the mic gate right away instead of waiting out the usual
echo-decay tail, and drops out of active listening/follow-up state back
to idle. No acknowledgment is spoken; the whole point is not to add more
audio after telling it to stop. Same physical-presence reasoning as the
manual wake hotkey applies here too. It needs the `keyboard` package;
not ctrl+fn, deliberately -- on most keyboards Fn is trapped by the
keyboard's own firmware and never reaches Windows as a real scan code,
so `keyboard` has no mapping for it at all and add_hotkey() raises
ValueError rather than just failing to fire. That's caught at
registration (same as the package being absent), so a bad chord disables
the hotkey with a warning instead of crashing the listener; rebind with
--hard-stop-hotkey to whatever chord your keyboard actually reports.
"""

import argparse
import datetime
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import NamedTuple

import numpy as np
import openwakeword
import sounddevice as sd
from faster_whisper import WhisperModel
from openwakeword.model import Model as WakeWordModel
from scipy.io.wavfile import write as wav_write
from silero_vad import VADIterator, load_silero_vad

try:
    # Optional: only needed for --manual-wake-hotkey. Not in every
    # environment's venv (it's a global-hook library, not something the
    # rest of the pipeline touches), so this degrades to "hotkey
    # disabled" rather than failing the whole listener over it, same
    # posture as the Piper voice below.
    import keyboard
except ImportError:
    keyboard = None

try:
    # Newer openwakeword releases ship the pretrained models as separate
    # downloads to keep the package small, fetched on first use and cached
    # locally after that. Older releases (e.g. 0.4.0) bundle the ONNX
    # models directly in the package instead, no download step or module
    # to import, in which case this just isn't needed.
    from openwakeword.utils import download_models
except ImportError:
    download_models = None

# voice_auth is a sibling package, not a sibling module, now that this
# file lives in listener/ instead of voice_auth/ itself. Put the repo
# root on sys.path so it resolves regardless of invocation cwd (`python
# listener/vad_listener.py` from anywhere, or `python -m listener.vad_listener`).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from feedback.speech import DEFAULT_MODEL_PATH as DEFAULT_FEEDBACK_MODEL
from feedback.speech import load_voice as load_feedback_voice
from feedback.speech import render as render_response
from feedback.speech import speak
from routing.bundle import RoutingStatus
from routing.config import ConfigError
from routing.execute import execute, response_key, spoken_override
from routing.matcher import DEFAULT_THRESHOLD as DEFAULT_ROUTING_THRESHOLD
from routing.route import Router
from routing.domains import DomainRouter
from domains.loader import compose_system_instruction
from llm_fallback.gemini.fallback_gemini import SYSTEM_PROMPT_PATH, attempt_fallback
from llm_fallback.claude_code.fallback import open_research_tabs
from listener.prune_captures import DEFAULT_KEEP as DEFAULT_KEEP_CAPTURES
from listener.prune_captures import prune as prune_captures
from memory.store import log_decision as log_memory_decision, read_memory, write_memory
from memory.scoring import PendingFactCache, propose_and_score
from memory.short_term import clear_short_memory, read_short_memory, write_short_memory
from ui.voice_ui import SESSION_DEFAULT_TIMEOUT_S, VoiceUI, VoiceUIState
from voice_auth.enroll import load_model as load_speaker_model
from voice_auth.enroll import load_reference
from voice_auth.verify import verify

# Cross-process signal from scripts/daily_briefing.py: written the moment
# that script finishes speaking its top-news narration (see its
# speak_top_news()), so this process -- which has no other way to know
# that a detached window in a different process just stopped talking --
# can re-arm "anything else?" at the right time instead of either racing
# it (re-arming immediately, see _NO_FOLLOW_UP_INTENTS) or never re-arming
# at all. Same fixed-path, filesystem-signal approach as
# feedback/speech.py's own cross-process playback lock, for the same
# reason: none of these processes share memory.
BRIEFING_NARRATION_FLAG_PATH = Path(tempfile.gettempdir()) / "ekko_briefing_narration_done.flag"
# How long this process waits for that flag before giving up quietly.
# daily_briefing.py only writes the flag after BOTH its narration steps
# finish -- news and then stocks, each an independent feedback/speech.py
# subprocess that reloads the Piper voice from scratch and plays back in
# real time (its own _tts_timeout() alone allows ~50s for a long
# multi-ticker stock line), on top of the news/stock fetches and two
# Gemini calls. A day with rich commentary for both sections can push
# that total past a minute and a half, so this is generous relative to
# it; it exists so a briefing window that crashed, was closed early, or
# never got anything to narrate doesn't leave this process waiting
# forever for a flag that's never coming.
BRIEFING_FOLLOWUP_TIMEOUT_S = 180.0
# How often the main loop actually stats the flag file while waiting for
# it. The loop iterates roughly every 32ms (one audio chunk), checking the
# filesystem that often is wasted work for a flag that takes seconds to
# appear -- this throttles it to something a human waiting on a spoken
# follow-up won't perceive as a delay, without hammering the disk 30x/sec.
BRIEFING_FOLLOWUP_POLL_S = 1.0

SAMPLE_RATE = 16000  # required by the model
CHUNK_SAMPLES = 512  # required chunk size at 16kHz, see silero_vad/utils_vad.py
# Also anchored to this file rather than cwd, same reasoning as
# DEFAULT_REFERENCE below: usage examples run this as `python
# listener/vad_listener.py` from the repo root, where a bare relative
# "captures" would land at the repo root instead of listener/captures.
DEFAULT_SAVE_DIR = str(Path(__file__).resolve().parent / "captures")
DEFAULT_WAKE_WORD = "hey_jarvis"
# A chord unlikely to already be bound to something else system-wide
# (unlike e.g. ctrl+space) and awkward to hit by accident. This is a
# physical-access bypass, not an identity check, so the chord only
# needs to avoid misfires, not resist an adversary at the keyboard --
# see the manual wake handling in listen() for why it skips
# verification entirely rather than trying to.
#
# Must end in a non-modifier trigger key (here 'w'), same as the hard
# stop chord below. `keyboard` only enforces the full combination when
# the last key is a real trigger; an all-modifier chord like
# "ctrl+windows" matches loosely -- it fires on a bare `ctrl` press and
# re-fires on key-repeat while held -- which had this misfiring on every
# unrelated Ctrl shortcut.
DEFAULT_MANUAL_WAKE_HOTKEY = "ctrl+alt+w"
# Same "unlikely to collide, awkward to hit by accident" bar as the
# manual wake chord above, but distinct from it, the two need to be
# unmistakable from each other since one starts a listen and the other
# kills one. Not ctrl+fn (Fn is handled by the keyboard's own firmware on
# most hardware and never reaches Windows as a scancode at all, so the
# `keyboard` package has no mapping for it and raises rather than just
# failing to fire, see the registration try/except below) and not
# ctrl+space (that's Windows' own system-wide input-method/language
# switch shortcut, likely to get intercepted before our hook sees it, and
# to fire the language switcher as an unwanted side effect). All-letter
# chord instead, no dependency on an oddball key some keyboards omit.
DEFAULT_HARD_STOP_HOTKEY = "ctrl+alt+q"
# Absolute, not cwd-relative: reference_embedding.pt lives in voice_auth/,
# a different directory than this file, so a bare relative default would
# only work if the caller happened to have voice_auth/ as their cwd.
DEFAULT_REFERENCE = str(_PROJECT_ROOT / "voice_auth" / "reference_embedding.pt")
DEFAULT_WHISPER_MODEL = "medium"
# Same anchoring rationale as DEFAULT_SAVE_DIR above.
DEFAULT_TRANSCRIPT_LOG = str(Path(__file__).resolve().parent / "captures" / "transcripts.jsonl")
# How long to keep ignoring the mic after EKKO's audio has actually
# stopped coming out of the speakers. Room decay only: the output
# device's own buffering is no longer this number's problem, speak()
# measures that and reports a real end-of-playback time for the gate to
# start counting from. See MicGate.
DEFAULT_FEEDBACK_TAIL_MS = 300
# A command segment shorter than this is a click, a door, or the tail of
# EKKO's own acknowledgment, not a command. Deliberately low: this is
# here to drop transients, not to filter by length. Real commands can be
# genuinely short ("mute"), and the 0.61s echo tail that motivated all of
# this proves duration alone can't tell speech from decay anyway, that's
# what the identity and confidence checks are for.
DEFAULT_MIN_COMMAND_MS = 300
# Cosine similarity floor for the *command* segment, far below the wake
# word's. See _screen_command_segment().
#
# Measured over the 18:20 captures in captures/: the enrolled speaker
# scores 0.379-0.714, EKKO's own acknowledgment tail scores 0.050 and
# 0.086. So the gap this sits in is enormous, and 0.25 is deliberately
# nearer the bottom of it, a tight cutoff here would start costing real
# commands (the 0.379 is a genuine one) to buy margin against clips that
# are already an order of magnitude away.
#
# The exception is the near-silent tail, which scored 0.274 and got past
# this by 0.024. Embeddings of something that's mostly noise floor are
# unstable and land more or less anywhere, so identity can't be the only
# check, that clip is what the confidence thresholds below are sized for.
DEFAULT_COMMAND_VERIFY_THRESHOLD = 0.25
# Whisper's own confidence that there was speech at all. Both are read
# off the segments it returns; see _transcribe().
#
# Same captures, same method, rather than round numbers. Real commands
# ran no_speech_prob 0.004-0.342 and avg_logprob -0.35 to -0.85; the tail
# clip that got past the identity check sat at 0.571 / -1.03. These two
# defaults are placed in the middle of that gap. Re-derive them the same
# way if the mic or the room changes, don't nudge them by feel.
#
# Both are biased towards rejecting, because the costs aren't symmetric
# anymore: a false reject means EKKO keeps listening and you say it
# again, while a false accept spends the turn on a sound you didn't make.
#
# Worth being clear about the limit of this layer, since it looks more
# capable than it is. Of the three echo tails in captures/, one scores
# 0.323/-0.90 while a genuine command ("Have a great day, Javis.") scores
# 0.342/-0.83, i.e. worse on no_speech_prob than the clip we want gone.
# They interleave, so no threshold here separates them and tightening
# these numbers only starts costing real commands. Identity is the check
# that actually does the work (it split the same clips 0.086 vs 0.379+);
# this is a backstop for junk that isn't a voice at all, and under
# --no-verify it is the only check left and will let some tails through.
DEFAULT_MAX_NO_SPEECH_PROB = 0.45
DEFAULT_MIN_AVG_LOGPROB = -0.95
# Biases Whisper's decoding toward EKKO's actual vocabulary (wake phrase,
# the command set routing/intents.yaml knows how to handle, and the fixed
# responses feedback.speech can say back) rather than nudging thresholds.
# This is passed as initial_prompt, not a real transcript prefix: Whisper
# treats it as prior context and leans toward similar words/phrasing when
# the audio is ambiguous, which matters most for short, easily-confused
# commands ("mute" vs "moot", "next track" vs "next track's").
DEFAULT_INITIAL_PROMPT = (
    "Hey Jarvis, run system analysis. Open task manager. Lock the screen. "
    "Play. Pause. Next track. Previous track. Volume up. Volume down. Mute. "
    "Open Chrome. Open Brave. Open Spotify. Search Brave for the weather. "
    "Already done. Command confirmed. Access denied."
)


class Transcription(NamedTuple):
    """What Whisper said, plus how sure it was that anyone said anything.

    The confidence fields exist because the text alone is not evidence.
    Handed a sub-second clip of room decay, Whisper doesn't return an
    empty string, it returns a confident-looking "Bye." or "Thank you."
    from the filler phrases its training data is full of. Those two
    hallucinations are what sent a real command's turn to the router as a
    NO MATCH. no_speech_prob and avg_logprob are the model's own answer
    to "was that actually speech", and they're the only part of its
    output that separates those clips from a genuine short command.

    Produced by _transcribe(), judged by _screen_command_segment().
    """

    text: str
    no_speech_prob: float
    avg_logprob: float


class MicGate:
    """Keeps EKKO's own voice out of EKKO's own microphone.

    feedback.speech.speak() blocks until playback finishes, but blocking
    the *main* loop doesn't stop the mic: sounddevice's input callback
    runs on its own thread and kept filling the queue throughout, so the
    acknowledgment was still sitting in the buffer when the loop resumed
    and got handed to VAD as the next speech segment. In practice that
    meant every wake word spent its command slot transcribing "Hi
    Mubaraq, what are we doing today?" back to itself, and those
    transcripts are still in captures/transcripts.jsonl.

    So the gate is checked in the callback rather than around it: while
    closed, chunks are dropped instead of queued, and nothing downstream
    (VAD, wake word, verification, Whisper) ever sees them.

    Gating for exactly the duration of the speak() call turned out not to
    be enough, and the second-order version of the same bug is why
    reopen_after() takes a timestamp instead of using "now". sd.wait()
    returns when PortAudio has handed off its last buffer, not when the
    sound is gone: the device still has a few hundred ms queued, and the
    room rings on after that. What leaked through was no longer the whole
    greeting, just its final syllable decaying to nothing, which Silero
    read as a speech segment and Whisper hallucinated into "Bye." and
    "Thank you." (both still in captures/transcripts.jsonl, both with a
    telltale envelope that peaks in the first 100ms and decays straight
    to the noise floor).

    The fix is to stop guessing at that gap. speak() measures the output
    stream's latency and returns the time playback genuinely ends, so the
    only thing left for this class to add is room decay (tail_seconds),
    which is what a constant can honestly describe. It also means the
    mute window scales with the audio automatically, however long a
    future response turns out to be.

    Read from the audio thread, written from the main one. That's safe
    without a lock: the only shared state is a float deadline and a bool,
    each written by exactly one thread in one place, and a chunk landing
    on either side of the boundary is a chunk of silence either way.
    """

    def __init__(self, tail_seconds: float) -> None:
        self.tail_seconds = tail_seconds
        self._speaking = False
        self._muted_until = 0.0

    def is_open(self) -> bool:
        return not self._speaking and time.monotonic() >= self._muted_until

    def close(self) -> None:
        self._speaking = True

    def reopen_after(self, playback_done_at: float) -> None:
        """playback_done_at is speak()'s end-of-playback timestamp, not
        the current time. Order still matters: arm the deadline before
        clearing the flag, or a chunk can slip through in between.
        """
        self._muted_until = playback_done_at + self.tail_seconds
        self._speaking = False

    def force_open(self) -> None:
        """Reopen the mic immediately, skipping the echo-decay tail.

        Used only by the hard-stop hotkey: reopen_after() deliberately
        waits out tail_seconds because in the normal flow there's real
        room decay to wait past, but a hard stop just killed the audio
        with sd.stop(), there's nothing left ringing to mute against, and
        making the user wait out a tail that no longer applies would
        defeat the point of a *hard* stop.
        """
        self._speaking = False
        self._muted_until = 0.0


def _build_wake_word_model(wake_word: str) -> WakeWordModel:
    """openWakeWord's Model constructor has changed across versions, and
    inspect.signature() isn't a reliable way to detect which shape is
    installed: some releases wrap __init__ in a deprecated-kwarg shim
    decorator that doesn't set __wrapped__, so the signature it reports
    is a generic (*args, **kwargs) no matter what the real parameters
    are. Just try the modern kwarg directly and fall back to the older
    one on failure instead of introspecting.
    """
    try:
        # Modern API (newer releases): wakeword_models takes bare
        # pretrained names directly (e.g. "hey_jarvis") and
        # resolves/downloads the matching model file itself.
        # inference_framework="onnx" since this project depends on
        # onnxruntime, not tflite_runtime (the other option, and this
        # constructor's own default).
        return WakeWordModel(wakeword_models=[wake_word], inference_framework="onnx")
    except TypeError:
        pass

    # Older releases (e.g. 0.4.x) bundle the pretrained models directly
    # rather than downloading them, want a full path via
    # wakeword_model_paths instead, and expose the registry as a
    # lowercase `models` dict rather than the newer `MODELS`.
    registry = getattr(openwakeword, "models", None) or getattr(openwakeword, "MODELS", None)
    model_info = registry.get(wake_word) if registry else None
    if model_info is None:
        raise ValueError(
            f"No bundled openWakeWord model named {wake_word!r}. "
            f"Available: {sorted(registry) if registry else '(none found)'}"
        )
    return WakeWordModel(wakeword_model_paths=[model_info["model_path"]])


def _resolve_prediction_key(oww_model: WakeWordModel, wake_word: str) -> str:
    """predict() keys its result dict by whatever name the loaded model
    ended up with, which isn't always `wake_word` verbatim: 0.4.x derives
    it from the bundled filename (e.g. "hey_jarvis" -> "hey_jarvis_v0.1"),
    while newer releases keep the name you passed in. Resolve it once at
    startup instead of guessing the key on every chunk in the hot loop.
    """
    if wake_word in oww_model.models:
        return wake_word
    for key in oww_model.models:
        if key.startswith(wake_word):
            return key
    raise ValueError(
        f"Loaded openWakeWord model(s) {sorted(oww_model.models)} don't match {wake_word!r}"
    )


def listen(
    threshold: float,
    min_silence_ms: int,
    speech_pad_ms: int,
    save_dir: str | None,
    wake_word: str,
    wake_threshold: float,
    active_window: float,
    verify_enabled: bool,
    reference_path: str,
    verify_threshold: float | None,
    skip_wake: bool,
    transcribe_enabled: bool,
    whisper_model_size: str,
    transcript_log: str,
    feedback_enabled: bool,
    feedback_model: str,
    feedback_tail_ms: int,
    execute_enabled: bool,
    routing_threshold: float,
    min_command_ms: int,
    command_verify_threshold: float,
    max_no_speech_prob: float,
    min_avg_logprob: float,
    llm_fallback_enabled: bool,
    research_tabs_enabled: bool,
    keep_captures: int,
    manual_wake_hotkey: str | None,
    hard_stop_hotkey: str | None,
    ui_enabled: bool = True,
    response_session_timeout: float = SESSION_DEFAULT_TIMEOUT_S,
    domain_routing_enabled: bool = True,
    memory_enabled: bool = True,
) -> None:
    print("Loading Silero VAD model...")
    model = load_silero_vad()
    vad = VADIterator(
        model,
        threshold=threshold,
        sampling_rate=SAMPLE_RATE,
        min_silence_duration_ms=min_silence_ms,
        speech_pad_ms=speech_pad_ms,
    )

    print(f"Loading openWakeWord model ({wake_word})...")
    if download_models is not None:
        # Fetches the ONNX model files from the openWakeWord GitHub
        # releases on first run, a few MB, cached locally after that.
        # No-op if they're already there. Older openwakeword versions
        # bundle the models directly and don't need this at all.
        download_models([wake_word])
    oww_model = _build_wake_word_model(wake_word)
    prediction_key = _resolve_prediction_key(oww_model, wake_word)

    speaker_model = None
    reference = None
    if verify_enabled:
        print("Loading speaker verification model...")
        speaker_model = load_speaker_model()
        reference = load_reference(reference_path)

    whisper_model = None
    router = None
    domain_router = None
    pending_facts = None
    if transcribe_enabled:
        print(f"Loading faster-whisper model ({whisper_model_size})...")
        whisper_model = _load_whisper_model(whisper_model_size)

        # Routing is tied to transcription rather than given its own
        # on/off flag: with no transcript there is nothing to route.
        print("Loading intent router...")
        try:
            router = Router.load(threshold=routing_threshold)
        except ConfigError as exc:
            # Unlike the Piper voice below, this isn't a nice-to-have that
            # the pipeline can shrug off. A malformed intents.yaml means
            # every command would silently no-match, which looks like a
            # microphone problem rather than a config one. Fail at
            # startup, where the message is actionable.
            raise SystemExit(f"[routing] {exc}")

        if domain_routing_enabled and llm_fallback_enabled:
            # Reuses router.model (the same MiniLM instance) rather than
            # loading a second copy -- see DomainRouter.load()'s docstring.
            # Only meaningful alongside llm_fallback_enabled: domain
            # detection only ever runs on the NO_MATCH path into
            # attempt_fallback(), see _handle_command().
            print("Loading domain router...")
            domain_router = DomainRouter.load(router.model)

        if memory_enabled:
            pending_facts = PendingFactCache()

    feedback_voice = None
    if feedback_enabled:
        print("Loading Piper TTS voice...")
        try:
            feedback_voice = load_feedback_voice(feedback_model)
        except FileNotFoundError as exc:
            # Audio feedback is a nice-to-have layered on top of the
            # security-critical verification path, not a dependency
            # of it, don't let a missing voice model take down the
            # rest of the pipeline. Same "warn, don't crash" posture
            # as feedback.speech.speak() itself.
            print(f"  {exc}\n  continuing without audio feedback")

    ui = None
    if ui_enabled:
        # Runs in its own thread inside this same process (see
        # ui/voice_ui.py), sits hidden at near-zero cost until a state
        # change shows it. Purely cosmetic: nothing below ever branches on
        # `ui`, every call site is `if ui:` and the pipeline behaves
        # identically with --no-ui.
        ui = VoiceUI()
        ui.start()

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    # sounddevice's callback runs on a separate audio thread, keep it tiny
    # and just hand chunks off through a queue instead of doing VAD work
    # inline, that avoids ever blocking the audio device.
    audio_q: queue.Queue = queue.Queue()
    mic_gate = MicGate(tail_seconds=feedback_tail_ms / 1000)

    # Set from keyboard's hook thread, cleared from the main loop below,
    # same "one writer, one reader, a bool/flag either side of the
    # boundary is fine without a lock" reasoning as MicGate. This is a
    # physical-presence bypass: whoever is at the keyboard can already
    # touch the mic, the files, and the scripts/ directory the router is
    # allowed to run, so the hotkey skips straight to active listening
    # rather than routing through a speaker-verification check that
    # exists to establish presence the hotkey has already established.
    manual_wake_event = threading.Event()
    if manual_wake_hotkey:
        if keyboard is None:
            print(
                "  `keyboard` package not installed, manual wake hotkey "
                "disabled (pip install keyboard)"
            )
            manual_wake_hotkey = None
        else:
            try:
                keyboard.add_hotkey(manual_wake_hotkey, manual_wake_event.set)
                print(f"Manual wake hotkey: {manual_wake_hotkey!r}")
            except ValueError as exc:
                # e.g. a key name `keyboard` has no scan code for at all
                # (Fn is the common case, see DEFAULT_HARD_STOP_HOTKEY's
                # comment) -- a bad chord degrading the hotkey to
                # "disabled" beats taking the whole listener down over it,
                # same posture as `keyboard` being uninstalled.
                print(f"  manual wake hotkey {manual_wake_hotkey!r} not usable ({exc}), disabled")
                manual_wake_hotkey = None

    # Same one-writer(hook thread)/one-reader(main loop) flag pattern as
    # manual_wake_event above. Registered independently of it: hard stop
    # is useful whether or not manual wake is enabled, they're opposite
    # doors (start a listen / kill one) rather than a pair.
    hard_stop_event = threading.Event()
    if hard_stop_hotkey:
        if keyboard is None:
            print(
                "  `keyboard` package not installed, hard stop hotkey "
                "disabled (pip install keyboard)"
            )
            hard_stop_hotkey = None
        else:
            try:
                keyboard.add_hotkey(hard_stop_hotkey, hard_stop_event.set)
                print(f"Hard stop hotkey: {hard_stop_hotkey!r}")
            except ValueError as exc:
                print(f"  hard stop hotkey {hard_stop_hotkey!r} not usable ({exc}), disabled")
                hard_stop_hotkey = None

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[stream warning] {status}")
        if not mic_gate.is_open():
            # EKKO is talking. Dropping the chunk here, rather than
            # filtering it out later, is the whole fix: nothing downstream
            # can mistake the assistant's own voice for the user's if the
            # audio never enters the queue. See MicGate.
            return
        samples = indata[:, 0]
        if ui is not None and awaiting_command:
            # Only bother while the popup actually has bars worth
            # animating (active listening for a command); feeding it
            # during idle VAD/wake-word scoring would be wasted work for
            # a window that's hidden anyway. Reads `awaiting_command` as a
            # closure over the main loop's variable, same "one writer
            # (main thread), one reader (audio thread), fine without a
            # lock" reasoning as MicGate above.
            rms = float(np.sqrt(np.mean(np.square(samples))))
            ui.set_audio_level(min(1.0, rms * 6))  # cheap headroom scaling
        audio_q.put(samples.copy())

    speech_buffer: list[np.ndarray] = []
    in_speech = False
    wake_word_fired = False
    # True from when the wake word's segment has been verified until
    # either a segment has been accepted as the command, or the
    # --active-window timeout elapses. Segments that arrive in between and
    # fail _screen_command_segment() leave this alone, which is the point:
    # a sound that isn't a command shouldn't be able to end the turn.
    # --skip-wake never sets this: every segment is verified directly and
    # immediately instead, as if it were the wake word segment, that's the
    # whole point of the tuning mode.
    awaiting_command = False
    awaiting_since: float | None = None
    # True right after EKKO has asked "Anything else I can help with?".
    # The next accepted segment gets checked against _is_decline() before
    # routing: a negative ends the multi-turn loop, anything else is
    # treated as the next command. Never set outside that follow-up, so a
    # "no" said as someone's very first command after the wake word is
    # routed normally rather than swallowed as a decline.
    expecting_reply = False
    # time.monotonic() deadline for the response-panel follow-up session
    # an llm_fallback open-ended answer starts (see _handle_command's
    # session_until parameter). None means no session is active, i.e. the
    # normal state -- outcomes are only spoken, never also shown in the
    # popup's panel.
    session_until: float | None = None
    # time.monotonic() deadline for "still waiting on
    # BRIEFING_NARRATION_FLAG_PATH to appear" (see its own comment). None
    # means nothing is pending. Set right after a daily_briefing turn
    # instead of re-arming immediately (see _NO_FOLLOW_UP_INTENTS), and
    # cleared either when the flag shows up (re-arm for real, see the
    # check near the top of the loop below) or when this deadline passes
    # (give up quietly) or when a fresh wake word fires in the meantime
    # (a new, unrelated turn has started, this one's moot).
    pending_briefing_followup_until: float | None = None
    last_briefing_flag_check = 0.0
    # The wake word segment's own verification score, carried across to
    # the command segment that follows purely so it can be logged
    # alongside that command's transcript, this is what actually
    # authorized it. The command segment gets scored too now, but for
    # filtering rather than permission, so that score isn't what lands in
    # the log, see _screen_command_segment().
    wake_verify_score: float | None = None

    print(
        f"Listening on the default input device "
        f"(threshold={threshold}, min_silence={min_silence_ms}ms, "
        f"wake_word={wake_word!r}). Ctrl+C to stop."
    )
    if skip_wake:
        print("--skip-wake: every speech segment will be verified directly, no wake word needed.")

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=CHUNK_SAMPLES,
        # PortAudio's default (low) latency gives itself almost no internal
        # buffer, so a 32ms callback deadline (CHUNK_SAMPLES, fixed by
        # Silero's chunk size) has zero slack. The verification/wake-word
        # inference that runs synchronously in the main thread right after
        # a segment ends is CPU-heavy Python holding the GIL, which can
        # delay this callback thread past that deadline; with no buffer,
        # that shows up immediately as an "input overflow" warning and
        # dropped audio. "high" asks PortAudio for a larger internal
        # buffer so it can absorb that stall instead of dropping samples.
        # Doesn't change CHUNK_SAMPLES or the 32ms chunking VAD depends on,
        # only how much slack PortAudio has before it starts discarding.
        latency="high",
        callback=callback,
    ):
        try:
            while True:
                chunk = audio_q.get()
                if len(chunk) != CHUNK_SAMPLES:
                    continue  # partial block, e.g. right at stream start/stop

                # Deferred daily_briefing follow-up: see
                # pending_briefing_followup_until's own comment for why
                # this exists instead of re-arming immediately. Throttled
                # to BRIEFING_FOLLOWUP_POLL_S so this doesn't stat the
                # flag file on every ~32ms audio chunk while nothing is
                # pending (the common case -- this whole block is a no-op
                # whenever pending_briefing_followup_until is None).
                if pending_briefing_followup_until is not None:
                    now = time.monotonic()
                    if now > pending_briefing_followup_until:
                        print(f"[{_now()}] daily_briefing follow-up: gave up waiting for narration to finish")
                        pending_briefing_followup_until = None
                        # The "Preparing your morning briefing…" panel
                        # (see the is_pending_briefing branch above) was
                        # given the same BRIEFING_FOLLOWUP_TIMEOUT_S as its
                        # own timeout_s, so it's already closing itself
                        # right around now -- this only resets the state
                        # chip, which has no timeout of its own and would
                        # otherwise stay stuck on PROCESSING indefinitely.
                        if ui is not None:
                            ui.set_state(VoiceUIState.IDLE)
                    elif now - last_briefing_flag_check >= BRIEFING_FOLLOWUP_POLL_S:
                        last_briefing_flag_check = now
                        if BRIEFING_NARRATION_FLAG_PATH.exists():
                            # scripts/daily_briefing.py's signal_narration_done()
                            # writes JSON now ({"ts": ..., "summary": ...}),
                            # not a bare timestamp -- `summary` is the actual
                            # news+stock text just spoken, read here so the
                            # panel this turn started (see the
                            # "Preparing your morning briefing…" placeholder
                            # above) gets replaced with what was really said,
                            # instead of staying a placeholder or vanishing
                            # with nothing to show for the wait. Malformed or
                            # missing content (a write that failed
                            # mid-flush, an older flag format) falls back to
                            # a generic line rather than showing nothing --
                            # same bug-tolerant posture that module's own
                            # signal_narration_done() docstring describes.
                            try:
                                flag_payload = json.loads(BRIEFING_NARRATION_FLAG_PATH.read_text(encoding="utf-8"))
                                narration_summary = flag_payload.get("summary")
                            except Exception:  # noqa: BLE001 -- see comment above
                                narration_summary = None
                            BRIEFING_NARRATION_FLAG_PATH.unlink(missing_ok=True)
                            pending_briefing_followup_until = None
                            print(f"[{_now()}] daily_briefing narration finished, re-arming for a follow-up")
                            if ui is not None:
                                ui.show_response(
                                    narration_summary or "Your morning briefing is ready.",
                                    timeout_s=response_session_timeout,
                                )
                            _say(feedback_voice, "anything_else", mic_gate, vad, audio_q, ui=ui)
                            awaiting_command = True
                            awaiting_since = time.monotonic()
                            expecting_reply = True
                            if ui is not None:
                                ui.set_transcript("")
                                ui.set_state(VoiceUIState.LISTENING)

                if hard_stop_event.is_set():
                    hard_stop_event.clear()
                    # sd.stop() is global to the process, not scoped to a
                    # particular stream, so this cuts off _say()'s
                    # sd.play() (see feedback/speech.py's speak()) even
                    # though this loop doesn't hold a reference to that
                    # stream. speak()'s blocking sd.wait() then returns
                    # like playback finished normally, mid-sentence
                    # silence rather than a hang or an exception.
                    sd.stop()
                    mic_gate.force_open()
                    vad.reset_states()
                    _drain(audio_q)
                    in_speech = False
                    speech_buffer = []
                    wake_word_fired = False
                    awaiting_command = False
                    awaiting_since = None
                    expecting_reply = False
                    session_until = None
                    pending_briefing_followup_until = None
                    wake_verify_score = None
                    clear_short_memory()  # see memory/short_term.py -- the turn this cancelled is done
                    print(f"[{_now()}] hard stop — active listen cancelled")
                    if ui is not None:
                        ui.set_state(VoiceUIState.IDLE)
                    continue

                if manual_wake_event.is_set():
                    manual_wake_event.clear()
                    if awaiting_command:
                        # Already listening -- treat a second press as
                        # "still here" and push the --active-window
                        # deadline back out, rather than stacking a
                        # second acknowledgment on top of whatever's
                        # already in flight.
                        awaiting_since = time.monotonic()
                        print(f"[{_now()}] manual wake — already listening, window extended")
                    else:
                        # No wake word, no verification: the hotkey itself
                        # is the presence check (see manual_wake_event's
                        # setup above), so this jumps straight to the same
                        # active-listening state a verified wake word segment
                        # produces.
                        print(f"[{_now()}] manual wake triggered — active listening, say your command")
                        if ui is not None:
                            ui.set_transcript("")
                        _say(feedback_voice, "generic_ack", mic_gate, vad, audio_q, ui=ui)
                        awaiting_command = True
                        awaiting_since = time.monotonic()
                        expecting_reply = False
                        wake_verify_score = None
                        if ui is not None:
                            ui.set_state(VoiceUIState.LISTENING)

                if in_speech:
                    speech_buffer.append(chunk)

                # Fed continuously, not gated on in_speech, that's the
                # usage openWakeWord is designed for and it keeps the
                # wake word's onset from getting clipped while VAD is
                # still deciding a segment has started. No point scoring
                # it while we're already waiting for/capturing a command
                # though, that segment isn't a wake word candidate, and
                # --skip-wake never needs it at all, wake_word_fired
                # doesn't gate anything in that mode (see below).
                if not awaiting_command and not skip_wake:
                    predictions = oww_model.predict(_to_int16(chunk))
                    score = predictions.get(prediction_key, 0.0)
                    if in_speech and not wake_word_fired and score >= wake_threshold:
                        wake_word_fired = True
                        # A fresh, unrelated turn is starting -- any
                        # still-pending daily_briefing follow-up (see
                        # pending_briefing_followup_until's comment) is
                        # moot now, drop it rather than have it fire
                        # "anything else?" in the middle of this new turn
                        # if the flag happens to appear later.
                        pending_briefing_followup_until = None
                        print(f"[{_now()}] wake word {wake_word!r} detected (score={score:.2f})")

                # Gave up on a wake word with no command following it,
                # drop back to idle and start scoring for the wake word
                # again. --skip-wake never reaches awaiting_command at
                # all, so this never fires for it.
                if (
                    awaiting_command
                    and not in_speech
                    and awaiting_since is not None
                    and time.monotonic() - awaiting_since > active_window
                ):
                    awaiting_command = False
                    awaiting_since = None
                    expecting_reply = False
                    print(f"[{_now()}] no command heard within {active_window:.0f}s, back to idle")
                    _say(feedback_voice, "listening_timeout", mic_gate, vad, audio_q, ui=ui)
                    if ui is not None:
                        ui.set_state(VoiceUIState.IDLE)

                event = vad(chunk, return_seconds=True)
                if event is None:
                    continue

                if "start" in event:
                    in_speech = True
                    wake_word_fired = False
                    speech_buffer = [chunk]
                    print(f"[{_now()}] speech started")

                elif "end" in event:
                    in_speech = False
                    duration = len(speech_buffer) * CHUNK_SAMPLES / SAMPLE_RATE
                    print(f"[{_now()}] speech ended ({duration:.2f}s)")

                    is_command = awaiting_command
                    # Either the real wake word segment, or (--skip-wake)
                    # a stand-in for it: identity gets checked here, not
                    # on the command that follows.
                    is_wake_check = skip_wake or (wake_word_fired and not is_command)

                    if is_wake_check:
                        # Only touch disk if we're keeping captures, or
                        # verify.py needs a real WAV to check, no reason
                        # to write one out otherwise.
                        path, tmp_dir = None, None
                        if speech_buffer:
                            target_dir = save_dir or (tmp_dir := tempfile.mkdtemp(prefix="ekko_vad_"))
                            path = _save_segment(speech_buffer, target_dir)
                            if save_dir:
                                print(f"  saved to {path}")

                        if path is None:
                            print("  nothing was captured, skipping verification")
                        elif verify_enabled:
                            is_match, similarity = verify(
                                reference, speaker_model, path, verify_threshold
                            )
                            if is_match:
                                wake_verify_score = similarity
                                print(f"[{_now()}] verified — active listening, say your command")
                                # Acknowledge first, arm second. The
                                # --active-window countdown should be
                                # time the user can actually talk into,
                                # not time spent listening to EKKO finish
                                # a sentence.
                                if ui is not None:
                                    ui.set_transcript("")
                                _say(feedback_voice, "generic_ack", mic_gate, vad, audio_q, ui=ui)
                                awaiting_command = True
                                awaiting_since = time.monotonic()
                                if ui is not None:
                                    ui.set_state(VoiceUIState.LISTENING)
                            else:
                                print("  voice did not match enrolled speaker — ignoring")
                                # ERROR rather than the default SPEAKING
                                # while "access denied" plays, then back to
                                # idle -- this is the one wake-check outcome
                                # that doesn't arm active listening after.
                                _say(
                                    feedback_voice, "access_denied", mic_gate, vad, audio_q,
                                    ui=ui, ui_state=VoiceUIState.ERROR,
                                )
                                if ui is not None:
                                    ui.set_state(VoiceUIState.IDLE)
                        else:
                            # --no-verify: trust the wake word alone, same
                            # as before this identity check moved here.
                            print(f"[{_now()}] wake word detected (verification disabled with --no-verify) — active listening")
                            if ui is not None:
                                ui.set_transcript("")
                            _say(feedback_voice, "generic_ack", mic_gate, vad, audio_q, ui=ui)
                            awaiting_command = True
                            awaiting_since = time.monotonic()
                            if ui is not None:
                                ui.set_state(VoiceUIState.LISTENING)

                        if tmp_dir:
                            shutil.rmtree(tmp_dir, ignore_errors=True)

                        if save_dir:
                            # Every wake word event (match, mismatch, or
                            # --no-verify) writes at least one segment to
                            # save_dir, and long-running sessions add these
                            # up without bound otherwise. Prune right here
                            # rather than only at startup, so a listener
                            # left running for days doesn't fill the disk.
                            prune_captures(save_dir, keep_captures)

                        if skip_wake:
                            # Tuning mode never enters the command phase,
                            # every segment goes through this same check
                            # again, back-to-back.
                            awaiting_command = False
                            awaiting_since = None

                    elif is_command:
                        path, tmp_dir = None, None
                        if speech_buffer:
                            target_dir = save_dir or (tmp_dir := tempfile.mkdtemp(prefix="ekko_vad_"))
                            path = _save_segment(speech_buffer, target_dir)
                            if save_dir:
                                print(f"  saved to {path}")

                        reason, transcription = _screen_command_segment(
                            path,
                            duration,
                            min_command_ms / 1000,
                            verify_enabled,
                            reference,
                            speaker_model,
                            command_verify_threshold,
                            transcribe_enabled,
                            whisper_model,
                            max_no_speech_prob,
                            min_avg_logprob,
                        )

                        if reason is not None:
                            # The turn survives. Everything below this
                            # branch that clears awaiting_command is
                            # deliberately skipped: a segment we've just
                            # decided wasn't a command shouldn't be able
                            # to end active listening, which is exactly
                            # what the acknowledgment's own echo tail used
                            # to do. The --active-window deadline is left
                            # alone too, so a room that keeps making
                            # noises can't hold the window open forever.
                            elapsed = time.monotonic() - (awaiting_since or 0.0)
                            print(
                                f"  ignoring segment: {reason} — still listening "
                                f"({max(0.0, active_window - elapsed):.1f}s left)"
                            )
                        else:
                            if transcription is None:
                                print("  command captured (transcription disabled with --no-transcribe)")
                                awaiting_command = False
                                awaiting_since = None
                                expecting_reply = False
                                wake_verify_score = None
                                if ui is not None:
                                    ui.set_state(VoiceUIState.IDLE)
                            else:
                                print(f"  transcript: {transcription.text!r}")
                                _log_transcript(
                                    transcript_log,
                                    path if save_dir else None,
                                    transcription.text,
                                    wake_verify_score,
                                )
                                if ui is not None:
                                    ui.set_transcript(transcription.text)
                                    ui.set_state(VoiceUIState.PROCESSING)

                                if expecting_reply and _is_decline(transcription.text):
                                    # A negative answer to "anything else?"
                                    # ends the loop, never routed: "no" isn't
                                    # a command.
                                    print(f"[{_now()}] declined — back to idle")
                                    _say(feedback_voice, "ok_bye", mic_gate, vad, audio_q, ui=ui)
                                    awaiting_command = False
                                    awaiting_since = None
                                    expecting_reply = False
                                    wake_verify_score = None
                                    # The session just ended on purpose --
                                    # any short-memory turn left pending
                                    # (an unanswered follow_up nobody
                                    # replied to) is done, not just stale.
                                    # See memory/short_term.py.
                                    clear_short_memory()
                                    if ui is not None:
                                        ui.set_state(VoiceUIState.IDLE)
                                else:
                                    outcome_key, outcome_bundle, session_until, grounded_follow_up, matched_domain = _handle_command(
                                        transcription.text,
                                        router,
                                        execute_enabled,
                                        feedback_voice,
                                        mic_gate,
                                        vad,
                                        audio_q,
                                        llm_fallback_enabled,
                                        ui=ui,
                                        session_until=session_until,
                                        session_timeout=response_session_timeout,
                                        research_tabs_enabled=research_tabs_enabled,
                                        domain_router=domain_router,
                                        pending_facts=pending_facts,
                                    )
                                    # A grounded follow-up (from the same
                                    # llm_fallback answer, see
                                    # _handle_command's docstring) always
                                    # wins over the generic RESPONSES
                                    # lookup when one came back; every
                                    # MATCHED command-router outcome has
                                    # none, so this changes nothing for
                                    # that path.
                                    follow_up_key = None
                                    follow_up_text = grounded_follow_up
                                    if grounded_follow_up is None and outcome_key is not None:
                                        follow_up_key = _follow_up_key(outcome_bundle, outcome_key)
                                    if follow_up_key is not None or follow_up_text is not None:
                                        # EKKO said something about the
                                        # outcome, matched or not, so keep
                                        # the turn going: ask, then re-arm
                                        # for a follow-up instead of
                                        # dropping back to idle. Usually
                                        # the generic "anything_else",
                                        # except right after opening
                                        # Brave (see _follow_up_key), or a
                                        # grounded next step from the
                                        # llm_fallback answer itself
                                        # (follow_up_text, preferred when
                                        # present -- see _handle_command's
                                        # docstring). "anything_else" is
                                        # just the RESPONSES fallback key
                                        # here; text_override always wins
                                        # when given. domain=matched_domain
                                        # only matters on this fallback
                                        # path (follow_up_text is None):
                                        # render() then prefers
                                        # "anything_else:<domain>" over the
                                        # flat list whenever this turn's
                                        # answer came from a domain expert
                                        # and Gemini didn't itself propose a
                                        # grounded follow_up -- see
                                        # feedback/speech.py's
                                        # "anything_else:<domain>" comment.
                                        _say(
                                            feedback_voice,
                                            follow_up_key or "anything_else",
                                            mic_gate,
                                            vad,
                                            audio_q,
                                            domain=matched_domain,
                                            text_override=follow_up_text,
                                            ui=ui,
                                        )
                                        awaiting_command = True
                                        awaiting_since = time.monotonic()
                                        expecting_reply = True
                                        if ui is not None:
                                            ui.set_transcript("")
                                            ui.set_state(VoiceUIState.LISTENING)
                                    else:
                                        # Nothing was said (empty transcript
                                        # or --no-execute), or _follow_up_key
                                        # deliberately suppressed the prompt
                                        # (daily_briefing -- see its own
                                        # comment: that intent's window keeps
                                        # talking on its own for several more
                                        # seconds after this SPEAK: line, and
                                        # re-arming active listening for
                                        # "anything else?" right on top of
                                        # that is what caused two things to
                                        # sound like they were both trying to
                                        # respond at once). Either way,
                                        # nothing to react to here right now,
                                        # back to idle.
                                        awaiting_command = False
                                        awaiting_since = None
                                        expecting_reply = False
                                        wake_verify_score = None
                                        is_pending_briefing = (
                                            outcome_key is not None
                                            and outcome_bundle.intent in _NO_FOLLOW_UP_INTENTS
                                        )
                                        if is_pending_briefing:
                                            # Specifically the daily_briefing
                                            # case, not "nothing was said":
                                            # its window is still going to
                                            # fetch, summarize, and narrate
                                            # news and stocks for up to
                                            # BRIEFING_FOLLOWUP_TIMEOUT_S more
                                            # seconds, on a different process
                                            # this one can't watch directly.
                                            # ui.set_state(IDLE) here used to
                                            # fire unconditionally, which
                                            # (combined with voice_ui.py's
                                            # AUTO_HIDE_AFTER_S) made the whole
                                            # popup vanish within ~1.5s of the
                                            # quick initial confirmation --
                                            # well before the real narration
                                            # had even started fetching,
                                            # making the assistant look like
                                            # it had finished and gone idle
                                            # while it was actually still
                                            # about to speak twice more.
                                            # PROCESSING + a placeholder panel
                                            # keeps the UI visibly present for
                                            # the whole wait instead; the flag
                                            # -detection block below swaps in
                                            # the real narrated text once it
                                            # appears. Wait for
                                            # BRIEFING_NARRATION_FLAG_PATH
                                            # instead of either re-arming now
                                            # (races the narration) or never
                                            # re-arming at all. Clear any
                                            # stale flag left over from a
                                            # previous, already-handled
                                            # briefing first, so this doesn't
                                            # fire immediately on a leftover.
                                            if ui is not None:
                                                ui.set_state(VoiceUIState.PROCESSING)
                                                ui.show_response(
                                                    "Preparing your morning briefing…",
                                                    timeout_s=BRIEFING_FOLLOWUP_TIMEOUT_S,
                                                )
                                            BRIEFING_NARRATION_FLAG_PATH.unlink(missing_ok=True)
                                            pending_briefing_followup_until = time.monotonic() + BRIEFING_FOLLOWUP_TIMEOUT_S
                                            last_briefing_flag_check = 0.0
                                        elif ui is not None:
                                            ui.set_state(VoiceUIState.IDLE)

                        if tmp_dir:
                            shutil.rmtree(tmp_dir, ignore_errors=True)

                    else:
                        # Ordinary segment, wake word never fired and
                        # we're not waiting on a command. Log it if
                        # we're keeping captures and move on.
                        if save_dir and speech_buffer:
                            print(f"  saved to {_save_segment(speech_buffer, save_dir)}")

                    speech_buffer = []
                    wake_word_fired = False

        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            if ui is not None:
                ui.stop()


def _say(
    voice,
    response_key_: str | None,
    gate: MicGate,
    vad,
    audio_q: queue.Queue,
    intent: str | None = None,
    domain: str | None = None,
    slots: dict | None = None,
    text_override: str | None = None,
    ui: "VoiceUI | None" = None,
    ui_state: "VoiceUIState" = VoiceUIState.SPEAKING,
    show_in_panel: bool = False,
    panel_timeout: float = SESSION_DEFAULT_TIMEOUT_S,
) -> None:
    """Speak one of feedback.speech's fixed responses with the mic gated,
    then clean up after the gap it leaves.

    `ui`/`ui_state`: if a VoiceUI is running, its state is set to
    `ui_state` (SPEAKING, unless a caller overrides it, e.g. ERROR for
    "access denied") for as long as this call is on the line. What the
    popup shows *after* playback finishes is the caller's call, not
    this function's -- it depends on what happens next (back to
    listening? idle?), which _say() has no way to know.

    `show_in_panel`: also puts the exact text spoken into the popup's
    response panel (see ui/voice_ui.py's show_response()), for
    `panel_timeout` seconds. Only `_handle_command()` sets this, and only
    for an llm_fallback open-ended answer or an outcome spoken during an
    active follow-up session it started -- see that function's
    `session_until` handling for what "active" means. Every other call
    site (acknowledgments, timeouts, "anything else?") leaves this False,
    since those aren't responses to display, just spoken prompts.

    Every place EKKO opens its mouth goes through here, not just the wake
    word acknowledgment. Saying "Done." into an open mic is the same bug
    as saying "Hi Mubaraq" into one, and the outcome responses land while
    the pipeline is at its most eager to hear something.

    Two things need clearing once playback is done. Whatever was already
    queued before the gate closed is stale by now, so it's dropped rather
    than replayed into VAD several seconds late. And VADIterator carries
    internal state across calls, so it's reset: the audio either side of
    the gap isn't continuous, and a half-triggered segment from before the
    acknowledgment shouldn't finish itself on the user's reply.

    Both of those happen while the gate is still muted for its decay
    window, which is the point: the callback keeps dropping chunks past
    this line, so there's nothing left to arrive between the drain and
    the reopen.

    `intent`, `domain` and `slots` are optional, only meaningful for
    intent-aware keys like "command_confirmed" or domain-aware keys like
    "anything_else:finance" (see feedback.speech.render()); every other
    call site below omits them.

    `text_override`, when given, is spoken (and shown, if `show_in_panel`)
    directly instead of looking `response_key_` up in RESPONSES -- see
    routing/execute.py's spoken_override(), the only current source of
    one, and llm_fallback's answer, the other. `response_key_` is still
    required in that case (it's what _handle_command uses to decide
    whether EKKO should say anything at all), it just doesn't get
    rendered into text itself.

    Resolves the text itself (via feedback.speech.render(), the same
    lookup feedback.speech.say() used to do internally) rather than
    calling say() as a black box, specifically so the resolved text is
    available here to hand to the popup too -- say() only ever returned a
    playback timestamp, and render() picking a fresh random RESPONSES
    variant a second time could show different words than the ones
    actually spoken.
    """
    if voice is None or response_key_ is None:
        return
    text = (
        text_override
        if text_override is not None
        else render_response(response_key_, intent=intent, domain=domain, slots=slots)
    )
    if text is None:
        # A caller asking for a key that doesn't exist is a wiring bug,
        # but not one worth taking the listening pipeline down over --
        # same posture feedback.speech.say() used to have for this.
        print(f"[speech] unknown response key {response_key_!r}")
    if ui is not None:
        ui.set_state(ui_state)
        if show_in_panel and text:
            # Show it now so the panel is up while EKKO is still talking,
            # but with a long enough timeout that it can't expire mid-
            # playback -- the real countdown (panel_timeout, meant to be
            # "how long to leave this up to read") starts below, once
            # speak() has actually finished and the text is fully spoken.
            ui.show_response(text, timeout_s=panel_timeout + 60.0)
    playback_done_at = 0.0
    gate.close()
    try:
        if text:
            playback_done_at = speak(voice, text)
            if ui is not None and show_in_panel:
                # Restart the clock from here: show_response() resets its
                # deadline on repeat calls (see its docstring), which is
                # exactly what a multi-turn session already relies on --
                # reusing that same behavior means the reader actually
                # gets panel_timeout seconds after hearing the response,
                # not panel_timeout seconds minus however long it took to
                # say it.
                ui.show_response(text, timeout_s=panel_timeout)
    finally:
        # 0.0 (nothing played, missing key or failed synthesis) falls back
        # to now, keeping the gate honest either way: a tail measured from
        # here is harmless if there was no audio, and never reopening
        # would be a deadlock.
        gate.reopen_after(playback_done_at or time.monotonic())
    _drain(audio_q)
    vad.reset_states()


# A closed, hand-written set rather than a routing/intents.yaml intent:
# that file is the security boundary for things EKKO can *do* (every
# intent needs a real .ps1 handler, checked to exist at load), and "no"
# doesn't do anything. Same "deterministic first" rule, applied to a
# closed set of two words' worth of variants instead.
_DECLINE_PHRASES = {
    "no", "nope", "nah", "no thanks", "no thank you", "nothing", "nothing else",
    "nothing more", "nothing for now", "that's all", "that'll be all",
    "that will be all", "that's it", "that'll do it", "that will do it",
    "i'm good", "i'm done", "i'm all set", "i'm okay", "i'm ok", "all good",
    "we're good", "thanks", "thank you", "thanks a lot", "thank you so much",
}


def _is_decline(text: str) -> bool:
    """True if a follow-up reply ("anything else?") was a plain negative."""
    normalized = text.strip().lower().strip(".!,")
    return normalized in _DECLINE_PHRASES


def _screen_command_segment(
    path: str | None,
    duration: float,
    min_command_seconds: float,
    verify_enabled: bool,
    reference,
    speaker_model,
    command_verify_threshold: float,
    transcribe_enabled: bool,
    whisper_model,
    max_no_speech_prob: float,
    min_avg_logprob: float,
) -> tuple[str | None, Transcription | None]:
    """Decide whether a segment captured during active listening is really
    a command. Returns (reason, transcription): a reason string means
    reject it and keep listening, None means run it.

    Active listening used to take the first speech segment it saw, full
    stop, which made every stray sound in the room cost the user their
    turn. In practice the stray sound was EKKO itself: the tail of its own
    acknowledgment, arriving after MicGate reopened, transcribed as "Bye."
    and routed to nothing while the user was still drawing breath. MicGate
    closes that specific gap now, but "the first thing you hear is the
    command" is the wrong rule regardless of what leaks through it, so
    this exists to be able to say no.

    Three checks, cheapest first, because each costs more than the last:

    1. Duration. Free, we already measured it. Kills clicks only.
    2. Identity. ~100ms of ECAPA. This is the check that generalizes:
       whatever EKKO says, in whatever response we add later, at whatever
       length, it will never score as the enrolled speaker. No constant to
       re-tune per phrase, which is what a "wait a bit longer after
       speaking" fix would have needed. It also closes a real hole, until
       now anyone in the room could issue the command once the user's wake
       word had verified. The threshold is far below the wake word's
       (DEFAULT_COMMAND_VERIFY_THRESHOLD) on purpose: this isn't
       authorizing anything, identity was settled at the wake word, it
       only has to separate the user from a TTS voice or a stranger, and a
       tight cutoff here would reject real commands for no benefit.
    3. Content. Whisper's own no_speech_prob/avg_logprob, free since we
       have to transcribe anyway. Catches junk from sources identity can't
       reason about, a door, a notification chime, a cough. A backstop
       rather than a second opinion: its scores for echo tails and real
       speech genuinely overlap, see DEFAULT_MAX_NO_SPEECH_PROB.
    """
    if path is None:
        return "nothing was captured", None

    if duration < min_command_seconds:
        return f"only {duration:.2f}s long (floor {min_command_seconds:.2f}s)", None

    if verify_enabled:
        is_match, similarity = verify(
            reference, speaker_model, path, command_verify_threshold, label="command"
        )
        if not is_match:
            return (
                f"not the enrolled speaker (similarity {similarity:.3f} < "
                f"{command_verify_threshold})",
                None,
            )

    if not transcribe_enabled:
        return None, None

    transcription = _transcribe(whisper_model, path)
    if not transcription.text:
        return "no speech in it", transcription
    if transcription.no_speech_prob > max_no_speech_prob:
        return (
            f"probably not speech (no_speech_prob "
            f"{transcription.no_speech_prob:.2f} > {max_no_speech_prob}, "
            f"heard {transcription.text!r})",
            transcription,
        )
    if transcription.avg_logprob < min_avg_logprob:
        return (
            f"low-confidence transcript (avg_logprob "
            f"{transcription.avg_logprob:.2f} < {min_avg_logprob}, "
            f"heard {transcription.text!r})",
            transcription,
        )
    return None, transcription


def _handle_command(
    transcript: str,
    router,
    execute_enabled: bool,
    voice,
    gate: MicGate,
    vad,
    audio_q: queue.Queue,
    llm_fallback_enabled: bool = True,
    ui: "VoiceUI | None" = None,
    session_until: float | None = None,
    session_timeout: float = SESSION_DEFAULT_TIMEOUT_S,
    research_tabs_enabled: bool = True,
    domain_router: "DomainRouter | None" = None,
    pending_facts: "PendingFactCache | None" = None,
):
    """Route a transcript, run what it matched, and say what happened.

    The one thing worth not losing here: execute() is handed a bundle,
    never the transcript. Whisper's output stops being text the moment
    routing is done with it, and what crosses into subprocess territory
    is an intent key and slot values that both came out of
    routing/intents.yaml. See routing/execute.py.

    `session_until`/`session_timeout`: an llm_fallback open-ended answer
    (the "ambiguous request" case) is always shown in the popup's
    response panel, not just spoken, and that starts a follow-up session:
    for as long as `session_until` (a time.monotonic() deadline) is still
    ahead of now, every outcome this function speaks -- matched command
    or not -- is *also* shown in that same panel rather than opening a
    fresh one, and speaking it slides the deadline forward another
    `session_timeout` seconds. Once the deadline passes, later turns
    revert to speaking only, exactly as if this session had never
    started; nothing here re-arms it on its own except a fresh ambiguous
    answer. This is deliberately just a display choice: `bundle` and
    `execute()` behave identically whether or not a session is active,
    the router isn't relaxed and no extra command can run just because a
    session happens to be open.

    Returns (outcome_key, bundle, session_until, grounded_follow_up):
    outcome_key is the feedback.speech RESPONSES key that was spoken (or
    None if EKKO stayed quiet), so the caller can decide whether to keep
    the turn going, see `expecting_reply` in listen(). bundle is returned
    alongside it so the caller can pick which follow-up prompt fits the
    outcome, see _follow_up_key. session_until is the (possibly updated)
    deadline the caller should pass back in on the next call.
    grounded_follow_up is a specific next step from that same llm_fallback
    answer (see llm_fallback/gemini/SYSTEM_PROMPT.md's `follow_up` field),
    or None on every other outcome -- a MATCHED command-router bundle
    never calls Gemini and has nothing to ground a follow-up in, so it
    always falls through to _follow_up_key()'s generic "anything_else"
    exactly as before this existed. The caller prefers this over
    _follow_up_key() only when it's non-None.

    Returns a 5th value too, matched_domain: the domains/registry.yaml key
    (if any) that supplied this turn's system prompt, or None on every
    path that never ran domain detection. Threaded back out so the caller
    can pass it as _say()'s `domain` when grounded_follow_up came back
    null -- see feedback/speech.py's "anything_else:<domain>" entries --
    instead of silently dropping into the flat "anything_else" the moment
    Gemini didn't happen to populate follow_up for one turn, which would
    undercut the domain's own persona right after it spoke in it.
    """
    session_active = session_until is not None and time.monotonic() < session_until

    # A short-memory turn only ever means something while the session that
    # produced it is still active -- once session_active goes False (the
    # follow-up window timed out, or this is simply a fresh wake with no
    # session at all), any leftover short_term.json is from an earlier,
    # unrelated conversation. Clear it here rather than leaving it for the
    # next active session to stumble into, same "never trust a stale load"
    # posture as routing/route.py re-reading intents.yaml fresh each call.
    if not session_active:
        clear_short_memory()

    bundle = router.route(transcript)  # also appends to routing/logs/routing.jsonl
    print(f"  {bundle.describe()}")

    matched_domain: str | None = None
    if bundle.status is RoutingStatus.NO_MATCH and llm_fallback_enabled:
        # Domain detection runs here, strictly between the command
        # router's NO_MATCH and the Gemini call -- never before it (a
        # command match always wins, unchanged) and never in competition
        # with the fallback call itself (a domain match doesn't skip
        # Gemini, it only picks which system-prompt bundle that same one
        # call gets). This is the resolution to "does domain detection or
        # the ambiguous-fallback check run first": a clear or
        # continuity-boosted domain match is pulled into an expert prompt
        # even at a moderate anchor score; only a transcript that clears
        # neither the command router nor any domain's threshold falls
        # through to the flat SYSTEM_PROMPT.md exactly as before domain
        # experts existed. See routing/domains.py and
        # domains/registry.yaml's threshold comments.
        system_instruction_override = None
        if domain_router is not None:
            match = domain_router.match(transcript)
            if match is not None:
                matched_domain, _score = match
                base_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
                system_instruction_override = compose_system_instruction(matched_domain, base_prompt)
                print(f"  [domains] matched {matched_domain!r}")
            domain_router.record(transcript, matched_domain)

        memory_context = None
        if pending_facts is not None:
            # memory_enabled implies pending_facts is not None -- see
            # listen()'s startup. Read fresh each call, same
            # never-trust-a-stale-load posture routing/route.py's
            # attempt_fallback() already applies to intents.yaml.
            memory_context = read_memory().as_prompt_block() or None

        # session_active, not just "does short_term.json exist" -- the
        # not-session_active branch above already clears a stale file, but
        # this also guards a same-turn race: session_active reflects the
        # deadline this specific call was invoked under, which is the only
        # thing that should decide whether a leftover turn is still live.
        pending_short_memory = read_short_memory() if session_active else None
        if pending_short_memory is not None and pending_short_memory.follow_up_answer is None:
            # This transcript is presumed to be the reply to
            # pending_short_memory.follow_up -- record that now so
            # short_term.json reflects it even though the fields actually
            # sent to Gemini below (prior_question/prior_answer/follow_up,
            # see build_prompt()'s short_memory docstring) come from the
            # object read a moment ago, not this write's result. If this
            # turn's own answer opens a new follow_up, the write further
            # down overwrites this anyway -- see write_short_memory()'s
            # "a new turn always overwrites" rule.
            write_short_memory(follow_up_answer=transcript)

        # A constrained second pass, one Gemini call that both re-checks
        # for a command and, if that comes back empty, answers an
        # open-ended question in the same round trip -- see
        # llm_fallback/README.md for why this is one call, not two.
        fallback_outcome = attempt_fallback(
            transcript,
            system_instruction_override=system_instruction_override,
            memory_context=memory_context,
            domain=matched_domain,
            short_memory=pending_short_memory,
        )
        if fallback_outcome.error:
            print(f"  [llm_fallback] {fallback_outcome.error}")
        elif fallback_outcome.bundle is not None:
            # A validated command pick: `bundle` becomes an ordinary
            # MATCHED bundle and falls into the execute() path below, same
            # as a direct embedding match.
            print(f"  [llm_fallback] {fallback_outcome.bundle.describe()}")
            bundle = fallback_outcome.bundle
        elif fallback_outcome.answer:
            # Not a command at all -- an open-ended answer instead. This
            # never produces an IntentBundle and never touches execute(),
            # so it's spoken and returned here rather than falling into
            # the MATCHED/execute() path below, which has nothing to do
            # for a bundle that's still NO_MATCH anyway. Always shown in
            # the panel (show_in_panel=True unconditionally) and always
            # starts/extends the follow-up session -- this is the one
            # case that can open the panel fresh, not just keep an
            # already-open one going.
            print(f"  [llm_fallback] answered: {fallback_outcome.answer!r}")
            _say(
                voice,
                "ambiguous_answer",
                gate,
                vad,
                audio_q,
                text_override=fallback_outcome.answer,
                ui=ui,
                show_in_panel=True,
                panel_timeout=session_timeout,
            )
            if fallback_outcome.follow_up:
                # Starts (or overwrites) the short-memory turn this
                # follow_up is now waiting on a reply to -- read back on
                # the *next* call above as pending_short_memory. Always a
                # fresh write, never a merge: this is a brand-new answer,
                # so any earlier unanswered follow_up it supersedes is
                # moot (see write_short_memory()'s "new turn" shape).
                write_short_memory(
                    prior_question=transcript,
                    prior_answer=fallback_outcome.answer,
                    follow_up=fallback_outcome.follow_up,
                )
            else:
                # No follow_up on this turn -- nothing left for a next
                # reply to attach to, and this transcript itself just
                # closed out whatever was pending (the write above already
                # recorded it as answered). Clear rather than leave an
                # answered, dead-end turn sitting on disk.
                clear_short_memory()
            if research_tabs_enabled and fallback_outcome.urls:
                # Gated on fallback_outcome.urls being non-empty, not just
                # research_tabs_enabled: urls is the fallback model's own
                # signal that it actually searched and found something
                # worth linking (see llm_fallback/claude_code/CLAUDE.md and
                # llm_fallback/gemini/SYSTEM_PROMPT.md's `urls` rule --
                # "[] whenever you didn't search for this answer, or found
                # nothing worth linking"). Opening a generic Brave tab for
                # the raw transcript on *every* spoken answer -- including
                # plain conversational ones the model answered from general
                # knowledge, e.g. while ENABLE_SEARCH is False and urls is
                # always [] -- searched literally nothing useful and did it
                # for questions that never needed a browser at all. Only
                # open tabs when there's at least one curated result to
                # accompany the generic search with.
                #
                # Best-effort, never blocks or fails the turn -- see
                # open_research_tabs()'s docstring. A Brave Search tab for
                # the transcript, plus up to 3 curated pages the fallback
                # model already found via WebSearch while answering (see
                # llm_fallback/claude_code/CLAUDE.md's `urls` rule), give
                # the person something to actually read past the one or
                # two spoken sentences.
                #
                # open_research_tabs() returns as soon as the process is
                # launched, not once it (or Brave) has finished -- so the
                # "pulling up some helpful sites" line below starts right
                # alongside the tabs opening instead of trailing behind
                # them by however long that took. Speaking it only after a
                # full round trip made the turn sound like it was waiting
                # on the browser before it could say anything; nothing
                # about the line depends on the tabs having finished.
                # Still gated on tabs_error, since that's only set for a
                # failure this function can know about *before* speaking
                # (the script missing, or the process failing to even
                # start) -- never claim a launch that's already known to
                # have failed. A failure discovered after that point is
                # logged later by open_research_tabs()'s background
                # reaper, same "stay vague rather than optimistic" posture
                # "error" has elsewhere in this function, just async now.
                tabs_error = open_research_tabs(transcript, fallback_outcome.urls)
                if tabs_error:
                    print(f"  [llm_fallback] research tabs: {tabs_error}")
                else:
                    _say(voice, "research_tabs_opened", gate, vad, audio_q, ui=ui)

            if pending_facts is not None and fallback_outcome.memory_candidate is not None:
                # Runs after _say() already spoke the answer above, never
                # blocking it -- a handful of CPU-resident MiniLM embeds
                # plus one small file write, cheap enough to stay inline
                # rather than threaded preemptively (see memory/README.md
                # and memory/scoring.py's module docstring). Never trusts
                # the candidate itself: propose_and_score() is the actual
                # gatekeeper, this just wires its decision to the file.
                existing_memory = read_memory()
                decision = propose_and_score(
                    fallback_outcome.memory_candidate,
                    existing_memory,
                    transcript,
                    pending=pending_facts,
                    model=domain_router.model if domain_router is not None else None,
                )
                log_memory_decision(fallback_outcome.memory_candidate, decision)
                if decision.accept:
                    write_memory(decision.section, decision.text, decision.merge_into)
                    print(f"  [memory] wrote to {decision.section!r} ({decision.reason})")
                else:
                    print(f"  [memory] not written ({decision.reason})")

            return "ambiguous_answer", bundle, time.monotonic() + session_timeout, fallback_outcome.follow_up, matched_domain
        else:
            print(f"  [llm_fallback] no command match, no answer (reason: {fallback_outcome.reason!r})")

    result = None
    if bundle.status is RoutingStatus.MATCHED:
        if execute_enabled:
            result = execute(bundle)
            print(f"  {result.describe()}")
        else:
            # response_key() with no result returns None, so EKKO stays
            # quiet rather than claiming "Done." for something it was
            # told not to do.
            print(f"  --no-execute, not running {bundle.handler}")

    outcome_key = response_key(bundle, result)
    # bundle.intent/bundle.slots let _say() pick an intent-specific
    # confirmation, e.g. "Opened chrome." instead of the generic "Done.",
    # when bundle.status is MATCHED. They're harmless to pass for the
    # other statuses too: NO_MATCH/EMPTY_TRANSCRIPT bundles have
    # intent=None, and no RESPONSES key there has a "<key>:<intent>"
    # entry to match against anyway.
    #
    # override_text is None for every handler except one that opted in by
    # printing a final "SPEAK: ..." stdout line (see routing/execute.py's
    # spoken_override(), currently only scripts/system_diagnosis.ps1) --
    # _say() falls back to the fixed RESPONSES text whenever it's None.
    override_text = spoken_override(result) if result is not None else None
    _say(
        voice,
        outcome_key,
        gate,
        vad,
        audio_q,
        intent=bundle.intent,
        slots=dict(bundle.slots),
        text_override=override_text,
        ui=ui,
        ui_state=VoiceUIState.ERROR if bundle.status is not RoutingStatus.MATCHED else VoiceUIState.SPEAKING,
        show_in_panel=session_active,
        panel_timeout=session_timeout,
    )
    if session_active:
        session_until = time.monotonic() + session_timeout
    # No grounded follow-up on this path -- a MATCHED command-router
    # bundle (or a NO_MATCH with llm_fallback_enabled=False) never called
    # Gemini, so there's no answer content for one to be grounded in;
    # the caller falls through to _follow_up_key()'s generic
    # "anything_else" exactly as before this existed. matched_domain is
    # also always None here: a MATCHED bundle short-circuits before domain
    # detection ever runs, and the "no command, no answer" NO_MATCH path
    # above falls through to here too, but by then there's no domain-
    # flavored content to protect either -- EKKO said "didn't catch that,"
    # not a domain answer, so the generic follow-up is the honest one.
    return outcome_key, bundle, session_until, None, matched_domain


# Which open_app targets, once opened, get the more specific
# brave_search_prompt follow-up instead of the generic "anything_else".
# A set rather than a single constant so a second app can opt in later
# without restructuring _follow_up_key, though today it's just brave.
_SEARCH_FOLLOW_UP_APPS = {"brave"}

# Intents whose confirmation isn't the end of what EKKO has to say:
# daily_briefing's SPEAK: line explicitly hands off to its own detached
# window (scripts/daily_briefing.py), which keeps fetching and speaking
# -- a second, later TTS call, from a different process, for the top news
# summary (see scripts/daily_briefing.ps1's header comment). Re-arming
# active listening and asking "anything else?" right after the initial
# SPEAK: line used to race against that: the mic would reopen for a real
# follow-up (or, worse, pick up the briefing window's own audio) while
# daily_briefing.py was still about to speak on its own, so it sounded
# like two things were both trying to respond. Suppressing the follow-up
# entirely for these intents means only the briefing window talks from
# here on, until it's done -- exactly the "only daily briefing should
# respond" behavior wanted. feedback/speech.py's playback lock still
# serializes the two if they do overlap, this is the fix for *why* they
# were racing in the first place, not a backstop for it.
_NO_FOLLOW_UP_INTENTS = {"daily_briefing"}


def _follow_up_key(bundle, outcome_key: str | None) -> str | None:
    """Which feedback.speech RESPONSES key to speak to re-arm the turn
    after an outcome, or None to stay quiet and drop back to idle instead
    of re-arming at all (see _NO_FOLLOW_UP_INTENTS above -- the only way
    None comes back today). Otherwise almost always the generic
    "anything_else" -- the one exception is right after open_app succeeds
    with app=brave, where asking "anything else?" skips past the obvious
    next step of searching for something. See
    RESPONSES["brave_search_prompt"] and routing/intents.yaml's
    web_search/open_apple_music intents, which is what a "yes" here
    actually routes to.
    """
    if bundle.intent in _NO_FOLLOW_UP_INTENTS:
        return None
    if (
        outcome_key == "command_confirmed"
        and bundle.intent == "open_app"
        and bundle.slots.get("app") in _SEARCH_FOLLOW_UP_APPS
    ):
        return "brave_search_prompt"
    return "anything_else"


def _drain(audio_q: queue.Queue) -> None:
    while True:
        try:
            audio_q.get_nowait()
        except queue.Empty:
            return


def _to_int16(audio: np.ndarray) -> np.ndarray:
    return np.int16(np.clip(audio, -1.0, 1.0) * 32767)


def _save_segment(chunks: list[np.ndarray], save_dir: str) -> str:
    audio = np.concatenate(chunks)
    filename = f"speech_{datetime.datetime.now():%Y%m%d_%H%M%S_%f}.wav"
    path = os.path.join(save_dir, filename)
    wav_write(path, SAMPLE_RATE, _to_int16(audio))
    return path


def _now() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def _load_whisper_model(model_size: str) -> WhisperModel:
    # Try the GPU the other three models already live on first; fall
    # back to CPU (int8, still fast enough for short command-length
    # audio) if this venv's CTranslate2 build can't see a usable
    # CUDA/cuBLAS/cuDNN install. Same "don't hard-fail on missing GPU"
    # posture as the rest of this pipeline.
    #
    # Constructing WhisperModel(device="cuda") alone doesn't prove CUDA
    # actually works, CTranslate2 loads the CUDA libraries lazily on
    # first inference rather than at construction time, so a missing
    # libcublas only surfaces once transcribe() is iterated. Force one
    # cheap warm-up inference here, at startup, so that failure (and
    # the fallback to CPU) happens now instead of mid-command later.
    try:
        gpu_model = WhisperModel(model_size, device="cuda", compute_type="float16")
        warmup = np.zeros(SAMPLE_RATE, dtype=np.float32)  # 1s of silence
        segments, _info = gpu_model.transcribe(warmup, language="en")
        list(segments)
        return gpu_model
    except Exception as exc:
        print(f"  faster-whisper: CUDA unavailable ({exc}), falling back to CPU")
        return WhisperModel(model_size, device="cpu", compute_type="int8")


def _transcribe(model: WhisperModel, wav_path: str) -> Transcription:
    # vad_filter=True is faster-whisper's own front-end suppression of
    # exactly this failure: it drops non-speech regions before decoding,
    # so a clip that's all decay usually yields no segments at all rather
    # than a hallucinated sentence. Belt and braces with the confidence
    # thresholds below it, since it's tuned for long-form audio and these
    # segments are already VAD-trimmed by Silero.
    segments, _info = model.transcribe(
        wav_path, language="en", vad_filter=True, initial_prompt=DEFAULT_INITIAL_PROMPT
    )
    segments = list(segments)
    if not segments:
        # Nothing decoded at all. Report it as maximum confidence that
        # there was no speech, so callers need only look at one field.
        return Transcription("", 1.0, DEFAULT_MIN_AVG_LOGPROB)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    # Worst case across segments for no_speech_prob (one silent segment
    # is enough to doubt the clip), mean for avg_logprob (already a
    # per-segment average, averaging again just weights them equally).
    return Transcription(
        text,
        max(segment.no_speech_prob for segment in segments),
        sum(segment.avg_logprob for segment in segments) / len(segments),
    )


def _log_transcript(
    log_path: str, wav_path: str | None, transcript: str, score: float | None
) -> None:
    # score is the *wake word* segment's verification similarity, not
    # this command segment's own, there isn't one anymore: identity is
    # established once, at the wake word, before active listening even
    # starts. None under --no-verify, where nothing was ever scored.
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "wav_path": wav_path,
        "transcript": transcript,
        "verify_score": round(score, 3) if score is not None else None,
    }
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Speech probability threshold, 0-1 (default: 0.5). Raise it in a noisy room.",
    )
    parser.add_argument(
        "--min-silence-ms",
        type=int,
        default=300,
        help="How long silence must last before a speech segment is considered over (default: 300).",
    )
    parser.add_argument(
        "--speech-pad-ms",
        type=int,
        default=30,
        help="Padding added to each side of a detected segment (default: 30).",
    )
    parser.add_argument(
        "--save-dir",
        default=DEFAULT_SAVE_DIR,
        help=f"Where to save captured speech segments (default: {DEFAULT_SAVE_DIR}).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't keep permanent captures. Segments that trigger the wake "
        "word are still written to a temp file for verification, then removed.",
    )
    parser.add_argument(
        "--wake-word",
        default=DEFAULT_WAKE_WORD,
        help=f"openWakeWord pretrained model name to trigger on (default: {DEFAULT_WAKE_WORD!r}).",
    )
    parser.add_argument(
        "--wake-threshold",
        type=float,
        default=0.5,
        help="Wake word score threshold, 0-1 (default: 0.5). Raise it to cut false triggers.",
    )
    parser.add_argument(
        "--active-window",
        type=float,
        default=10.0,
        help="Seconds to wait for a command after the wake word before giving "
        "up and going back to idle (default: 10.0). Segments rejected by the "
        "checks below don't end active listening, so this window has to cover "
        "a false start plus the command that follows it.",
    )
    parser.add_argument(
        "--reference",
        default=DEFAULT_REFERENCE,
        help=f"Path to the enrolled reference embedding (default: {DEFAULT_REFERENCE}).",
    )
    parser.add_argument(
        "--verify-threshold",
        type=float,
        default=None,
        help="Cosine similarity threshold for the wake word verification "
        "check. Default: auto-picks a lower threshold for short clips "
        "than long ones (see voice_auth/verify.py), since short clips "
        "(like 'hey jarvis' itself) score lower even for a genuine "
        "match. Pass a value here to override both with one flat "
        "threshold instead.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip speaker verification, just report wake word detections.",
    )
    parser.add_argument(
        "--whisper-model",
        default=DEFAULT_WHISPER_MODEL,
        help=f"faster-whisper model size to load (default: {DEFAULT_WHISPER_MODEL!r}). "
        "Larger sizes (medium, large-v3) are more accurate but slower and use more VRAM.",
    )
    parser.add_argument(
        "--transcript-log",
        default=DEFAULT_TRANSCRIPT_LOG,
        help=f"JSONL file to append transcripts of verified commands to (default: {DEFAULT_TRANSCRIPT_LOG}).",
    )
    parser.add_argument(
        "--no-transcribe",
        action="store_true",
        help="Skip Whisper transcription entirely, just report verification results.",
    )
    parser.add_argument(
        "--skip-wake",
        action="store_true",
        help="Tuning mode: skip the wake word requirement and verify every "
        "speech segment directly, back-to-back, as if each one were the "
        "wake word segment. Use this to dial in --verify-threshold for "
        "the wake word check, saying 'hey jarvis' each time rather than "
        "arbitrary phrases, since that's what actually gets verified now.",
    )
    parser.add_argument(
        "--no-feedback",
        action="store_true",
        help="Don't play the audio acknowledgment after the wake word is verified.",
    )
    parser.add_argument(
        "--feedback-model",
        default=str(DEFAULT_FEEDBACK_MODEL),
        help=f"Path to the Piper voice .onnx file for audio feedback (default: {DEFAULT_FEEDBACK_MODEL}).",
    )
    parser.add_argument(
        "--feedback-tail-ms",
        type=int,
        default=DEFAULT_FEEDBACK_TAIL_MS,
        help=f"How long to keep ignoring the mic after EKKO's audio has "
        f"actually stopped playing (default: {DEFAULT_FEEDBACK_TAIL_MS}). "
        "Room decay only, the output device's own buffering is measured "
        "rather than covered by this number. Raise it in a room with "
        "noticeable echo.",
    )
    parser.add_argument(
        "--min-command-ms",
        type=int,
        default=DEFAULT_MIN_COMMAND_MS,
        help=f"Ignore command segments shorter than this during active "
        f"listening (default: {DEFAULT_MIN_COMMAND_MS}). Low on purpose, "
        "it's here to drop clicks and thumps, not to filter by length.",
    )
    parser.add_argument(
        "--command-verify-threshold",
        type=float,
        default=DEFAULT_COMMAND_VERIFY_THRESHOLD,
        help=f"Cosine similarity floor for the command segment, checked "
        f"against the same enrolled reference as the wake word (default: "
        f"{DEFAULT_COMMAND_VERIFY_THRESHOLD}). Much lower than "
        "--verify-threshold by design: it only has to reject EKKO's own "
        "voice and other people's, not authorize yours, which the wake "
        "word already did. Ignored under --no-verify.",
    )
    parser.add_argument(
        "--max-no-speech-prob",
        type=float,
        default=DEFAULT_MAX_NO_SPEECH_PROB,
        help=f"Ignore a command segment when Whisper's own no_speech_prob "
        f"exceeds this (default: {DEFAULT_MAX_NO_SPEECH_PROB}). This is what "
        "catches its habit of hallucinating 'Bye.' or 'Thank you.' out of "
        "sub-second non-speech.",
    )
    parser.add_argument(
        "--min-avg-logprob",
        type=float,
        default=DEFAULT_MIN_AVG_LOGPROB,
        help=f"Ignore a command segment whose mean avg_logprob falls below "
        f"this (default: {DEFAULT_MIN_AVG_LOGPROB}). Backstop to "
        "--max-no-speech-prob for text Whisper produced but wasn't confident in.",
    )
    parser.add_argument(
        "--no-execute",
        action="store_true",
        help="Route commands and say the outcome, but don't actually run "
        "the matched handler script. Everything up to the subprocess call "
        "still happens, including the routing log, so this is the flag for "
        "watching what EKKO would do before letting it do it.",
    )
    parser.add_argument(
        "--routing-threshold",
        type=float,
        default=DEFAULT_ROUTING_THRESHOLD,
        help="Cosine similarity below which a transcript matches no intent "
        f"(default: {DEFAULT_ROUTING_THRESHOLD}). Separate from "
        "--verify-threshold, which is the speaker check. Measure this one "
        "with routing/calibrate.py rather than guessing at it.",
    )
    parser.add_argument(
        "--no-llm-fallback",
        action="store_true",
        help="Don't run the llm_fallback second pass on a NO_MATCH transcript. "
        "It stays a plain 'didn't catch that' exactly as it was before that "
        "module existed. See llm_fallback/README.md.",
    )
    parser.add_argument(
        "--no-research-tabs",
        action="store_true",
        help="Don't open Brave research tabs alongside an llm_fallback "
        "open-ended answer. The answer is still spoken; only the browser "
        "side effect is skipped. Has no effect if --no-llm-fallback is also "
        "given, since there's no answer to open tabs for either way. See "
        "scripts/open_research_tabs.ps1.",
    )
    parser.add_argument(
        "--no-domain-routing",
        action="store_true",
        help="Don't run routing/domains.py's anchor matcher on a NO_MATCH "
        "transcript before the llm_fallback call. Every fallback answer "
        "uses the flat SYSTEM_PROMPT.md exactly as before domain experts "
        "existed. Has no effect if --no-llm-fallback is also given. See "
        "domains/registry.yaml.",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Don't read memory/MEMORY.md as fallback context, and don't "
        "propose/score/write anything to it after an answer. EKKO behaves "
        "as if memory/ didn't exist. See memory/README.md.",
    )
    parser.add_argument(
        "--keep-captures",
        type=int,
        default=DEFAULT_KEEP_CAPTURES,
        help=f"Number of newest speech_*.wav segments to keep in --save-dir "
        f"(default: {DEFAULT_KEEP_CAPTURES}). Pruned after every wake word "
        "event via listener/prune_captures.py, not just at startup. "
        "transcripts.jsonl is never touched.",
    )
    parser.add_argument(
        "--manual-wake-hotkey",
        default=DEFAULT_MANUAL_WAKE_HOTKEY,
        help=f"Global hotkey (via the `keyboard` package's chord syntax) "
        f"that jumps straight to active listening, skipping the wake word "
        f"and speaker verification entirely (default: {DEFAULT_MANUAL_WAKE_HOTKEY!r}). "
        "Physical access to the keyboard is treated as the presence check "
        "in this path, same as it already is for the mic and scripts/. "
        "Requires the `keyboard` package; falls back to disabled with a "
        "warning if it isn't installed.",
    )
    parser.add_argument(
        "--no-manual-wake",
        action="store_true",
        help="Disable the manual wake hotkey entirely.",
    )
    parser.add_argument(
        "--hard-stop-hotkey",
        default=DEFAULT_HARD_STOP_HOTKEY,
        help=f"Global hotkey (via the `keyboard` package's chord syntax) "
        f"that immediately cancels an active listen (default: "
        f"{DEFAULT_HARD_STOP_HOTKEY!r}). Cuts off any TTS audio in "
        "progress, reopens the mic right away, and drops back to idle -- "
        "no acknowledgment is spoken. Requires the `keyboard` package; "
        "falls back to disabled with a warning if it isn't installed, or "
        "if the chord names a key `keyboard` can't map at all (e.g. "
        "ctrl+fn -- Fn is firmware-level on most keyboards and never "
        "reaches Windows as a real key), rather than crashing on it.",
    )
    parser.add_argument(
        "--no-hard-stop",
        action="store_true",
        help="Disable the hard stop hotkey entirely.",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Don't show the on-screen popup (ui/voice_ui.py) that mirrors "
        "listening/processing/speaking/error state. Runs in-process in its "
        "own thread, near-zero cost while hidden, so this is only for a "
        "headless run (e.g. no interactive desktop session) or a "
        "preference not to see it.",
    )
    parser.add_argument(
        "--response-session-timeout",
        type=float,
        default=SESSION_DEFAULT_TIMEOUT_S,
        help="Seconds an llm_fallback open-ended answer's response panel "
        f"(and the follow-up session it starts) stays open for (default: "
        f"{SESSION_DEFAULT_TIMEOUT_S}, a guessed starting point -- see "
        "ui/voice_ui.py). While the session is active, every outcome EKKO "
        "speaks is also shown in that same panel and slides the deadline "
        "forward again; once it lapses, later turns go back to speaking "
        "only. Purely visual -- doesn't change what routing/execute.py "
        "will do.",
    )
    args = parser.parse_args()

    listen(
        threshold=args.threshold,
        min_silence_ms=args.min_silence_ms,
        speech_pad_ms=args.speech_pad_ms,
        save_dir=None if args.no_save else args.save_dir,
        wake_word=args.wake_word,
        wake_threshold=args.wake_threshold,
        active_window=args.active_window,
        verify_enabled=not args.no_verify,
        reference_path=args.reference,
        verify_threshold=args.verify_threshold,
        skip_wake=args.skip_wake,
        transcribe_enabled=not args.no_transcribe,
        whisper_model_size=args.whisper_model,
        transcript_log=args.transcript_log,
        feedback_enabled=not args.no_feedback,
        feedback_model=args.feedback_model,
        feedback_tail_ms=args.feedback_tail_ms,
        execute_enabled=not args.no_execute,
        routing_threshold=args.routing_threshold,
        min_command_ms=args.min_command_ms,
        command_verify_threshold=args.command_verify_threshold,
        max_no_speech_prob=args.max_no_speech_prob,
        min_avg_logprob=args.min_avg_logprob,
        llm_fallback_enabled=not args.no_llm_fallback,
        research_tabs_enabled=not args.no_research_tabs,
        domain_routing_enabled=not args.no_domain_routing,
        memory_enabled=not args.no_memory,
        keep_captures=args.keep_captures,
        manual_wake_hotkey=None if args.no_manual_wake else args.manual_wake_hotkey,
        hard_stop_hotkey=None if args.no_hard_stop else args.hard_stop_hotkey,
        ui_enabled=not args.no_ui,
        response_session_timeout=args.response_session_timeout,
    )
