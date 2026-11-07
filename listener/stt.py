"""Speech to text for one command segment: which engine runs (Whisper or
Parakeet), loading it, transcribing, and judging the result.

Heavy runtimes (faster-whisper, onnx-asr) are imported inside the loaders so
the backend and the tests can import this module for free."""

import json
import time
from collections.abc import Callable
from typing import NamedTuple

import listener.stt_models as stt_models
from listener import segment_log
from listener.defaults import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_MIN_AVG_LOGPROB,
    PROMPT_ONLY_PHRASES,
    WHISPER_BEAM_SIZE,
    pick_whisper,
    whisper_cpu_threads,
)
from paths import data

RUNTIME_PATH = data("listener", "stt_runtime.json")
SAMPLE_RATE = 16000


class Transcription(NamedTuple):
    """Whisper's output plus its confidence there was speech at all.

    A sub-second clip of room decay doesn't come back as an empty string --
    Whisper hallucinates a confident "Bye." or "Thank you." from its
    training data's filler phrases, which used to send a real command's
    turn to the router as NO_MATCH. no_speech_prob and avg_logprob are the
    only signals that separate those from a genuine short command.

    Produced by transcribe(), judged by screen_transcription().
    """

    text: str
    no_speech_prob: float
    avg_logprob: float
    stt_ms: float | None = None  # wall time of the decode, for segment_log


def transcribe(model, audio) -> Transcription:
    """`audio` is a 16 kHz mono float32 array or a path to a WAV; faster-whisper takes either.

    vad_filter=True is faster-whisper's own front-end suppression of
    exactly the hallucination failure -- drops non-speech before decoding, so
    an all-decay clip usually yields no segments rather than a hallucinated
    sentence. Belt and braces with the confidence thresholds, since it's
    tuned for long-form audio and these segments are already VAD-trimmed.
    """
    started = time.perf_counter()
    segments, _info = model.transcribe(
        audio, language="en", beam_size=WHISPER_BEAM_SIZE, vad_filter=True, initial_prompt=DEFAULT_INITIAL_PROMPT
    )
    segments = list(segments)  # the decode itself happens here, lazily
    stt_ms = (time.perf_counter() - started) * 1000
    if not segments:
        # Nothing decoded -- report max confidence there was no speech so
        # callers only need to check one field.
        return Transcription("", 1.0, DEFAULT_MIN_AVG_LOGPROB, stt_ms)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    # Worst case across segments for no_speech_prob (one silent segment is
    # enough to doubt the clip), mean for avg_logprob (already a
    # per-segment average).
    return Transcription(
        text,
        max(segment.no_speech_prob for segment in segments),
        sum(segment.avg_logprob for segment in segments) / len(segments),
        stt_ms,
    )


# ---- judging a transcript ---------------------------------------------------

def normalize(text: str) -> str:
    return text.strip().strip(".!?, ").lower()


def ekko_phrases() -> frozenset[str]:
    """Everything EKKO itself says, plus the retired lines its Whisper prompt
    still carries; a transcript equal to one is never the user."""
    from feedback.speech import RESPONSES  # imports piper, so only when needed

    lines = [p for group in RESPONSES.values() for p in group] + list(PROMPT_ONLY_PHRASES)
    return frozenset(normalize(p) for p in lines)


def screen_transcription(
    t: Transcription, echo_phrases: frozenset[str], max_no_speech_prob: float, min_avg_logprob: float
) -> str | None:
    """Why this transcript isn't a command, or None. Parakeet reports no
    confidence, so it passes with neutral numbers (0, 0) and only the text
    checks apply to it."""
    if not t.text:
        return "no speech in it"
    if normalize(t.text) in echo_phrases:
        return f"EKKO's own voice (heard {t.text!r})"
    if t.no_speech_prob > max_no_speech_prob:
        return f"probably not speech (no_speech_prob {t.no_speech_prob:.2f} > {max_no_speech_prob}, heard {t.text!r})"
    if t.avg_logprob < min_avg_logprob:
        return f"low-confidence transcript (avg_logprob {t.avg_logprob:.2f} < {min_avg_logprob}, heard {t.text!r})"
    return None


# ---- choosing and loading an engine -----------------------------------------

class Engine(NamedTuple):
    name: str  # "whisper" | "parakeet"
    model: str
    device: str
    threads: int | None
    transcribe: Callable[[object], Transcription]  # array or WAV path in
    fallback_reason: str | None  # set when this isn't what was asked for


def pick_engine(setting: str, cuda_devices: int, processor: str, parakeet_ready: bool) -> tuple[str, str | None]:
    """("whisper" | "parakeet", why we aren't using what was asked, if so).
    auto: Whisper on a usable GPU (where it's fast), Parakeet otherwise."""
    gpu = cuda_devices > 0 and processor != "cpu"
    wanted = setting if setting != "auto" else ("whisper" if gpu else "parakeet")
    if wanted == "parakeet" and not parakeet_ready:
        why = "Parakeet isn't downloaded yet, so ekko is using Whisper. Download it in Settings, then Audio."
        return "whisper", None if setting == "auto" and gpu else why
    return wanted, None


class GpuUnavailable(RuntimeError):
    """The GPU was asked for and can't be used (typically the CUDA libraries
    aren't installed), and the caller said not to load a CPU model instead."""


def load_whisper_model(model_size: str, processor: str, cpu_threads: int, cpu_fallback: bool = True):
    """(transcribe function, info). `processor` and `cpu_threads` come from the settings.
    With cpu_fallback=False a GPU that won't start raises GpuUnavailable instead of
    loading the slower CPU model, for a caller with something better to try."""
    import ctranslate2
    import numpy as np
    from faster_whisper import WhisperModel

    # WhisperModel(device="cuda") alone doesn't prove CUDA works --
    # CTranslate2 loads CUDA libraries lazily on first inference, so a
    # missing libcublas only surfaces mid-transcribe. Force one cheap
    # warm-up inference here so that failure (and the CPU fallback)
    # happens at startup instead.
    fallback = None
    size, device, compute_type = pick_whisper(model_size, ctranslate2.get_cuda_device_count(), processor)
    threads = None
    model = None
    if device == "cuda":
        try:
            model = WhisperModel(size, device="cuda", compute_type=compute_type)
            segments, _info = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")
            list(segments)
            print(f"  faster-whisper: {size} on cuda ({compute_type})")
        except Exception as exc:
            print(f"  faster-whisper: CUDA unavailable ({exc}), falling back to CPU")
            fallback = f"CUDA unavailable: {exc}"
            model = None
            if not cpu_fallback:
                raise GpuUnavailable(fallback) from exc
            size, device, compute_type = pick_whisper(model_size, 0)
    if model is None:
        threads = whisper_cpu_threads(cpu_threads)
        print(f"  faster-whisper: {size} on cpu ({compute_type}, {threads} threads)")
        model = WhisperModel(size, device="cpu", compute_type=compute_type, cpu_threads=threads)
    info = {"model": size, "device": device, "threads": threads, "compute_type": compute_type, "fallback_reason": fallback}
    return (lambda audio: transcribe(model, audio)), info


def load_parakeet_model(model_id: str, cpu_threads: int):
    """(transcribe function, info) for a downloaded Parakeet model, via onnx-asr on CPU."""
    import numpy as np
    import onnx_asr
    import onnxruntime as ort

    spec = stt_models.MODELS[model_id]
    threads = whisper_cpu_threads(cpu_threads)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    model = onnx_asr.load_model(spec.onnx_name, stt_models.model_dir(model_id), quantization="int8", sess_options=options)
    model.recognize(np.zeros(SAMPLE_RATE, dtype=np.float32), sample_rate=SAMPLE_RATE)  # surfaces a broken install now
    print(f"  parakeet: {model_id} on cpu (int8, {threads} threads)")

    def run(audio) -> Transcription:
        started = time.perf_counter()
        try:
            result = model.recognize(audio) if isinstance(audio, str) else model.recognize(audio, sample_rate=SAMPLE_RATE)
            text = str(getattr(result, "text", result)).strip()
        except Exception as exc:  # e.g. a segment longer than the model's ~30 s limit
            segment_log.write({"event": "stt_error", "engine": "parakeet", "error": f"{type(exc).__name__}: {exc}"})
            text = ""
        # No confidence from this runtime: neutral numbers pass Whisper's gates.
        return Transcription(text, 0.0, 0.0, (time.perf_counter() - started) * 1000)

    info = {"model": model_id, "device": "cpu", "threads": threads, "compute_type": "int8", "fallback_reason": None}
    return run, info


def _cuda_devices() -> int:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


def load_engine(
    setting: str,
    whisper_model: str,
    processor: str,
    cpu_threads: int,
    parakeet_model: str,
    *,
    cuda_devices: int | None = None,
    parakeet_ready: Callable[[str], bool] | None = None,
    load_whisper=None,
    load_parakeet=None,
    runtime_path=None,
) -> Engine:
    """Load what the settings ask for. If that can't run, fall back to Whisper
    and record why: the listener must always come up with some engine."""
    started = time.perf_counter()
    if cuda_devices is None:
        cuda_devices = _cuda_devices()
    ready = (parakeet_ready or stt_models.is_downloaded)(parakeet_model)
    name, reason = pick_engine(setting, cuda_devices, processor, ready)
    whisper = load_whisper or load_whisper_model
    fn = info = None
    if name == "whisper" and setting == "auto" and ready and cuda_devices > 0 and processor != "cpu":
        # auto wants the GPU, but "there is a GPU" isn't "its libraries load" (the
        # installed app ships none). If it won't start, Parakeet is the fast CPU
        # choice, not a slow CPU Whisper.
        try:
            fn, info = whisper(whisper_model, processor, cpu_threads, False)
        except GpuUnavailable as exc:
            print(f"  {exc}, trying Parakeet")
            reason = f"{exc}. Using Parakeet on the processor instead."
            name = "parakeet"
    if name == "parakeet":
        try:
            fn, info = (load_parakeet or load_parakeet_model)(parakeet_model, cpu_threads)
        except Exception as exc:
            problem = f"Parakeet couldn't start ({type(exc).__name__}: {exc}), so ekko is using Whisper."
            print(f"  {problem}")
            reason = f"{reason} {problem}" if reason else problem
            name = "whisper"
    if name == "whisper" and fn is None:
        fn, info = whisper(whisper_model, processor, cpu_threads)
        if info.get("fallback_reason"):
            reason = f"{reason} {info['fallback_reason']}" if reason else info["fallback_reason"]
    engine = Engine(name, info["model"], info["device"], info["threads"], fn, reason)
    record = {
        "engine": engine.name,
        "model": engine.model,
        "device": engine.device,
        "threads": engine.threads,
        "compute_type": info["compute_type"],
        "requested": setting,
        "fallback_reason": reason,
        "load_ms": round((time.perf_counter() - started) * 1000),
    }
    segment_log.write({"event": "engine_loaded", **record})
    write_runtime(record, runtime_path or RUNTIME_PATH)
    return engine


def write_runtime(record: dict, path) -> None:
    """What the app's Settings page shows as "Running: ...". Best effort."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        pass
