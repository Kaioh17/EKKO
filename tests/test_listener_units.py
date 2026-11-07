"""The listener's own helpers. Importing vad_listener loads torch and the VAD
(about 15 s), so everything that can be checked there is checked in this one
module."""

import queue
import time
from types import SimpleNamespace

import numpy as np
import pytest

from listener.segmenter import PreRoll
from listener.stt import Transcription

vl = pytest.importorskip("listener.vad_listener")


class FakeVad:
    def __init__(self):
        self.resets = 0

    def reset_states(self):
        self.resets += 1


def loaded_preroll():
    p = PreRoll(450)
    for i in range(5):
        p.add(f"wake-phrase-{i}", epoch=vl.MicGate(0.0).epoch)
    return p


# ---- the wake acknowledgement ------------------------------------------------

def test_speech_ack_says_the_generic_line(monkeypatch):
    said = []
    monkeypatch.setattr(vl, "_say", lambda voice, key, *a, **k: said.append((voice, key)))
    vl._ack("speech", "VOICE", vl.MicGate(0.3), FakeVad(), queue.Queue(), loaded_preroll())
    assert said == [("VOICE", "generic_ack")]


def test_chime_ack_plays_gated_then_drains_and_resets(monkeypatch):
    gate, vad, q = vl.MicGate(0.0), FakeVad(), queue.Queue()
    q.put("stale block")
    seen = {}

    def fake_chime():
        seen["gate_open_during"] = gate.is_open()
        return time.monotonic()

    monkeypatch.setattr(vl, "play_chime", fake_chime)
    monkeypatch.setattr(vl, "_say", lambda *a, **k: pytest.fail("a chime must not speak"))
    before = gate.epoch
    vl._ack("chime", "VOICE", gate, vad, q, loaded_preroll())
    assert seen["gate_open_during"] is False  # the mic is shut while it plays
    assert gate.epoch == before + 1  # which also invalidates the pre-roll
    assert gate.is_open()  # and reopened afterwards (tail is 0 here)
    assert q.empty() and vad.resets == 1


def test_chime_that_fails_to_play_still_reopens_the_mic(monkeypatch):
    gate = vl.MicGate(0.0)
    monkeypatch.setattr(vl, "play_chime", lambda: 0.0)  # play_chime's "failed" value
    vl._ack("chime", "VOICE", gate, FakeVad(), queue.Queue(), loaded_preroll())
    assert gate.is_open()


def test_silent_ack_makes_no_sound_but_drops_the_wake_phrase_lead(monkeypatch):
    monkeypatch.setattr(vl, "_say", lambda *a, **k: pytest.fail("silent means silent"))
    monkeypatch.setattr(vl, "play_chime", lambda: pytest.fail("silent means silent"))
    p = loaded_preroll()
    vl._ack("none", "VOICE", vl.MicGate(0.3), FakeVad(), queue.Queue(), p)
    # nothing played, so the gate never cleared the pre-roll; without this the
    # command would start with the tail of "Hey ekko"
    assert p.take() == ([], 0)


def test_no_feedback_voice_means_no_ack_of_any_kind(monkeypatch):
    monkeypatch.setattr(vl, "_say", lambda *a, **k: pytest.fail("feedback is off"))
    monkeypatch.setattr(vl, "play_chime", lambda: pytest.fail("feedback is off"))
    for style in ("speech", "chime", "none"):
        vl._ack(style, None, vl.MicGate(0.3), FakeVad(), queue.Queue(), loaded_preroll())


# ---- judging a command segment ----------------------------------------------

def screen(engine, **kw):
    args = dict(
        path="clip.wav", duration=2.0, min_command_seconds=0.3, verify_enabled=False, reference=None,
        speaker_model=None, command_verify_threshold=0.25, transcribe_enabled=True, engine=engine,
        max_no_speech_prob=0.45, min_avg_logprob=-0.95, audio=np.zeros(16000, np.float32),
        echo_phrases=frozenset({"access denied"}), custom_words=["Ekko", "Chrome"], word_correction_threshold=0.18,
    )
    args.update(kw)
    return vl._screen_command_segment(**args)


def engine_saying(text, nsp=0.1, lp=-0.3):
    return SimpleNamespace(transcribe=lambda audio: Transcription(text, nsp, lp, 5.0))


def test_a_good_command_passes_and_custom_words_are_applied():
    reason, t = screen(engine_saying("hey echo open chrom"))
    assert reason is None and t.text == "hey Ekko open Chrome"


def test_ekkos_own_voice_is_rejected():
    reason, t = screen(engine_saying("Access denied."))
    assert reason.startswith("EKKO's own voice") and t.text == "Access denied."


def test_too_short_and_transcription_off():
    assert screen(engine_saying("hi"), duration=0.1)[0].startswith("only 0.10s long")
    assert screen(engine_saying("hi"), transcribe_enabled=False) == (None, None)


def test_the_in_memory_audio_is_what_gets_transcribed():
    got = []
    engine = SimpleNamespace(transcribe=lambda audio: got.append(audio) or Transcription("ok", 0.0, 0.0, 1.0))
    audio = np.ones(100, np.float32)
    screen(engine, audio=audio)
    assert got[0] is audio
    screen(engine, audio=None)
    assert got[1] == "clip.wav"  # falls back to the saved file
