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
Once "hey ekko" fires and that segment ends, it goes through speaker
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

A NO_MATCH transcript gets one more pass via llm_fallback/: a single call
to whichever provider --provider selects (llm_fallback/brain/core.py's
attempt_fallback(), no filesystem/shell access regardless of provider)
that either picks a routing/intents.yaml command (re-validated
independently) or, if it's not a command, answers the question directly
-- no IntentBundle, no execute(). One call handles both; see
llm_fallback/README.md for why, and for how provider selection works.
--no-llm-fallback skips this
("didn't catch that" instead). An open-ended answer that came back with
`urls` can offer a Brave research tab: an avatar card with Yes/No that
closes after RESEARCH_OFFER_TIMEOUT_S, announced via the research_offer
response. Nothing opens unless the user says yes (see
scripts/open_research_tabs.ps1). --no-research-tabs skips the offer.

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
"hey ekko" back-to-back and watch similarity scores.

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

import numpy as np
import openwakeword
import sounddevice as sd
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

from paths import data  # noqa: E402
from feedback.speech import DEFAULT_MODEL_PATH as DEFAULT_FEEDBACK_MODEL
from feedback.speech import load_voice as load_feedback_voice
from feedback.speech import render as render_response
from feedback.speech import play_chime, speak
from routing.bundle import RoutingStatus
from routing.config import ConfigError
from routing.execute import execute, response_key, spoken_override
from routing.matcher import DEFAULT_THRESHOLD as DEFAULT_ROUTING_THRESHOLD
from routing.route import Router
from routing.domains import DomainRouter
from domains.loader import compose_system_instruction
from llm_fallback.brain import attempt_with_failover
from llm_fallback.brain.providers import get_provider
from llm_fallback.brain.core import default_provider
from llm_fallback.catalog import PROVIDERS
from llm_fallback.brain.prompt import base_system_instruction

RESEARCH_OFFER_TIMEOUT_S = 5.0  # how long the Yes/No card waits before closing
from listener import control, segment_log
from listener.audio_setup import apply_devices, settings_defaults
from listener.segmenter import HoldToTalk, PreRoll, segment_seconds
from listener.stt import Transcription, ekko_phrases, load_engine, screen_transcription
from listener.text_correct import apply_custom_words
from listener.prune_captures import DEFAULT_KEEP as DEFAULT_KEEP_CAPTURES
from listener.prune_captures import prune as prune_captures
from memory.store import log_decision as log_memory_decision, read_memory, write_memory
from memory.scoring import PendingFactCache, propose_and_score
from memory.context import gather as gather_context
from memory.day_log import record_turn
from ui.tauri_ui import TauriUI
from ui.voice_ui import SESSION_DEFAULT_TIMEOUT_S, VoiceUI, VoiceUIState
from voice_auth.enroll import load_model as load_speaker_model
from voice_auth.enroll import load_reference
from voice_auth.verify import verify
from listener.defaults import (  # noqa: E402  defaults live in listener/defaults.py so the backend can read them without torch
    DEFAULT_SAVE_DIR,
    DEFAULT_WAKE_WORD,
    DEFAULT_VAD_THRESHOLD,
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_COMMAND_MIN_SILENCE_MS,
    DEFAULT_WAKE_THRESHOLD,
    DEFAULT_ACTIVE_WINDOW_S,
    DEFAULT_MANUAL_WAKE_HOTKEY,
    DEFAULT_HARD_STOP_HOTKEY,
    DEFAULT_REFERENCE,
    DEFAULT_WHISPER_MODEL,
    DEFAULT_TRANSCRIPT_LOG,
    DEFAULT_FEEDBACK_TAIL_MS,
    DEFAULT_MIN_COMMAND_MS,
    DEFAULT_COMMAND_VERIFY_THRESHOLD,
    DEFAULT_MAX_NO_SPEECH_PROB,
    DEFAULT_MIN_AVG_LOGPROB,
    DEFAULT_CUSTOM_WORDS,
    DEFAULT_PREROLL_MS,
    DEFAULT_PUSH_TO_TALK_HOTKEY,
    PUSH_TO_TALK_MAX_S,
    DEFAULT_WORD_CORRECTION_THRESHOLD,
)

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
# file_operation reuses the same flag (control_center/hands_on/file_gateway/__main__.py
# writes it when its approval terminal closes), but a human reading and
# answering an approval prompt takes far longer than a narration.
FILE_OP_FOLLOWUP_TIMEOUT_S = 600.0
# How often the main loop stats the flag file. The loop iterates every
# ~32ms (one audio chunk); checking that often is wasted work, so this
# throttles to a rate no human waiting on a spoken follow-up would notice.
BRIEFING_FOLLOWUP_POLL_S = 1.0

SAMPLE_RATE = 16000  # required by the model
CHUNK_SAMPLES = 512  # required chunk size at 16kHz, see silero_vad/utils_vad.py


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
        # Bumped on every close(); lets the main loop tell that EKKO spoke
        # in between two blocks (see PreRoll).
        self.epoch = 0

    def is_open(self) -> bool:
        return not self._speaking and time.monotonic() >= self._muted_until

    def close(self) -> None:
        self.epoch += 1
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


def _build_wake_word_model(wake_word: str, feature_models_dir: Path) -> WakeWordModel:
    """openWakeWord's Model constructor shape has changed across
    versions, and inspect.signature() can't reliably detect which is
    installed (some releases wrap __init__ in a shim that hides the real
    signature). Try the modern kwarg and fall back to the older one on
    failure.
    """
    try:
        # Modern API: wakeword_models takes pretrained names directly and
        # resolves/downloads the model itself. onnx, not tflite, since
        # this project depends on onnxruntime. melspec/embedding paths
        # point at feature_models_dir (see their download above this
        # function's call site) rather than openWakeWord's own default
        # (its own install directory) -- the installed app's files are
        # never written to, only DATA_DIR is.
        return WakeWordModel(
            wakeword_models=[wake_word],
            inference_framework="onnx",
            melspec_model_path=str(feature_models_dir / "melspectrogram.onnx"),
            embedding_model_path=str(feature_models_dir / "embedding_model.onnx"),
        )
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
    if wake_word.endswith(".onnx"):
        # A model loaded from a file is keyed by its filename stem.
        wake_word = Path(wake_word).stem
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
    command_min_silence_ms: int,
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
    verify_commands: bool = False,
    llm_provider: str | None = None,  # None: whatever the UI says at call time
    preroll_ms: int = DEFAULT_PREROLL_MS,
    push_to_talk: bool = False,
    push_to_talk_hotkey: str = DEFAULT_PUSH_TO_TALK_HOTKEY,
    ack_style: str = "speech",
    stt_engine: str = "auto",
    parakeet_model: str = "tdt-0.6b-v2",
    custom_words: list[str] | None = None,
    word_correction_threshold: float = DEFAULT_WORD_CORRECTION_THRESHOLD,
    whisper_device: str = "auto",
    whisper_cpu_threads: int = 0,
    input_device: str | None = None,
    output_device: str | None = None,
) -> None:
    # Before the mic stream opens and before any playback: both read
    # sounddevice's defaults.
    for note in apply_devices(input_device, output_device):
        print(f"  {note}")
    if input_device or output_device:
        print(f"Audio devices: input={sd.default.device[0]}, output={sd.default.device[1]}")
    print("Loading Silero VAD model...")
    model = load_silero_vad()
    vad = VADIterator(
        model,
        threshold=threshold,
        sampling_rate=SAMPLE_RATE,
        min_silence_duration_ms=min_silence_ms,
        speech_pad_ms=speech_pad_ms,
    )

    idle_min_silence_samples = vad.min_silence_samples
    command_min_silence_samples = SAMPLE_RATE * command_min_silence_ms / 1000

    if wake_word.endswith(".onnx"):
        # Custom-trained model (e.g. listener/models/hey_ekko.onnx). Relative
        # paths are anchored to the repo root, same as the other defaults.
        wake_path = Path(wake_word)
        if not wake_path.is_absolute():
            wake_path = _PROJECT_ROOT / wake_path
        if not wake_path.is_file():
            raise SystemExit(f"[wake word] model file not found: {wake_path}")
        wake_word = str(wake_path)

    print(f"Loading openWakeWord model ({wake_word})...")
    # openWakeWord always needs its own melspectrogram/embedding
    # (feature-extraction) models, regardless of which wake word is used
    # -- separate from the wake-word-specific model, and newer releases
    # don't bundle them in the pip package at all. Fetched into DATA_DIR
    # (not openWakeWord's own install directory, which may not be
    # writable, and must never be, see paths.py) on first run; a no-op
    # after. Previously this only ran for a pretrained wake word name,
    # which silently skipped fetching these for our own hey_ekko.onnx and
    # crashed the frozen build the first time anyone installed it (its
    # own package directory ships without them).
    feature_models_dir = data("openwakeword")
    if download_models is not None:
        # [wake_word], not []: download_models([]) means "download every
        # pretrained model" (openwakeword's own default), which would
        # pull down alexa/hey_jarvis/timer/etc. we never use. A custom
        # .onnx filename like ours never substring-matches an official
        # pretrained name, so this still fetches nothing extra for it --
        # only the always-fetched feature/VAD models below.
        download_models([wake_word], target_directory=str(feature_models_dir))
    oww_model = _build_wake_word_model(wake_word, feature_models_dir)
    prediction_key = _resolve_prediction_key(oww_model, wake_word)

    speaker_model = None
    reference = None
    if verify_enabled and not os.path.isfile(reference_path):
        # First run: nobody has enrolled yet. Run on the wake word alone
        # rather than crash-loop under the supervisor; enrolling turns
        # verification on at the next start.
        print(
            f"  No voice enrollment at {reference_path}; speaker verification is off "
            "until you enroll (python voice_auth/enroll.py <wav folder> <that path>)."
        )
        verify_enabled = False
    if verify_enabled:
        print("Loading speaker verification model...")
        speaker_model = load_speaker_model()
        reference = load_reference(reference_path)

    whisper_model = None
    echo_phrases: frozenset[str] = frozenset()
    router = None
    domain_router = None
    pending_facts = None
    if transcribe_enabled:
        print(f"Loading speech recognition (engine: {stt_engine})...")
        whisper_model = load_engine(
            stt_engine, whisper_model_size, whisper_device, whisper_cpu_threads, parakeet_model
        )
        if whisper_model.fallback_reason:
            print(f"  {whisper_model.fallback_reason}")
        echo_phrases = ekko_phrases()

        # Tied to transcription, not its own flag: no transcript means
        # nothing to route.
        print("Loading intent router...")
        try:
            router = Router.load(threshold=routing_threshold)
        except ConfigError as exc:
            # A malformed intents.yaml would silently no-match every
            # command, which looks like a mic problem, not a config one --
            # fail loudly at startup instead.
            raise SystemExit(f"[routing] {exc}") from exc

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
        print("Loading TTS voice...")
        try:
            feedback_voice = load_feedback_voice(feedback_model)
        except (FileNotFoundError, RuntimeError) as exc:
            # Nice-to-have layered on the verification path, not a
            # dependency of it -- don't crash the pipeline over a missing
            # voice model.
            print(f"  {exc}\n  continuing without audio feedback")

    ui = None
    if ui_enabled:
        # Runs in its own thread, near-zero cost while hidden. Purely
        # cosmetic: nothing branches on `ui` besides `if ui:`, so behavior
        # is identical with --no-ui.
        # Under the backend the ekko-ui avatar window draws it (TauriUI posts
        # state/transcript/response); run standalone, the Tk popup does.
        ui = TauriUI() if control.supervised() else VoiceUI()
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
    class _HardStop(threading.Event):
        # The main loop is blocked in speak()/processing during a turn, so
        # it can't poll this flag; cut playback right when it's set (sd.stop()
        # is process-global and thread-safe) and let the loop reset state later.
        def set(self):
            sd.stop()
            super().set()

    hard_stop_event = _HardStop()
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

    # Optional hold-to-talk. Polled each audio block with keyboard.is_pressed()
    # rather than hooked: a key-up callback is easy to lose, and the state
    # of the keys right now is exactly what's needed.
    ptt = None
    if push_to_talk:
        if keyboard is None:
            print("  `keyboard` package not installed, hold-to-talk disabled (pip install keyboard)")
        elif skip_wake:
            print("  hold-to-talk is not available with --skip-wake")
        else:
            try:
                keyboard.parse_hotkey(push_to_talk_hotkey)  # raises ValueError for a key it can't map
                ptt = HoldToTalk(PUSH_TO_TALK_MAX_S)
                print(f"Hold-to-talk hotkey: {push_to_talk_hotkey!r} (hold it, speak, let go)")
            except ValueError as exc:
                print(f"  hold-to-talk hotkey {push_to_talk_hotkey!r} not usable ({exc}), disabled")
    via_ptt = False  # the segment being judged came from a held key, for the log

    # The UI's Wake button, via backend/supervisor.py's command queue (long-polled over HTTP).
    if control.supervised():
        control.start_command_reader(
            {"wake": manual_wake_event, "stop": hard_stop_event, "reload_llm": _planned_models.clear}
        )

    # Overflow records from the audio thread; the main loop writes them, so
    # the callback never touches the disk.
    log_q: queue.SimpleQueue = queue.SimpleQueue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[stream warning] {status}")
            # PortAudio reports the loss on the callback *after* it; the
            # gate rarely flips within one 32ms block, so its state now is
            # the state during the gap.
            overflow = segment_log.overflow_event(status, gate_open=mic_gate.is_open())
            if overflow is not None:
                log_q.put(overflow)
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
    # Blocks heard just before speech, so a segment keeps its first syllable.
    preroll = PreRoll(preroll_ms)
    lead_blocks = 0  # how many of speech_buffer's blocks are pre-roll
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
        f"(threshold={threshold}, min_silence={min_silence_ms}ms "
        f"(active listen: {command_min_silence_ms}ms), "
        f"wake_word={wake_word!r}). Ctrl+C to stop."
    )
    if control.supervised():
        control.report_ready()
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
                while not log_q.empty():
                    segment_log.write(log_q.get())
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
                            speak_text = None
                            try:
                                flag_payload = json.loads(BRIEFING_NARRATION_FLAG_PATH.read_text(encoding="utf-8"))
                                narration_summary = flag_payload.get("summary")
                                # A consulting question from hands_on (see
                                # control_center/hands_on/file_gateway/consult.py): said
                                # instead of "anything else?", and the
                                # session is opened so the reply gets the
                                # short memory that question was saved to.
                                speak_text = flag_payload.get("speak")
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
                            if speak_text:
                                session_until = time.monotonic() + response_session_timeout
                            _say(
                                feedback_voice,
                                "ambiguous_answer" if speak_text else "anything_else",
                                mic_gate, vad, audio_q,
                                text_override=speak_text,
                                ui=ui,
                            )
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
                    preroll.clear()
                    if ptt is not None:
                        ptt.cancel()
                    wake_word_fired = False
                    awaiting_command = False
                    awaiting_since = None
                    expecting_reply = False
                    session_until = None
                    pending_briefing_followup_until = None
                    wake_verify_score = None
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
                        segment_log.write({"event": "activation", "source": "manual"})
                        if ui is not None:
                            ui.set_transcript("")
                        _ack(ack_style, feedback_voice, mic_gate, vad, audio_q, preroll, ui=ui)
                        awaiting_command = True
                        awaiting_since = time.monotonic()
                        expecting_reply = False
                        wake_verify_score = None
                        if ui is not None:
                            ui.set_state(VoiceUIState.LISTENING)

                forced_event = None  # a release ends the segment without VAD's say-so
                if ptt is not None:
                    step = ptt.update(keyboard.is_pressed(push_to_talk_hotkey), time.monotonic())
                    if step == "press":
                        # Holding the key is the presence check (like manual wake),
                        # and skips the acknowledgment: you're already talking.
                        pending_briefing_followup_until = None
                        in_speech = False
                        wake_word_fired = False
                        preroll.add(chunk, mic_gate.epoch)
                        speech_buffer, lead_blocks = preroll.take()  # keeps the lead-in
                        vad.reset_states()
                        awaiting_command = True
                        awaiting_since = time.monotonic()
                        expecting_reply = False
                        wake_verify_score = None
                        print(f"[{_now()}] hold-to-talk — speak, then let go")
                        segment_log.write({"event": "activation", "source": "push_to_talk"})
                        if ui is not None:
                            ui.set_transcript("")
                            ui.set_state(VoiceUIState.LISTENING)
                        continue
                    if ptt.active:
                        speech_buffer.append(chunk)
                        heard = vad(chunk, return_seconds=True)  # only to know something was said
                        if heard and "start" in heard:
                            ptt.heard_speech()
                        continue
                    if step == "release":
                        if not ptt.voiced:
                            print(f"[{_now()}] hold-to-talk — nothing heard, ignoring")
                            segment_log.write({
                                "event": "segment", "kind": "command", "via": "push_to_talk",
                                "duration_s": round(segment_seconds(len(speech_buffer), lead_blocks), 3),
                                "outcome": "rejected", "reason": "nothing said",
                            })
                            speech_buffer = []
                            vad.reset_states()
                            awaiting_command = False
                            awaiting_since = None
                            if ui is not None:
                                ui.set_state(VoiceUIState.IDLE)
                            continue
                        forced_event = {"end": 0.0}
                        in_speech = False
                        via_ptt = True
                        awaiting_since = time.monotonic()  # a long hold mustn't read as a timeout

                if in_speech:
                    speech_buffer.append(chunk)
                else:
                    preroll.add(chunk, mic_gate.epoch)

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
                    segment_log.write({"event": "timeout", "window_s": active_window})
                    _say(feedback_voice, "listening_timeout", mic_gate, vad, audio_q, ui=ui)
                    if ui is not None:
                        ui.set_state(VoiceUIState.IDLE)

                # VADIterator reads min_silence_samples on every chunk, so
                # swapping it here takes effect immediately, even for a
                # segment already in progress when active listening starts.
                vad.min_silence_samples = (
                    command_min_silence_samples if awaiting_command else idle_min_silence_samples
                )
                event = forced_event or vad(chunk, return_seconds=True)
                if event is None:
                    continue

                if "start" in event:
                    in_speech = True
                    wake_word_fired = False
                    # The chunk VAD fired on was just added to the pre-roll.
                    speech_buffer, lead_blocks = preroll.take()
                    print(f"[{_now()}] speech started")

                elif "end" in event:
                    in_speech = False
                    duration = segment_seconds(len(speech_buffer), lead_blocks)
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
                            verify_started = time.perf_counter()
                            is_match, similarity = verify(
                                reference, speaker_model, path, verify_threshold
                            )
                            segment_log.write({
                                "event": "segment", "kind": "wake", "duration_s": round(duration, 3),
                                "outcome": "accepted" if is_match else "rejected",
                                "reason": None if is_match else "not the enrolled speaker",
                                "verify_score": round(float(similarity), 3),
                                "verify_ms": round((time.perf_counter() - verify_started) * 1000, 1),
                            })
                            if is_match:
                                segment_log.write({"event": "activation", "source": "wake"})
                                wake_verify_score = similarity
                                _log_transcript(transcript_log, path if save_dir else None, f"[wake] {wake_word}", similarity)
                                print(f"[{_now()}] verified — active listening, say your command")
                                # Acknowledge first, arm second -- the
                                # --active-window countdown should be
                                # time to talk, not time spent hearing
                                # EKKO finish a sentence.
                                if ui is not None:
                                    ui.set_transcript("")
                                _ack(ack_style, feedback_voice, mic_gate, vad, audio_q, preroll, ui=ui)
                                awaiting_command = True
                                awaiting_since = time.monotonic()
                                if ui is not None:
                                    ui.set_state(VoiceUIState.LISTENING)
                            else:
                                print("  voice did not match enrolled speaker — ignoring")
                                _log_transcript(transcript_log, path if save_dir else None, f"[wake rejected] {wake_word}", similarity)
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
                            segment_log.write({"event": "activation", "source": "wake"})
                            _log_transcript(transcript_log, None, f"[wake] {wake_word}", None)
                            if ui is not None:
                                ui.set_transcript("")
                            _ack(ack_style, feedback_voice, mic_gate, vad, audio_q, preroll, ui=ui)
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

                        screen_started = time.perf_counter()
                        reason, transcription = _screen_command_segment(
                            path,
                            duration,
                            min_command_ms / 1000,
                            verify_enabled and verify_commands,
                            reference,
                            speaker_model,
                            command_verify_threshold,
                            transcribe_enabled,
                            whisper_model,
                            max_no_speech_prob,
                            min_avg_logprob,
                            audio=np.concatenate(speech_buffer) if speech_buffer else None,
                            echo_phrases=echo_phrases,
                            custom_words=custom_words,
                            word_correction_threshold=word_correction_threshold,
                        )
                        segment_log.write({
                            "event": "segment", "kind": "command", "duration_s": round(duration, 3), "preroll_ms": round(lead_blocks * 32),
                            "via": "push_to_talk" if via_ptt else "voice",
                            "outcome": "accepted" if reason is None else "rejected",
                            "reason": segment_log.reason_code(reason), "detail": reason,
                            # screen = duration check + speaker check + STT + gates
                            "screen_ms": round((time.perf_counter() - screen_started) * 1000, 1),
                            "stt_ms": round(transcription.stt_ms, 1) if transcription and transcription.stt_ms is not None else None,
                            "no_speech_prob": round(transcription.no_speech_prob, 3) if transcription else None,
                            "avg_logprob": round(transcription.avg_logprob, 3) if transcription else None,
                            "text": transcription.text if transcription else None,
                            "wake_verify_score": wake_verify_score,
                        })

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
                                    # Recorded so the transcript window
                                    # shows this conversation ended.
                                    record_turn(transcription.text, None, kind="decline")
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
                                        provider=llm_provider,
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
                                            is_file_op = outcome_bundle.intent == "file_operation"
                                            wait_s = FILE_OP_FOLLOWUP_TIMEOUT_S if is_file_op else BRIEFING_FOLLOWUP_TIMEOUT_S
                                            if ui is not None:
                                                ui.set_state(VoiceUIState.PROCESSING)
                                                ui.show_response(
                                                    "Waiting for you in the terminal…"
                                                    if is_file_op
                                                    else "Preparing your morning briefing…",
                                                    timeout_s=wait_s,
                                                )
                                            # Not for file_operation: its terminal can
                                            # finish (and write the flag) while the
                                            # confirmation above is still being spoken,
                                            # and unlinking here would eat that signal.
                                            # hands_on clears any stale flag itself at
                                            # start instead.
                                            if not is_file_op:
                                                BRIEFING_NARRATION_FLAG_PATH.unlink(missing_ok=True)
                                            pending_briefing_followup_until = time.monotonic() + wait_s
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
                    via_ptt = False

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


def _ack(style, voice, gate: MicGate, vad, audio_q: queue.Queue, preroll: PreRoll, ui=None) -> None:
    """What ekko does once it has heard "Hey ekko" and is ready for the
    request: say hi (default), play a short chime, or nothing. With no
    feedback voice (--no-feedback) it never makes a sound.

    "none" plays nothing, so the mic gate never closes and never clears the
    pre-roll; clear it here, or the request would start with the tail of the
    wake phrase."""
    if voice is None or style == "none":
        preroll.clear()
        return
    if style == "speech":
        _say(voice, "generic_ack", gate, vad, audio_q, ui=ui)
        return
    # Same gating as _say: mic shut while it plays, then flush and reset.
    gate.close()
    done = 0.0
    try:
        done = play_chime()
    finally:
        gate.reopen_after(done or time.monotonic())
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
    engine,
    max_no_speech_prob: float,
    min_avg_logprob: float,
    audio: np.ndarray | None = None,
    echo_phrases: frozenset[str] = frozenset(),
    custom_words: list[str] | None = None,
    word_correction_threshold: float = 0.0,
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
    3. Content -- the transcript itself (see stt.screen_transcription): Whisper's no_speech_prob/avg_logprob, free since we
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

    transcription = engine.transcribe(audio if audio is not None else path)
    if transcription.text and custom_words:
        transcription = transcription._replace(
            text=apply_custom_words(transcription.text, custom_words, word_correction_threshold)
        )
    reason = screen_transcription(transcription, echo_phrases, max_no_speech_prob, min_avg_logprob)
    return reason, transcription


_MODEL_OFFLINE = "Model Offline"
# provider -> its configured model, for the avatar's tag. Filled on first use,
# then read from memory only; backend/routers/settings.py pushes "reload_llm"
# when the llm section is edited, which clears it (see main()'s command reader).
_planned_models: dict[str, str] = {}


def _planned_model(provider: str) -> str:
    if provider not in _planned_models:
        try:
            _planned_models[provider] = get_provider(provider).default_model
        except (ValueError, KeyError, AttributeError, OSError):
            return _MODEL_OFFLINE  # settings locked/missing: a tag must never break a turn
    return _planned_models[provider]


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
    provider: str | None = None,
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
    # Only decides whether outcomes show in the popup panel and whether the
    # follow-up session is extended -- conversation memory is the day-log
    # window (memory.context.gather()), which no timer clears.
    session_active = session_until is not None and time.monotonic() < session_until

    route_started = time.perf_counter()
    bundle = router.route(transcript)  # also appends to routing/logs/routing.jsonl
    segment_log.write({
        "event": "route", "status": bundle.status.value, "intent": bundle.intent,
        "route_ms": round((time.perf_counter() - route_started) * 1000, 1),
    })
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
        provider = provider or default_provider()  # per call: UI edits apply without a restart
        system_instruction_override = None
        if domain_router is not None:
            match = domain_router.match(transcript)
            if match is not None:
                matched_domain, _score = match
                base_prompt = base_system_instruction(provider)
                system_instruction_override = compose_system_instruction(matched_domain, base_prompt)
                print(f"  [domains] matched {matched_domain!r}")
            domain_router.record(transcript, matched_domain)

        memory_context = None
        if pending_facts is not None:
            # Read fresh each call -- same never-trust-a-stale-load
            # posture as routing/route.py's intents.yaml reload.
            memory_context = read_memory().as_prompt_block() or None

        context = gather_context()

        # Footer tag on the avatar: the configured model now, the model that
        # actually answered once the call returns (failover may differ).
        # Only turns that reach this LLM branch get one: a directly matched
        # command never calls a model, so there is nothing to show.
        if ui is not None:
            ui.set_meta(matched_domain, _planned_model(provider))

        # One Gemini call that both re-checks for a command and, if
        # empty, answers an open-ended question -- see
        # llm_fallback/README.md for why this is one call, not two.
        fallback_outcome = attempt_with_failover(
            transcript,
            provider=provider,
            system_instruction_override=system_instruction_override,
            memory_context=memory_context,
            domain=matched_domain,
            **context.as_kwargs(),
        )
        if ui is not None:
            # Empty model on error: nothing answered, so don't show the one that failed.
            ui.set_meta(matched_domain, None if fallback_outcome.error else fallback_outcome.model)
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
            record_turn(
                transcript,
                " ".join(filter(None, (fallback_outcome.answer, fallback_outcome.follow_up))),
                kind="answer",
            )
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
            if research_tabs_enabled and fallback_outcome.urls and ui is not None:
                # Permission layer: urls is the model's own signal it found
                # something worth linking. Ask on the avatar card (Yes/No,
                # RESEARCH_OFFER_TIMEOUT_S, then it just closes) instead of
                # opening Brave unasked; Yes posts /api/chat/research.
                ui.show_offer(transcript, fallback_outcome.urls, RESEARCH_OFFER_TIMEOUT_S)
                _say(voice, "research_offer", gate, vad, audio_q, ui=ui)

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
            exec_started = time.perf_counter()
            result = execute(bundle)
            segment_log.write({
                "event": "execute", "intent": bundle.intent,
                "exec_ms": round((time.perf_counter() - exec_started) * 1000, 1),
            })
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
    record_turn(
        transcript,
        override_text or (render_response(outcome_key, intent=bundle.intent, domain=matched_domain, slots=dict(bundle.slots)) if outcome_key else ""),
        kind="command" if bundle.status is RoutingStatus.MATCHED else "unrecognized",
    )
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
#
# file_operation is here for the same reason from the other direction: its
# handler opens a terminal that is *waiting for the user to type y* (see
# scripts/windows/hands_on.ps1). Re-arming the mic to ask "anything else?"
# while someone is reading an approval prompt means EKKO is listening to a
# room where the person is busy not talking to it, and anything it picks up
# lands mid-decision.
_NO_FOLLOW_UP_INTENTS = {"daily_briefing", "file_operation"}


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
        help=f"How long silence must last before a speech segment is considered over while waiting for the wake word (default: {DEFAULT_MIN_SILENCE_MS}).",
    )
    parser.add_argument(
        "--command-min-silence-ms",
        type=int,
        default=DEFAULT_COMMAND_MIN_SILENCE_MS,
        help="Same, but during active listening (after the wake word), so "
        f"pauses inside a command don't cut it off (default: {DEFAULT_COMMAND_MIN_SILENCE_MS}).",
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
        help=f"openWakeWord pretrained model name, or path to a custom .onnx "
        f"(e.g. listener/models/hey_ekko.onnx), to trigger on (default: {DEFAULT_WAKE_WORD!r}).",
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
        "(like 'hey ekko' itself) score lower even for a genuine "
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
        help=f"faster-whisper model size to load (default: {DEFAULT_WHISPER_MODEL!r}: medium on a CUDA GPU, "
        "small on CPU). Larger sizes (medium, large-v3) are more accurate but slower and use more VRAM.",
    )
    parser.add_argument(
        "--preroll-ms",
        type=int,
        default=DEFAULT_PREROLL_MS,
        help=f"Audio kept from before speech is detected, so a request keeps its first syllable (default: {DEFAULT_PREROLL_MS}).",
    )
    parser.add_argument(
        "--push-to-talk",
        action="store_true",
        help="Optional: hold the hold-to-talk hotkey, speak, let go. No wake word, no acknowledgment, no wait for a pause.",
    )
    parser.add_argument(
        "--push-to-talk-hotkey",
        default=DEFAULT_PUSH_TO_TALK_HOTKEY,
        help=f"The hold-to-talk key combination, in the keyboard package's spelling (default: {DEFAULT_PUSH_TO_TALK_HOTKEY!r}).",
    )
    parser.add_argument(
        "--ack-style",
        choices=["speech", "chime", "none"],
        default="speech",
        help="What ekko does after the wake word: speech (say hi), chime (a short sound) or none.",
    )
    parser.add_argument(
        "--stt-engine",
        choices=["auto", "whisper", "parakeet"],
        default="auto",
        help="Speech recognition engine: auto uses Whisper on a GPU and Parakeet (once downloaded) on CPU.",
    )
    parser.add_argument(
        "--parakeet-model",
        default="tdt-0.6b-v2",
        help="Which Parakeet model to use (see listener/stt_models.py).",
    )
    parser.add_argument(
        "--custom-words",
        nargs="*",
        default=list(DEFAULT_CUSTOM_WORDS),
        help="Words to spell your way in transcripts when a close match is heard.",
    )
    parser.add_argument(
        "--word-correction-threshold",
        type=float,
        default=DEFAULT_WORD_CORRECTION_THRESHOLD,
        help="How loose a custom-word match may be, 0-1 (0 = off).",
    )
    parser.add_argument(
        "--whisper-device",
        choices=["auto", "gpu", "cpu"],
        default="auto",
        help="Processor for speech recognition: auto uses the GPU when there is one, cpu never does.",
    )
    parser.add_argument(
        "--whisper-cpu-threads",
        type=int,
        default=0,
        help="CPU threads for speech recognition on CPU; 0 = half the logical cores "
        "(python -m listener.calibrate finds the fastest count).",
    )
    parser.add_argument(
        "--input-device",
        default=None,
        help="Name of the microphone to use (default: the system default).",
    )
    parser.add_argument(
        "--output-device",
        default=None,
        help="Name of the speaker to use (default: the system default).",
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
        "the wake word check, saying 'hey ekko' each time rather than "
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
        "--verify-commands",
        action="store_true",
        help="Also speaker-verify every command segment after the wake word "
        "(off by default: identity is checked once, at the wake word, and "
        "the open session is trusted after that).",
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
        "--provider",
        choices=PROVIDERS,
        default=None,
        help="Which llm_fallback/<provider>/provider.py answers a NO_MATCH "
        "transcript (default: the provider chosen in the app). "
        "claude_code needs the `claude` CLI on PATH and a subscription; "
        "ollama needs `ollama serve` running locally; groq/deepseek/claude_api/openai need "
        "their API key in .env and stop at their spend cap (set in the app). See "
        "llm_fallback/README.md.",
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
    # The app's settings (DB) become the defaults; explicit flags still win.
    parser.set_defaults(**settings_defaults())
    args = parser.parse_args()

    listen(
        threshold=args.threshold,
        min_silence_ms=args.min_silence_ms,
        command_min_silence_ms=args.command_min_silence_ms,
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
        preroll_ms=args.preroll_ms,
        push_to_talk=args.push_to_talk,
        push_to_talk_hotkey=args.push_to_talk_hotkey,
        ack_style=args.ack_style,
        stt_engine=args.stt_engine,
        parakeet_model=args.parakeet_model,
        custom_words=args.custom_words,
        word_correction_threshold=args.word_correction_threshold,
        whisper_device=args.whisper_device,
        whisper_cpu_threads=args.whisper_cpu_threads,
        input_device=args.input_device,
        output_device=args.output_device,
        transcript_log=args.transcript_log,
        feedback_enabled=not args.no_feedback,
        feedback_model=args.feedback_model,
        feedback_tail_ms=args.feedback_tail_ms,
        execute_enabled=not args.no_execute,
        routing_threshold=args.routing_threshold,
        min_command_ms=args.min_command_ms,
        command_verify_threshold=args.command_verify_threshold,
        verify_commands=args.verify_commands,
        max_no_speech_prob=args.max_no_speech_prob,
        min_avg_logprob=args.min_avg_logprob,
        llm_fallback_enabled=not args.no_llm_fallback,
        research_tabs_enabled=not args.no_research_tabs,
        domain_routing_enabled=not args.no_domain_routing,
        llm_provider=args.provider,
        memory_enabled=not args.no_memory,
        keep_captures=args.keep_captures,
        manual_wake_hotkey=None if args.no_manual_wake else args.manual_wake_hotkey,
        hard_stop_hotkey=None if args.no_hard_stop else args.hard_stop_hotkey,
        ui_enabled=not args.no_ui,
        response_session_timeout=args.response_session_timeout,
    )
