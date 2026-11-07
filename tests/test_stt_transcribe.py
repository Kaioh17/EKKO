from types import SimpleNamespace

import numpy as np

from listener.defaults import DEFAULT_INITIAL_PROMPT, DEFAULT_MIN_AVG_LOGPROB, WHISPER_BEAM_SIZE
from listener.stt import Transcription, transcribe


class FakeModel:
    def __init__(self, segments):
        self.segments = segments
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter(self.segments), SimpleNamespace()


def seg(text, nsp, lp):
    return SimpleNamespace(text=f" {text} ", no_speech_prob=nsp, avg_logprob=lp)


def test_accepts_an_in_memory_array_and_passes_it_through():
    model = FakeModel([seg("Open Chrome.", 0.1, -0.4)])
    audio = np.zeros(16000, dtype=np.float32)
    t = transcribe(model, audio)
    assert model.calls[0][0] is audio  # no file round trip
    kwargs = model.calls[0][1]
    assert kwargs["language"] == "en" and kwargs["beam_size"] == WHISPER_BEAM_SIZE
    assert kwargs["vad_filter"] is True and kwargs["initial_prompt"] == DEFAULT_INITIAL_PROMPT
    assert t.text == "Open Chrome." and t.stt_ms is not None and t.stt_ms >= 0


def test_still_accepts_a_wav_path():
    model = FakeModel([seg("Hi", 0.0, -0.1)])
    transcribe(model, "clip.wav")
    assert model.calls[0][0] == "clip.wav"


def test_worst_no_speech_and_mean_logprob_across_segments():
    t = transcribe(FakeModel([seg("a", 0.1, -0.2), seg("b", 0.6, -0.6)]), "x.wav")
    assert t.text == "a b"
    assert t.no_speech_prob == 0.6
    assert abs(t.avg_logprob - (-0.4)) < 1e-9


def test_no_segments_reports_certain_silence():
    t = transcribe(FakeModel([]), "x.wav")
    assert t.text == "" and t.no_speech_prob == 1.0 and t.avg_logprob == DEFAULT_MIN_AVG_LOGPROB


def test_transcription_stt_ms_is_optional():
    assert Transcription("x", 0.0, -0.1).stt_ms is None
