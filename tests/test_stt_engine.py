import json

import pytest

from listener import stt
from listener.stt import Transcription

ECHO = frozenset({"access denied", "command confirmed", "hey, what do you need"})


# ---- which engine ---------------------------------------------------------

@pytest.mark.parametrize(
    "setting, cuda, processor, ready, expected",
    [
        ("auto", 1, "auto", True, ("whisper", None)),  # GPU: Whisper medium
        ("auto", 1, "gpu", True, ("whisper", None)),
        ("auto", 0, "auto", True, ("parakeet", None)),  # CPU: Parakeet
        ("auto", 1, "cpu", True, ("parakeet", None)),  # the user said CPU
        ("parakeet", 1, "auto", True, ("parakeet", None)),
        ("whisper", 0, "auto", True, ("whisper", None)),
    ],
)
def test_pick_engine(setting, cuda, processor, ready, expected):
    assert stt.pick_engine(setting, cuda, processor, ready) == expected


def test_pick_engine_without_the_parakeet_model_falls_back_with_a_reason():
    for setting, cuda in (("auto", 0), ("parakeet", 0), ("parakeet", 1)):
        engine, reason = stt.pick_engine(setting, cuda, "auto", False)
        assert engine == "whisper" and "Parakeet" in reason and "download" in reason.lower()
    # auto on a GPU never wanted Parakeet, so there's nothing to explain
    assert stt.pick_engine("auto", 1, "auto", False) == ("whisper", None)


# ---- loading, with fake runtimes -------------------------------------------

def fake_whisper(info_extra=None, gpu_fails=False):
    calls = []

    def load(model, processor, threads, cpu_fallback=True):
        calls.append(cpu_fallback)
        if gpu_fails and not cpu_fallback:
            raise stt.GpuUnavailable("CUDA unavailable: no cublas")
        info = {"model": "small", "device": "cpu", "threads": 4, "compute_type": "int8", "fallback_reason": None}
        if gpu_fails:
            info["fallback_reason"] = "CUDA unavailable: no cublas"
        info.update(info_extra or {})
        return (lambda audio: Transcription("whisper text", 0.1, -0.3, 5.0)), info

    load.calls = calls
    return load


def fake_parakeet(raises=None):
    def load(model_id, threads):
        if raises:
            raise raises
        info = {"model": model_id, "device": "cpu", "threads": 4, "compute_type": "int8", "fallback_reason": None}
        return (lambda audio: Transcription("parakeet text", 0.0, 0.0, 3.0)), info

    return load


def load(tmp_path, setting="auto", cuda=0, ready=True, whisper=None, parakeet=None, processor="auto"):
    return stt.load_engine(
        setting, "auto", processor, 0, "tdt-0.6b-v2",
        cuda_devices=cuda, parakeet_ready=lambda _id: ready,
        load_whisper=whisper or fake_whisper(), load_parakeet=parakeet or fake_parakeet(),
        runtime_path=tmp_path / "stt_runtime.json",
    )


def test_auto_on_cpu_loads_parakeet(tmp_path):
    e = load(tmp_path)
    assert (e.name, e.model, e.device, e.fallback_reason) == ("parakeet", "tdt-0.6b-v2", "cpu", None)
    assert e.transcribe(b"audio").text == "parakeet text"


def test_auto_with_gpu_loads_whisper(tmp_path):
    e = load(tmp_path, cuda=1, whisper=fake_whisper({"model": "medium", "device": "cuda", "threads": None}))
    assert (e.name, e.model, e.device) == ("whisper", "medium", "cuda")


def test_parakeet_load_failure_falls_back_to_whisper_and_says_why(tmp_path):
    e = load(tmp_path, setting="parakeet", parakeet=fake_parakeet(raises=RuntimeError("onnxruntime exploded")))
    assert e.name == "whisper"
    assert "Parakeet" in e.fallback_reason and "onnxruntime exploded" in e.fallback_reason


def test_missing_model_falls_back_without_trying_to_load_it(tmp_path):
    def must_not_run(*_):
        raise AssertionError("loaded a model that isn't downloaded")

    e = load(tmp_path, setting="parakeet", ready=False, parakeet=must_not_run)
    assert e.name == "whisper" and "download" in e.fallback_reason.lower()


def test_whisper_cuda_to_cpu_fallback_reason_is_kept(tmp_path):
    e = load(tmp_path, setting="whisper", whisper=fake_whisper({"fallback_reason": "CUDA unavailable: no cublas"}))
    assert e.name == "whisper" and e.fallback_reason == "CUDA unavailable: no cublas"


def test_runtime_file_describes_what_is_running(tmp_path):
    load(tmp_path, setting="parakeet", parakeet=fake_parakeet(raises=RuntimeError("x")))
    info = json.loads((tmp_path / "stt_runtime.json").read_text(encoding="utf-8"))
    assert info["engine"] == "whisper" and info["device"] == "cpu" and info["threads"] == 4
    assert info["requested"] == "parakeet" and "Parakeet" in info["fallback_reason"]
    assert "load_ms" in info


# ---- judging a transcript ---------------------------------------------------

def screen(t, **kw):
    return stt.screen_transcription(t, ECHO, kw.get("max_nsp", 0.45), kw.get("min_lp", -0.95))


def test_screen_accepts_a_normal_command():
    assert screen(Transcription("Open Chrome.", 0.1, -0.4)) is None


def test_screen_rejects_empty_and_doubtful_whisper_output():
    assert screen(Transcription("", 1.0, -1.0)) == "no speech in it"
    assert screen(Transcription("Play.", 0.54, -0.5)).startswith("probably not speech (no_speech_prob 0.54 > 0.45")
    assert screen(Transcription("Hmm.", 0.1, -1.09)).startswith("low-confidence transcript (avg_logprob -1.09 < -0.95")


def test_screen_rejects_ekko_hearing_itself_for_either_engine():
    for t in (Transcription("Access denied.", 0.1, -0.3), Transcription("Command confirmed", 0.0, 0.0)):
        reason = stt.screen_transcription(t, ECHO, 0.45, -0.95)
        assert reason.startswith("EKKO's own voice")


def test_screen_keeps_a_command_that_merely_contains_a_phrase():
    assert screen(Transcription("Open Chrome, access denied.", 0.1, -0.3)) is None


def test_screen_parakeet_only_needs_text():
    # Parakeet reports no confidence: neutral numbers must pass Whisper's gates.
    assert screen(Transcription("Open Chrome.", 0.0, 0.0)) is None
    assert screen(Transcription("", 0.0, 0.0)) == "no speech in it"


# ---- a GPU that is there but can't be used (the packaged app has no CUDA libraries) ----

def test_auto_uses_parakeet_when_the_gpu_cant_be_used(tmp_path):
    whisper = fake_whisper(gpu_fails=True)
    e = load(tmp_path, cuda=1, whisper=whisper)
    assert e.name == "parakeet" and e.device == "cpu"
    assert "no cublas" in e.fallback_reason
    assert whisper.calls == [False]  # asked Whisper for the GPU only: no slow CPU model loaded for nothing


def test_auto_gpu_that_works_never_touches_parakeet(tmp_path):
    def must_not_run(*_):
        raise AssertionError("Parakeet loaded though the GPU works")

    e = load(tmp_path, cuda=1, whisper=fake_whisper({"model": "medium", "device": "cuda"}), parakeet=must_not_run)
    assert (e.name, e.device) == ("whisper", "cuda") and e.fallback_reason is None


def test_gpu_unusable_and_no_parakeet_model_means_whisper_on_cpu(tmp_path):
    whisper = fake_whisper(gpu_fails=True)
    e = load(tmp_path, cuda=1, ready=False, whisper=whisper)
    assert (e.name, e.device) == ("whisper", "cpu")
    assert "no cublas" in e.fallback_reason
    assert whisper.calls == [True]  # nothing else to fall back to


def test_gpu_unusable_and_parakeet_broken_still_ends_on_whisper_cpu_with_both_reasons(tmp_path):
    whisper = fake_whisper(gpu_fails=True)
    e = load(tmp_path, cuda=1, whisper=whisper, parakeet=fake_parakeet(raises=RuntimeError("bad onnx")))
    assert (e.name, e.device) == ("whisper", "cpu")
    assert "no cublas" in e.fallback_reason and "bad onnx" in e.fallback_reason
    assert whisper.calls == [False, True]


def test_asking_for_whisper_on_a_dead_gpu_stays_whisper(tmp_path):
    whisper = fake_whisper(gpu_fails=True)
    e = load(tmp_path, setting="whisper", cuda=1, whisper=whisper)
    assert e.name == "whisper" and whisper.calls == [True]  # the user chose Whisper: CPU Whisper, not Parakeet
