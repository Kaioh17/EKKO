from types import SimpleNamespace

import pytest
from conftest import AUTH

from listener import audio_setup

# What sounddevice.query_devices() returns, trimmed to the fields used.
DEVICES = [
    {"name": "Speakers (Realtek)", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Microphone (Realtek)", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 0},
    {"name": "Headset (USB)", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 2},
    {"name": "Microphone (Realtek)", "hostapi": 1, "max_input_channels": 2, "max_output_channels": 0},  # other host API
]


def test_settings_defaults_carry_every_audio_setting(client):
    client.patch(
        "/api/settings/general",
        headers=AUTH,
        json={
            "input_device": "Headset (USB)",
            "output_device": "Speakers (Realtek)",
            "whisper_device": "cpu",
            "whisper_cpu_threads": 6,
            "max_no_speech_prob": 0.6,
            "min_avg_logprob": -1.2,
            "min_command_ms": 450,
            "preroll_ms": 300,
            "stt_engine": "parakeet",
            "parakeet_model": "tdt-0.6b-v3",
            "custom_words": ["Ekko", "Zed"],
            "word_correction_threshold": 0.3,
            "push_to_talk": True,
            "push_to_talk_hotkey": "Ctrl+Alt+T",
            "ack_style": "chime",
        },
    )
    d = audio_setup.settings_defaults()
    assert d["input_device"] == "Headset (USB)"
    assert d["output_device"] == "Speakers (Realtek)"
    assert d["whisper_device"] == "cpu"
    assert d["whisper_cpu_threads"] == 6
    assert d["max_no_speech_prob"] == 0.6
    assert d["min_avg_logprob"] == -1.2
    assert d["min_command_ms"] == 450
    assert d["preroll_ms"] == 300
    assert d["stt_engine"] == "parakeet" and d["parakeet_model"] == "tdt-0.6b-v3"
    assert d["custom_words"] == ["Ekko", "Zed"] and d["word_correction_threshold"] == 0.3
    assert d["push_to_talk"] is True and d["push_to_talk_hotkey"] == "ctrl+alt+t"  # saved normalized
    assert d["ack_style"] == "chime"


def test_settings_defaults_fall_back_to_code_defaults(client):
    from listener.defaults import (
        DEFAULT_CUSTOM_WORDS,
        DEFAULT_MAX_NO_SPEECH_PROB,
        DEFAULT_MIN_AVG_LOGPROB,
        DEFAULT_MIN_COMMAND_MS,
        DEFAULT_WORD_CORRECTION_THRESHOLD,
    )

    d = audio_setup.settings_defaults()
    assert d["input_device"] is None and d["output_device"] is None
    assert d["whisper_device"] == "auto" and d["whisper_cpu_threads"] == 0
    assert d["max_no_speech_prob"] == DEFAULT_MAX_NO_SPEECH_PROB
    assert d["min_avg_logprob"] == DEFAULT_MIN_AVG_LOGPROB
    assert d["min_command_ms"] == DEFAULT_MIN_COMMAND_MS
    assert d["preroll_ms"] == 450
    assert d["stt_engine"] == "auto" and d["parakeet_model"] == "tdt-0.6b-v2"
    assert d["custom_words"] == list(DEFAULT_CUSTOM_WORDS)
    assert d["word_correction_threshold"] == DEFAULT_WORD_CORRECTION_THRESHOLD
    assert d["push_to_talk"] is False and d["push_to_talk_hotkey"] == "ctrl+alt+space"
    assert d["ack_style"] == "speech"


def test_new_general_fields_validate_and_round_trip(client):
    for bad in ({"max_no_speech_prob": 1.5}, {"min_avg_logprob": 0.5}, {"min_command_ms": -1}, {"preroll_ms": -1}, {"preroll_ms": 5000},
                {"stt_engine": "vosk"}, {"parakeet_model": "tdt-9b"}, {"word_correction_threshold": 2},
                {"ack_style": "bell"}, {"push_to_talk_hotkey": "w"}, {"push_to_talk_hotkey": "ctrl+alt+w"}):
        assert client.patch("/api/settings/general", headers=AUTH, json=bad).status_code == 422, bad
    ok = {"max_no_speech_prob": 0.3, "min_avg_logprob": -0.8, "min_command_ms": 200}
    assert client.patch("/api/settings/general", headers=AUTH, json=ok).status_code == 200
    body = client.get("/api/settings/general", headers=AUTH).json()
    assert {k: body[k] for k in ok} == ok


def test_resolve_device_matches_by_name_within_the_right_kind():
    assert audio_setup.resolve_device("Microphone (Realtek)", "input", DEVICES, hostapi=0) == (1, None)
    assert audio_setup.resolve_device("Speakers (Realtek)", "output", DEVICES, hostapi=0) == (0, None)
    # a headset is both
    assert audio_setup.resolve_device("Headset (USB)", "input", DEVICES, hostapi=0) == (2, None)
    assert audio_setup.resolve_device("Headset (USB)", "output", DEVICES, hostapi=0) == (2, None)


def test_resolve_device_ignores_other_host_apis():
    # the same physical mic listed under another Windows host API
    assert audio_setup.resolve_device("Microphone (Realtek)", "input", DEVICES, hostapi=1) == (3, None)


def test_resolve_device_unplugged_or_wrong_kind_falls_back_with_a_reason():
    idx, reason = audio_setup.resolve_device("Gone Mic", "input", DEVICES, hostapi=0)
    assert idx is None and "Gone Mic" in reason
    # a speaker is not a microphone
    idx, reason = audio_setup.resolve_device("Speakers (Realtek)", "input", DEVICES, hostapi=0)
    assert idx is None and reason


def test_resolve_device_none_means_system_default_without_a_reason():
    assert audio_setup.resolve_device(None, "input", DEVICES, hostapi=0) == (None, None)
    assert audio_setup.resolve_device("", "output", DEVICES, hostapi=0) == (None, None)


class FakeSd:
    """The slice of sounddevice's API apply_devices uses, shaped like the real one."""

    def __init__(self):
        self.default = SimpleNamespace(device=[0, 0], hostapi=0)

    def query_devices(self):
        return DEVICES


def test_apply_devices_sets_only_what_resolved_and_reports_the_rest():
    sd = FakeSd()
    notes = audio_setup.apply_devices("Headset (USB)", "Gone Speakers", sd=sd)
    assert sd.default.device[0] == 2  # input switched
    assert sd.default.device[1] == 0  # output unchanged: unplugged, keeps the system default
    assert len(notes) == 1 and "Gone Speakers" in notes[0]


def test_apply_devices_with_nothing_saved_changes_nothing():
    sd = FakeSd()
    assert audio_setup.apply_devices(None, None, sd=sd) == []
    assert list(sd.default.device) == [0, 0]


def test_apply_devices_against_the_real_sounddevice():
    # The fake above had `default_hostapi`, which real sounddevice doesn't
    # have (it's `default.hostapi`): the listener crashed at startup. This
    # runs the same code on the real module.
    import sounddevice as sd

    if not hasattr(sd, "query_devices") or type(sd).__name__ == "MagicMock":
        pytest.skip("no sounddevice in this environment")
    try:
        devices = list(sd.query_devices())
    except Exception:
        pytest.skip("no audio devices on this machine")
    mic = next((d["name"] for d in devices if d["max_input_channels"] > 0 and d["hostapi"] == sd.default.hostapi), None)
    if mic is None:
        pytest.skip("no microphone on the default host API")
    before = tuple(sd.default.device)
    try:
        assert audio_setup.apply_devices(mic, None) == []
        assert sd.default.device[0] >= 0
        assert audio_setup.apply_devices("No Such Device", None) != []
    finally:
        sd.default.device = before
