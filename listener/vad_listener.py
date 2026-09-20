"""
Always-on voice pipeline: VAD -> wake word -> speaker verification ->
command capture -> transcription -> routing -> execution, with an LLM
fallback for unmatched commands.

Silero VAD scores fixed 512-sample chunks (~32ms @ 16kHz); VADIterator turns
that into speech-start/speech-end events, with padding and a minimum
silence gap so pauses don't fragment a sentence. Every chunk is also fed to
openWakeWord continuously, not just during VAD-detected speech, which is
its intended usage and avoids clipping the wake word's onset.

Identity is checked on the WAKE WORD segment, not the command that follows.
Once "hey jarvis" fires and that segment ends, it goes through speaker
verification (voice_auth/verify.py). A match arms active listening for
--active-window seconds; the next accepted segment is the command. A
mismatch drops straight back to idle. (Verifying the short wake-word
phrase itself is deliberate: verify.py's SHORT_BUCKET has a threshold
calibrated for this clip length -- see --verify-threshold / --skip-wake to
retune it, saying the wake word during tuning rather than assuming the
default transfers.)

Checking identity at the wake word lets a verified user get an audible
acknowledgment immediately (--no-feedback), the same "go ahead" cue
Alexa/Google Assistant give. The mic stays gated until that ack has
genuinely finished playing plus a room-decay tail (--feedback-tail-ms),
otherwise EKKO transcribes its own greeting back as the command -- see
MicGate.

Active listening screens each candidate segment (_screen_command_segment):
long enough, from the enrolled speaker, and something Whisper is actually
confident was said. A failing segment is discarded and listening
continues -- only --active-window elapsing drops back to idle -- so a
cough or stray echo doesn't cost the user their turn. This identity check
(--command-verify-threshold) is much looser than the wake word's: it only
rejects non-user audio, since the wake word already authorized.

A passing segment is transcribed locally (faster-whisper), routed
(routing/: intent match -> slot extraction -> IntentBundle), and a matched
bundle's PowerShell handler is run. Segments where the wake word never
fired never touch the speaker model.

Permission boundary: a segment only reaches the router if the wake word
fired AND the speaker embedding matched. What the router can return is
bounded by routing/intents.yaml, and what that can name is bounded to
.ps1 files under scripts/ -- no path from speech to an arbitrary command,
by construction. --no-execute keeps everything above but runs nothing.

A NO_MATCH transcript gets one more pass via llm_fallback/: a single HTTPS
call to Gemini (llm_fallback/gemini/fallback_gemini.py, no subprocess, no
filesystem/shell access) that either picks a routing/intents.yaml command
(re-validated independently) or, if it's not a command, answers the
question directly -- no IntentBundle, no execute(). One call handles both;
see llm_fallback/README.md for why (and llm_fallback/gemini/README.md for
why Gemini replaced Claude here -- CLI cold-start and extended-thinking
token spend were the real latency source). --no-llm-fallback skips this
("didn't catch that" instead). An open-ended answer can also open a Brave
tab with the transcript plus up to 3 pages the fallback found via search
(best-effort, non-blocking -- see scripts/open_research_tabs.ps1 and
SYSTEM_PROMPT.md's `urls` field), announced via the research_tabs_opened
response only once Brave actually launched. Search grounding is OFF by
default (the free API key has zero quota, see llm_fallback/gemini/README.md's
"Known issue"), so today these tabs are plain search, not curated results.
--no-research-tabs skips both the tabs and the announcement.

Depends on voice_auth (enroll.py, verify.py) for identity; voice_auth has
no dependency back on this module.

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

--skip-wake is a tuning mode for --verify-threshold: it skips the wake
word requirement and verifies every speech segment directly and
immediately, as if each were the wake word segment, so you can repeat
"hey jarvis" back-to-back and watch similarity scores.

Manual wake (--manual-wake-hotkey, default ctrl+alt+w) jumps straight to
active listening, bypassing wake word and speaker verification entirely --
deliberate, not a gap, since physical keyboard access already meets the
same presence bar as the mic and scripts/. Useful in a noisy room or for
exercising routing/intents.yaml changes without speaking. Requires the
`keyboard` package; its absence disables the hotkey with a warning
instead of failing the listener.

Hard stop (--hard-stop-hotkey, default ctrl+alt+q) does the opposite:
kills an active listen. One press stops any TTS audio (sd.stop()),
reopens the mic immediately (skipping the echo-decay tail), and drops
back to idle -- no acknowledgment is spoken, since the point is not to
add more audio after telling it to stop. Same physical-presence reasoning
as manual wake. Requires `keyboard`; not ctrl+fn, since Fn is trapped by
keyboard firmware on most hardware and never reaches Windows as a real
scan code, so `keyboard` has no mapping for it and add_hotkey() raises
ValueError rather than failing to fire. Caught at registration (same as
the package being absent), so a bad chord disables the hotkey with a
warning instead of crashing the listener.
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
    # Optional: only used for --manual-wake-hotkey. Absence disables the
    # hotkey with a warning instead of failing the whole listener.
    import keyboard
except ImportError:
    keyboard = None

try:
    # Newer openwakeword releases fetch pretrained models on first use
    # instead of bundling them (older releases like 0.4.0 bundle the ONNX
    # models directly and don't need this).
    from openwakeword.utils import download_models
except ImportError:
    download_models = None

# voice_auth is a sibling package under the repo root, not this file's own
# directory (listener/) -- put the root on sys.path so imports resolve
# regardless of invocation cwd.
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
from memory.short_term import clear_short_memory, read_active_short_memory, write_short_memory
from ui.voice_ui import SESSION_DEFAULT_TIMEOUT_S, VoiceUI, VoiceUIState
from voice_auth.enroll import load_model as load_speaker_model
from voice_auth.enroll import load_reference
from voice_auth.verify import verify

# Cross-process signal from scripts/daily_briefing.py: written once its
# narration finishes speaking, so this process can re-arm "anything
# else?" at the right time instead of racing it or never re-arming.
# Filesystem flag because these processes share no memory (same approach
# as feedback/speech.py's playback lock).
BRIEFING_NARRATION_FLAG_PATH = Path(tempfile.gettempdir()) / "ekko_briefing_narration_done.flag"
# How long to wait for that flag before giving up. daily_briefing.py's
# narration (news + stocks, two Gemini calls + TTS each) can push past a
# minute and a half; this is generous relative to that so a crashed or
# closed briefing window doesn't leave this process waiting forever.
BRIEFING_FOLLOWUP_TIMEOUT_S = 180.0
# How often the main loop stats the flag file. The loop iterates every
# ~32ms (one audio chunk); checking that often is wasted work, so this
# throttles to a rate no human waiting on a spoken follow-up would notice.
BRIEFING_FOLLOWUP_POLL_S = 1.0

SAMPLE_RATE = 16000  # required by the model
CHUNK_SAMPLES = 512  # required chunk size at 16kHz, see silero_vad/utils_vad.py
# Anchored to this file, not cwd: usage is `python listener/vad_listener.py`
# from the repo root, where a bare "captures" would resolve to the repo
# root instead of listener/captures.
DEFAULT_SAVE_DIR = str(Path(__file__).resolve().parent / "captures")
DEFAULT_WAKE_WORD = "hey_jarvis"
DEFAULT_VAD_THRESHOLD = 0.5
DEFAULT_MIN_SILENCE_MS = 300
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
# Absolute, not cwd-relative: reference_embedding.pt lives in voice_auth/,
# a different directory than this file.
DEFAULT_REFERENCE = str(_PROJECT_ROOT / "voice_auth" / "reference_embedding.pt")
DEFAULT_WHISPER_MODEL = "medium"
# Same cwd-independence rationale as DEFAULT_SAVE_DIR.
DEFAULT_TRANSCRIPT_LOG = str(Path(__file__).resolve().parent / "captures" / "transcripts.jsonl")
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
    "Hey Jarvis, run system analysis. Open task manager. Lock the screen. "
    "Play. Pause. Next track. Previous track. Volume up. Volume down. Mute. "
    "Open Chrome. Open Brave. Open Spotify. Search Brave for the weather. "
    "Already done. Command confirmed. Access denied."
)


class Transcription(NamedTuple):
    """Whisper's output plus its confidence there was speech at all.

    A sub-second clip of room decay doesn't come back as an empty string --
    Whisper hallucinates a confident "Bye." or "Thank you." from its
    training data's filler phrases, which used to send a real command's
    turn to the router as NO_MATCH. no_speech_prob and avg_logprob are the
    only signals that separate those from a genuine short command.

    Produced by _transcribe(), judged by _screen_command_segment().
    """

    text: str
    no_speech_prob: float
    avg_logprob: float


class MicGate:
    """Keeps EKKO's own voice out of its own microphone.

    speak() blocks until playback finishes, but sounddevice's input
    callback runs on its own thread and keeps filling the queue the whole
    time -- without this gate, every acknowledgment/response gets queued
    and handed to VAD as the next speech segment. So the gate is checked
    inside the callback: while closed, chunks are dropped instead of
    queued, and nothing downstream ever sees them.

    Closing only for the duration of speak() isn't enough: sd.wait()
    returns once PortAudio hands off its last buffer, not once the sound
    is actually gone from the room, so a fixed window lets the final
    decaying syllable leak through as its own hallucinated "Bye."/
    "Thank you." speak() instead measures the real end-of-playback time;
    reopen_after() adds only room decay (tail_seconds) on top of it, a
    number that scales honestly regardless of response length.

    Read from the audio thread, written from the main one -- safe without
    a lock since each field is written by exactly one thread in one
    place, and a chunk landing on either side of the boundary is silence
    either way.
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
        """playback_done_at is speak()'s end-of-playback time, not now.
        Arm the deadline before clearing the flag, or a chunk can slip
        through in between.
        """
        self._muted_until = playback_done_at + self.tail_seconds
        self._speaking = False

    def force_open(self) -> None:
        """Reopen immediately, skipping the echo-decay tail.

        Only for hard-stop: sd.stop() already killed the audio, so
        there's no decay left to wait out.
        """
        self._speaking = False
        self._muted_until = 0.0


def _build_wake_word_model(wake_word: str) -> WakeWordModel:
    """openWakeWord's Model constructor shape has changed across
    versions, and inspect.signature() can't reliably detect which is
    installed (some releases wrap __init__ in a shim that hides the real
    signature). Try the modern kwarg and fall back to the older one on
    failure.
    """
    try:
        # Modern API: wakeword_models takes pretrained names directly and
        # resolves/downloads the model itself. onnx, not tflite, since
        # this project depends on onnxruntime.
        return WakeWordModel(wakeword_models=[wake_word], inference_framework="onnx")
    except TypeError:
        pass

    # Older releases (e.g. 0.4.x) bundle models and need a full path via
    # wakeword_model_paths, with the registry exposed as lowercase
    # `models` instead of `MODELS`.
    registry = getattr(openwakeword, "models", None) or getattr(openwakeword, "MODELS", None)
    model_info = registry.get(wake_word) if registry else None
    if model_info is None:
        raise ValueError(
            f"No bundled openWakeWord model named {wake_word!r}. "
            f"Available: {sorted(registry) if registry else '(none found)'}"
        )
    return WakeWordModel(wakeword_model_paths=[model_info["model_path"]])


def _resolve_prediction_key(oww_model: WakeWordModel, wake_word: str) -> str:
    """predict()'s result dict isn't always keyed by `wake_word` verbatim:
    0.4.x derives it from the bundled filename (e.g. "hey_jarvis_v0.1"),
    newer releases keep the passed-in name. Resolve once at startup
    instead of guessing on every chunk in the hot loop.
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
        # Fetches ONNX models on first run (cached after); no-op on older
        # openwakeword releases that bundle them already.
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

        # Tied to transcription, not its own flag: no transcript means
        # nothing to route.
        print("Loading intent router...")
        try:
            router = Router.load(threshold=routing_threshold)
        except ConfigError as exc:
            # A malformed intents.yaml would silently no-match every
            # command, which looks like a mic problem, not a config one --
            # fail loudly at startup instead.
            raise SystemExit(f"[routing] {exc}")

        if domain_routing_enabled and llm_fallback_enabled:
            # Reuses router.model (same MiniLM instance) instead of a
            # second copy -- see DomainRouter.load(). Only matters on the
            # NO_MATCH -> llm fallback path, see _handle_command().
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
            # Nice-to-have layered on the verification path, not a
            # dependency of it -- don't crash the pipeline over a missing
            # voice model.
            print(f"  {exc}\n  continuing without audio feedback")

    ui = None
    if ui_enabled:
        # Runs in its own thread, near-zero cost while hidden. Purely
        # cosmetic: nothing branches on `ui` besides `if ui:`, so behavior
        # is identical with --no-ui.
        ui = VoiceUI()
        ui.start()

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    # sounddevice's callback runs on its own thread -- keep it tiny and
    # hand chunks off via queue rather than doing VAD work inline, so the
    # audio device never blocks.
    audio_q: queue.Queue = queue.Queue()
    mic_gate = MicGate(tail_seconds=feedback_tail_ms / 1000)

    # Set from keyboard's hook thread, cleared from the main loop -- same
    # one-writer/one-reader lock-free reasoning as MicGate. Physical-
    # presence bypass: whoever's at the keyboard can already touch the mic
    # and scripts/, so this skips straight to active listening instead of
    # a speaker-verification check that exists to establish presence the
    # hotkey already has.
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
                # A chord `keyboard` has no scan code for at all (Fn is
                # the common case) -- disable with a warning rather than
                # crash.
                print(f"  manual wake hotkey {manual_wake_hotkey!r} not usable ({exc}), disabled")
                manual_wake_hotkey = None

    # Same lock-free flag pattern as manual_wake_event. Registered
    # independently: these are opposite doors (start/kill a listen), not
    # a pair.
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
            # EKKO is talking -- drop here so nothing downstream can ever
            # mistake its own voice for the user's. See MicGate.
            return
        samples = indata[:, 0]
        if ui is not None and awaiting_command:
            # Only worth it while the popup has bars to animate (active
            # listening). Reads `awaiting_command` as a closure over the
            # main loop's variable -- lock-free for the same reason as
            # MicGate.
            rms = float(np.sqrt(np.mean(np.square(samples))))
            ui.set_audio_level(min(1.0, rms * 6))  # cheap headroom scaling
        audio_q.put(samples.copy())

    speech_buffer: list[np.ndarray] = []
    in_speech = False
    wake_word_fired = False
    # True from a verified wake word until a segment is accepted as the
    # command or --active-window elapses. A segment that fails
    # _screen_command_segment() leaves this alone -- a non-command can't
    # end the turn. --skip-wake never sets this (every segment is
    # verified directly instead).
    awaiting_command = False
    awaiting_since: float | None = None
    # True right after "Anything else I can help with?" -- the next
    # accepted segment is checked against _is_decline() before routing.
    # Only set during that follow-up, so a "no" as the first command
    # after a wake word routes normally.
    expecting_reply = False
    # Deadline for the response-panel follow-up session an llm_fallback
    # open-ended answer starts (see _handle_command's session_until).
    # None = no active session, i.e. outcomes are only spoken, never
    # shown in the panel.
    session_until: float | None = None
    # Deadline for "still waiting on BRIEFING_NARRATION_FLAG_PATH". None =
    # nothing pending. Set right after a daily_briefing turn (see
    # _NO_FOLLOW_UP_INTENTS); cleared when the flag appears, the deadline
    # passes, or a fresh wake word makes this turn moot.
    pending_briefing_followup_until: float | None = None
    last_briefing_flag_check = 0.0
    # The wake word segment's own verification score, carried to the
    # command segment purely for logging -- that score is what actually
    # authorized the turn. The command segment is scored too, but only
    # for filtering, see _screen_command_segment().
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
        # PortAudio's default (low) latency leaves almost no buffer for a
        # 32ms callback (CHUNK_SAMPLES); CPU-heavy verification/wake-word
        # inference running synchronously on the main thread can stall
        # this callback past that deadline and drop samples. "high" asks
        # PortAudio for a bigger buffer to absorb that stall -- doesn't
        # change CHUNK_SAMPLES or the 32ms chunking VAD depends on.
        latency="high",
        callback=callback,
    ):
        try:
            while True:
                chunk = audio_q.get()
                if len(chunk) != CHUNK_SAMPLES:
                    continue  # partial block, e.g. right at stream start/stop

                # Deferred daily_briefing follow-up (see
                # pending_briefing_followup_until). Throttled to
                # BRIEFING_FOLLOWUP_POLL_S so this doesn't stat the flag
                # file on every ~32ms chunk; a no-op whenever nothing is
                # pending.
                if pending_briefing_followup_until is not None:
                    now = time.monotonic()
                    if now > pending_briefing_followup_until:
                        print(f"[{_now()}] daily_briefing follow-up: gave up waiting for narration to finish")
                        pending_briefing_followup_until = None
                        # The "Preparing your morning briefing…" panel
                        # shares BRIEFING_FOLLOWUP_TIMEOUT_S as its own
                        # timeout and is already closing itself -- this
                        # just resets the state chip, which has no
                        # timeout of its own.
                        if ui is not None:
                            ui.set_state(VoiceUIState.IDLE)
                    elif now - last_briefing_flag_check >= BRIEFING_FOLLOWUP_POLL_S:
                        last_briefing_flag_check = now
                        if BRIEFING_NARRATION_FLAG_PATH.exists():
                            # signal_narration_done() writes JSON
                            # ({"ts", "summary"}) -- `summary` is the real
                            # text just spoken, read here so the
                            # placeholder panel gets replaced with it.
                            # Malformed/missing content falls back to a
                            # generic line rather than showing nothing.
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
                    # sd.stop() is process-global, not stream-scoped, so
                    # this cuts _say()'s playback even without a reference
                    # to that stream. speak()'s blocking sd.wait() then
                    # returns as if playback finished normally.
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
                    clear_short_memory()  # the cancelled turn is done
                    print(f"[{_now()}] hard stop — active listen cancelled")
                    if ui is not None:
                        ui.set_state(VoiceUIState.IDLE)
                    continue

                if manual_wake_event.is_set():
                    manual_wake_event.clear()
                    if awaiting_command:
                        # Already listening -- a second press just means
                        # "still here", push the deadline out instead of
                        # stacking another acknowledgment.
                        awaiting_since = time.monotonic()
                        print(f"[{_now()}] manual wake — already listening, window extended")
                    else:
                        # The hotkey itself is the presence check (see its
                        # setup above) -- jump straight to the same
                        # active-listening state a verified wake word
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

                # Fed continuously, not gated on in_speech -- this is
                # openWakeWord's intended usage and keeps the wake word's
                # onset from being clipped while VAD is still deciding a
                # segment has started. Skipped once a command is already
                # being awaited (not a wake candidate anymore) or under
                # --skip-wake (wake_word_fired unused there).
                if not awaiting_command and not skip_wake:
                    predictions = oww_model.predict(_to_int16(chunk))
                    score = predictions.get(prediction_key, 0.0)
                    if in_speech and not wake_word_fired and score >= wake_threshold:
                        wake_word_fired = True
                        # A fresh, unrelated turn is starting -- drop any
                        # pending daily_briefing follow-up rather than let
                        # it fire "anything else?" mid-turn later.
                        pending_briefing_followup_until = None
                        print(f"[{_now()}] wake word {wake_word!r} detected (score={score:.2f})")

                # Gave up on a wake word with no command following --
                # --skip-wake never reaches awaiting_command so this
                # never fires for it.
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
                    # a stand-in for it -- identity is checked here, not
                    # on the command that follows.
                    is_wake_check = skip_wake or (wake_word_fired and not is_command)

                    if is_wake_check:
                        # Only touch disk if keeping captures or verify.py
                        # needs a real WAV.
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
                                # Acknowledge first, arm second -- the
                                # --active-window countdown should be
                                # time to talk, not time spent hearing
                                # EKKO finish a sentence.
                                if ui is not None:
                                    ui.set_transcript("")
                                _say(feedback_voice, "generic_ack", mic_gate, vad, audio_q, ui=ui)
                                awaiting_command = True
                                awaiting_since = time.monotonic()
                                if ui is not None:
                                    ui.set_state(VoiceUIState.LISTENING)
                            else:
                                print("  voice did not match enrolled speaker — ignoring")
                                # ERROR state while "access denied" plays,
                                # then back to idle -- the one wake-check
                                # outcome that doesn't arm active listening.
                                _say(
                                    feedback_voice, "access_denied", mic_gate, vad, audio_q,
                                    ui=ui, ui_state=VoiceUIState.ERROR,
                                )
                                if ui is not None:
                                    ui.set_state(VoiceUIState.IDLE)
                        else:
                            # --no-verify: trust the wake word alone.
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
                            # --no-verify) writes at least one segment --
                            # prune here, not just at startup, so a
                            # long-running listener doesn't fill the disk.
                            prune_captures(save_dir, keep_captures)

                        if skip_wake:
                            # Tuning mode never enters the command phase --
                            # every segment goes through this same check
                            # again.
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
                            # The turn survives -- deliberately skip
                            # clearing awaiting_command: a segment that
                            # wasn't a command shouldn't end active
                            # listening (that's exactly what the ack's
                            # echo tail used to do). --active-window is
                            # left alone too, so noise can't hold the
                            # window open forever.
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
                                    # ends the loop -- "no" is never
                                    # routed as a command.
                                    print(f"[{_now()}] declined — back to idle")
                                    _say(feedback_voice, "ok_bye", mic_gate, vad, audio_q, ui=ui)
                                    awaiting_command = False
                                    awaiting_since = None
                                    expecting_reply = False
                                    wake_verify_score = None
                                    # Session ended on purpose -- any
                                    # pending short-memory turn (unanswered
                                    # follow_up) is done, not just stale.
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
                                    # A grounded follow-up from the same
                                    # llm_fallback answer always wins over
                                    # the generic RESPONSES lookup; a
                                    # MATCHED command bundle never has one.
                                    follow_up_key = None
                                    follow_up_text = grounded_follow_up
                                    if grounded_follow_up is None and outcome_key is not None:
                                        follow_up_key = _follow_up_key(outcome_bundle, outcome_key)
                                    if follow_up_key is not None or follow_up_text is not None:
                                        # EKKO said something about the
                                        # outcome -- keep the turn going:
                                        # ask, then re-arm for a follow-up
                                        # instead of dropping to idle.
                                        # domain=matched_domain lets
                                        # render() prefer
                                        # "anything_else:<domain>" when
                                        # this turn's answer came from a
                                        # domain expert and Gemini didn't
                                        # propose its own follow_up.
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
                                        # Nothing was said (empty
                                        # transcript, --no-execute), or
                                        # _follow_up_key suppressed the
                                        # prompt (daily_briefing, whose
                                        # window keeps talking on its own
                                        # for several more seconds) --
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
                                            # daily_briefing's window is
                                            # still fetching/narrating for
                                            # up to BRIEFING_FOLLOWUP_TIMEOUT_S
                                            # more seconds on a different
                                            # process. PROCESSING + a
                                            # placeholder panel keeps the UI
                                            # visibly present for the wait
                                            # (setting IDLE here used to let
                                            # voice_ui's auto-hide close the
                                            # popup within ~1.5s, well
                                            # before narration even
                                            # started). The flag-detection
                                            # block above swaps in the real
                                            # text once it appears. Clear
                                            # any stale flag first so this
                                            # doesn't fire on a leftover.
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
                        # Ordinary segment: wake word never fired and
                        # we're not awaiting a command. Log if keeping
                        # captures, move on.
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
    then clean up after the gap.

    Every place EKKO opens its mouth goes through here -- saying "Done."
    into an open mic is the same self-hearing bug as the ack itself, and
    outcome responses land while the pipeline is most eager to listen.

    `ui`/`ui_state`: sets VoiceUI's state to `ui_state` (default SPEAKING)
    for the duration of the call. What the popup shows *after* is the
    caller's call, not this function's.

    `show_in_panel`: also puts the spoken text into the popup's response
    panel for `panel_timeout` seconds. Only _handle_command() sets this
    (llm_fallback open-ended answers, or outcomes during an active
    follow-up session) -- other call sites (acks, timeouts, "anything
    else?") are prompts, not responses to display.

    After playback: drains the queue (whatever was buffered before the
    gate closed is stale) and resets VAD's internal state (audio either
    side of the gap isn't continuous). Both happen while the gate is
    still muted for its decay tail, so nothing new arrives in between.

    `intent`/`domain`/`slots` are only meaningful for intent-/domain-aware
    RESPONSES keys (e.g. "command_confirmed", "anything_else:finance");
    other call sites omit them.

    `text_override`, when given, is spoken (and shown, if `show_in_panel`)
    instead of looking `response_key_` up in RESPONSES -- see
    routing/execute.py's spoken_override() and llm_fallback's answer, the
    two sources. `response_key_` is still required (it's what
    _handle_command uses to decide whether EKKO should say anything at
    all).

    Resolves text via feedback.speech.render() directly (rather than
    calling say() as a black box) so the resolved text is available to
    hand to the popup too -- calling render() again for the popup could
    pick a different random RESPONSES variant than what was actually
    spoken.
    """
    if voice is None or response_key_ is None:
        return
    text = (
        text_override
        if text_override is not None
        else render_response(response_key_, intent=intent, domain=domain, slots=slots)
    )
    if text is None:
        # A caller asking for a nonexistent key is a wiring bug, not one
        # worth taking the pipeline down over.
        print(f"[speech] unknown response key {response_key_!r}")
    if ui is not None:
        ui.set_state(ui_state)
        if show_in_panel and text:
            # Show now (padded timeout so it can't expire mid-playback) --
            # the real panel_timeout countdown starts below once speak()
            # actually finishes.
            ui.show_response(text, timeout_s=panel_timeout + 60.0)
    playback_done_at = 0.0
    gate.close()
    try:
        if text:
            playback_done_at = speak(voice, text)
            if ui is not None and show_in_panel:
                # show_response() resets its deadline on repeat calls --
                # restart it here so the reader gets the full
                # panel_timeout after hearing the response, not
                # panel_timeout minus however long it took to say it.
                ui.show_response(text, timeout_s=panel_timeout)
    finally:
        # 0.0 (missing key or failed synthesis) falls back to now -- a
        # tail measured from here is harmless with no audio, and never
        # reopening would deadlock.
        gate.reopen_after(playback_done_at or time.monotonic())
    _drain(audio_q)
    vad.reset_states()


# A closed, hand-written set rather than a routing/intents.yaml intent --
# that file is the security boundary for things EKKO can *do*, and "no"
# doesn't do anything.
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
    a command. Returns (reason, transcription): a reason means reject and
    keep listening, None means run it.

    Three checks, cheapest first:
    1. Duration -- free, kills clicks only.
    2. Identity (~100ms ECAPA) -- generalizes to any TTS response at any
       length, and closes the hole where anyone in the room could issue a
       command once the wake word verified. Threshold is far below the
       wake word's (DEFAULT_COMMAND_VERIFY_THRESHOLD): identity was
       already settled at the wake word, this only rejects non-user audio.
    3. Content -- Whisper's no_speech_prob/avg_logprob, free since we
       transcribe anyway. Catches non-speech identity can't judge (door,
       chime, cough). A backstop, not a second opinion: echo-tail and
       real-speech scores genuinely overlap, see DEFAULT_MAX_NO_SPEECH_PROB.
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

    execute() is handed a bundle, never the transcript -- what crosses
    into subprocess territory is an intent key and slot values that came
    from routing/intents.yaml, not raw Whisper output. See routing/execute.py.

    `session_until`/`session_timeout`: an llm_fallback open-ended answer
    always shows in the popup's response panel and starts a follow-up
    session. While `session_until` (a monotonic deadline) hasn't passed,
    every later outcome this function speaks is also shown in that panel
    (not a fresh one) and speaking it extends the deadline by
    `session_timeout`. Once it lapses, later turns revert to speaking
    only. Purely a display choice -- execute()'s behavior never changes
    based on whether a session is open.

    Returns (outcome_key, bundle, session_until, grounded_follow_up,
    matched_domain):
    - outcome_key: the RESPONSES key spoken (None if EKKO stayed quiet).
    - bundle: for the caller to pick a follow-up prompt via _follow_up_key.
    - session_until: updated deadline to pass back in next call.
    - grounded_follow_up: a specific next step from the same llm_fallback
      answer (SYSTEM_PROMPT.md's `follow_up`), or None otherwise -- a
      MATCHED bundle never calls Gemini so has nothing to ground. Caller
      prefers this over _follow_up_key() when non-None.
    - matched_domain: the domains/registry.yaml key that supplied this
      turn's system prompt, or None. Let the caller pass it as _say()'s
      `domain` so a domain's persona isn't dropped into the flat
      "anything_else" just because Gemini didn't populate follow_up.
    """
    session_active = session_until is not None and time.monotonic() < session_until

    # A short-memory turn only means something while its session is still
    # active -- once inactive, any leftover short_term.json is from an
    # unrelated conversation. Clear rather than let a later session
    # stumble into it.
    if not session_active:
        clear_short_memory()

    bundle = router.route(transcript)  # also appends to routing/logs/routing.jsonl
    print(f"  {bundle.describe()}")

    matched_domain: str | None = None
    if bundle.status is RoutingStatus.NO_MATCH and llm_fallback_enabled:
        # Domain detection runs strictly between NO_MATCH and the Gemini
        # call: never before it (a command match always wins) and never
        # in competition with it (a domain match only picks which
        # system-prompt bundle the same Gemini call gets). Only a
        # transcript that clears neither the router nor any domain
        # threshold falls through to the flat SYSTEM_PROMPT.md. See
        # routing/domains.py and domains/registry.yaml.
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
            # Read fresh each call -- same never-trust-a-stale-load
            # posture as routing/route.py's intents.yaml reload.
            memory_context = read_memory().as_prompt_block() or None

        # session_active, not just "does short_term.json exist" -- guards
        # a same-turn race: only this call's own deadline should decide
        # whether a leftover turn is still live.
        pending_short_memory = read_active_short_memory() if session_active else None
        if pending_short_memory is not None and pending_short_memory.follow_up_answer is None:
            # Presumed reply to pending_short_memory.follow_up -- record
            # now even though the fields sent to Gemini below come from
            # the object read a moment ago. A new follow_up from this
            # turn's own answer overwrites it anyway.
            write_short_memory(follow_up_answer=transcript)

        # One Gemini call that both re-checks for a command and, if
        # empty, answers an open-ended question -- see
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
            # Validated command pick -- falls into the execute() path
            # below like a direct embedding match.
            print(f"  [llm_fallback] {fallback_outcome.bundle.describe()}")
            bundle = fallback_outcome.bundle
        elif fallback_outcome.answer:
            # Not a command -- an open-ended answer. Never produces an
            # IntentBundle or touches execute(), so it's spoken/returned
            # here instead of the MATCHED path below. Always shown in the
            # panel and always starts/extends the follow-up session.
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
                # follow_up now expects a reply to -- read back next call
                # as pending_short_memory. Always a fresh write; any
                # earlier unanswered follow_up it supersedes is moot.
                write_short_memory(
                    prior_question=transcript,
                    prior_answer=fallback_outcome.answer,
                    follow_up=fallback_outcome.follow_up,
                )
            else:
                # No follow_up -- nothing for a next reply to attach to,
                # and this write already recorded the pending turn as
                # answered. Clear rather than leave a dead-end turn on disk.
                clear_short_memory()
            if research_tabs_enabled and fallback_outcome.urls:
                # Gated on urls being non-empty, not just
                # research_tabs_enabled: urls is the fallback model's own
                # signal it actually searched and found something worth
                # linking (see SYSTEM_PROMPT.md's `urls` rule). Otherwise
                # every plain conversational answer would pop a useless
                # Brave tab.
                #
                # Best-effort, never blocks the turn. open_research_tabs()
                # returns as soon as the process launches, not once Brave
                # finishes, so the "pulling up some helpful sites" line
                # starts alongside the tabs rather than trailing behind
                # them. tabs_error only covers failures known before
                # speaking (script missing, process failed to start); a
                # later failure is logged by open_research_tabs()'s
                # background reaper.
                tabs_error = open_research_tabs(transcript, fallback_outcome.urls)
                if tabs_error:
                    print(f"  [llm_fallback] research tabs: {tabs_error}")
                else:
                    _say(voice, "research_tabs_opened", gate, vad, audio_q, ui=ui)

            if pending_facts is not None and fallback_outcome.memory_candidate is not None:
                # Runs after _say() already spoke the answer, never
                # blocking it -- cheap enough (a few MiniLM embeds + one
                # small write) to stay inline. propose_and_score() is the
                # actual gatekeeper; this just wires its decision to disk.
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
    # bundle.intent/slots let _say() pick an intent-specific confirmation
    # (e.g. "Opened chrome.") when MATCHED -- harmless for other statuses,
    # which have no matching "<key>:<intent>" RESPONSES entry anyway.
    #
    # override_text is None unless a handler opted in via a final
    # "SPEAK: ..." stdout line (routing/execute.py's spoken_override(),
    # currently only system_diagnosis.ps1); _say() falls back to fixed
    # RESPONSES text otherwise.
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
    # No grounded follow-up here: a MATCHED bundle (or NO_MATCH with
    # llm_fallback disabled) never called Gemini, so the caller falls
    # through to _follow_up_key()'s generic "anything_else". matched_domain
    # is always None too -- a MATCHED bundle short-circuits before domain
    # detection runs, and the "no command, no answer" path has no
    # domain-flavored content to protect either.
    return outcome_key, bundle, session_until, None, matched_domain


# Which open_app targets get the more specific brave_search_prompt
# follow-up instead of the generic "anything_else". A set, not a single
# constant, so more apps can opt in later.
_SEARCH_FOLLOW_UP_APPS = {"brave"}

# Intents whose confirmation isn't the end of what EKKO has to say.
# daily_briefing hands off to its own detached window
# (scripts/daily_briefing.py), which keeps fetching and speaking on its
# own for a while. Re-arming "anything else?" right after the initial
# confirmation used to race that second TTS call, making it sound like
# two things were responding at once. Suppressing the follow-up for these
# intents means only the briefing window talks from here on.
_NO_FOLLOW_UP_INTENTS = {"daily_briefing"}


def _follow_up_key(bundle, outcome_key: str | None) -> str | None:
    """Which RESPONSES key re-arms the turn after an outcome, or None to
    drop back to idle instead (see _NO_FOLLOW_UP_INTENTS). Otherwise
    almost always "anything_else" -- the exception is right after
    open_app succeeds with app=brave, where the obvious next step is
    searching for something. See RESPONSES["brave_search_prompt"].
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
    # Try the GPU the other models live on, fall back to CPU (int8, still
    # fast enough for short commands) if this venv's CTranslate2 can't see
    # CUDA/cuBLAS/cuDNN.
    #
    # WhisperModel(device="cuda") alone doesn't prove CUDA works --
    # CTranslate2 loads CUDA libraries lazily on first inference, so a
    # missing libcublas only surfaces mid-transcribe. Force one cheap
    # warm-up inference here so that failure (and the CPU fallback)
    # happens at startup instead.
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
    # exactly this failure -- drops non-speech before decoding, so an
    # all-decay clip usually yields no segments rather than a
    # hallucinated sentence. Belt and braces with the confidence
    # thresholds below, since it's tuned for long-form audio and these
    # segments are already VAD-trimmed.
    segments, _info = model.transcribe(
        wav_path, language="en", vad_filter=True, initial_prompt=DEFAULT_INITIAL_PROMPT
    )
    segments = list(segments)
    if not segments:
        # Nothing decoded -- report max confidence there was no speech so
        # callers only need to check one field.
        return Transcription("", 1.0, DEFAULT_MIN_AVG_LOGPROB)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    # Worst case across segments for no_speech_prob (one silent segment is
    # enough to doubt the clip), mean for avg_logprob (already a
    # per-segment average).
    return Transcription(
        text,
        max(segment.no_speech_prob for segment in segments),
        sum(segment.avg_logprob for segment in segments) / len(segments),
    )


def _log_transcript(
    log_path: str, wav_path: str | None, transcript: str, score: float | None
) -> None:
    # score is the *wake word* segment's verification similarity -- the
    # command segment no longer has its own, identity is established once
    # at the wake word. None under --no-verify.
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
        default=DEFAULT_VAD_THRESHOLD,
        help=f"Speech probability threshold, 0-1 (default: {DEFAULT_VAD_THRESHOLD}). Raise it in a noisy room.",
    )
    parser.add_argument(
        "--min-silence-ms",
        type=int,
        default=DEFAULT_MIN_SILENCE_MS,
        help=f"How long silence must last before a speech segment is considered over (default: {DEFAULT_MIN_SILENCE_MS}).",
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
        default=DEFAULT_WAKE_THRESHOLD,
        help=f"Wake word score threshold, 0-1 (default: {DEFAULT_WAKE_THRESHOLD}). Raise it to cut false triggers.",
    )
    parser.add_argument(
        "--active-window",
        type=float,
        default=DEFAULT_ACTIVE_WINDOW_S,
        help="Seconds to wait for a command after the wake word before giving "
        f"up and going back to idle (default: {DEFAULT_ACTIVE_WINDOW_S}). Segments rejected by the "
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
