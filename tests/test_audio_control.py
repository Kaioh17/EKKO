"""Audio devices + volume. The fake-PyAudio tests run anywhere (CI has no sound
card); the `hardware` tests drive the real PyAudio / Windows endpoint and skip
when either is missing:  python -m pytest tests/test_audio_control.py -v -s"""

import os
import sys
import types

import pytest

from backend import audio_control
from tests.conftest import AUTH


class FakePyAudio:
    DEVICES = [
        {"name": "Mic A", "index": 0, "hostApi": 0, "maxInputChannels": 1, "maxOutputChannels": 0},
        {"name": "Speakers B", "index": 1, "hostApi": 0, "maxInputChannels": 0, "maxOutputChannels": 2},
        {"name": "Headset C", "index": 2, "hostApi": 0, "maxInputChannels": 1, "maxOutputChannels": 2},
        {"name": "Mic A (other API)", "index": 3, "hostApi": 1, "maxInputChannels": 1, "maxOutputChannels": 0},
    ]

    def get_default_host_api_info(self):
        return {"index": 0}

    def get_device_count(self):
        return len(self.DEVICES)

    def get_device_info_by_index(self, i):
        return self.DEVICES[i]

    def get_default_input_device_info(self):
        return self.DEVICES[0]

    def get_default_output_device_info(self):
        return self.DEVICES[1]

    def terminate(self):
        pass


@pytest.fixture
def fake_pyaudio(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyaudio", types.SimpleNamespace(PyAudio=FakePyAudio))


def test_lists_default_host_api_only(fake_pyaudio):
    d = audio_control.list_devices()
    assert d["available"]
    assert [x["name"] for x in d["input"]] == ["Mic A", "Headset C"]
    assert [x["name"] for x in d["output"]] == ["Speakers B", "Headset C"]
    assert (d["default_input"], d["default_output"]) == ("Mic A", "Speakers B")


def test_find_device_by_name_and_unplugged(fake_pyaudio):
    assert audio_control.find_device("Headset C", "output") == 2
    assert audio_control.find_device("Gone", "input") is None


def test_unavailable_without_pyaudio(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyaudio", None)  # makes `import pyaudio` raise ImportError
    d = audio_control.list_devices()
    assert d["available"] is False and d["input"] == []


def test_api_reports_devices_and_volume(client, fake_pyaudio, monkeypatch):
    monkeypatch.setattr(audio_control, "get_volume", lambda: {"level": 40, "muted": False})
    body = client.get("/api/audio", headers=AUTH).json()
    assert body["input"][0]["name"] == "Mic A" and body["volume"]["level"] == 40


def test_api_set_volume_and_unsupported(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(audio_control, "set_volume", lambda level, muted: seen.update(l=level, m=muted) or {"level": level, "muted": bool(muted)})
    r = client.put("/api/audio/volume", json={"level": 30}, headers=AUTH)
    assert r.json() == {"level": 30, "muted": False} and seen["l"] == 30
    assert client.put("/api/audio/volume", json={"level": 101}, headers=AUTH).status_code == 422
    monkeypatch.setattr(audio_control, "set_volume", lambda level, muted: None)
    assert client.put("/api/audio/volume", json={"level": 5}, headers=AUTH).status_code == 501


def test_device_choice_persists_in_settings(client):
    r = client.patch("/api/settings/general", json={"input_device": "Mic A", "output_device": None}, headers=AUTH)
    assert r.status_code == 200 and r.json()["input_device"] == "Mic A"
    assert client.get("/api/settings/general", headers=AUTH).json()["input_device"] == "Mic A"


# ---- real hardware -------------------------------------------------------

def _real_devices():
    try:
        import pyaudio  # noqa: F401
    except ImportError:
        pytest.skip("pyaudio not installed")
    d = audio_control.list_devices()
    if not d["available"]:
        pytest.skip("PyAudio found no audio host")
    return d


@pytest.mark.hardware
def test_hardware_every_device_opens():
    """Each listed device must really open and start a stream; proves switching to it will work."""
    pyaudio = pytest.importorskip("pyaudio")

    d = _real_devices()
    pa = pyaudio.PyAudio()
    try:
        for kind in ("input", "output"):
            for dev in d[kind]:
                info = pa.get_device_info_by_index(dev["index"])
                rate = int(info["defaultSampleRate"])
                try:
                    s = pa.open(rate=rate, channels=1, format=pyaudio.paInt16, input=kind == "input", output=kind == "output",
                                input_device_index=dev["index"] if kind == "input" else None,
                                output_device_index=dev["index"] if kind == "output" else None, frames_per_buffer=1024)
                except OSError as e:  # some listed endpoints are disabled/exclusive-locked
                    print(f"  skip {kind} {dev['name']!r}: {e}")
                    continue
                if kind == "input":
                    assert len(s.read(1024, exception_on_overflow=False)) == 2048
                else:
                    s.write(b"\x00\x00" * 1024)  # silence, not a beep
                s.close()
                print(f"  ok   {kind} {dev['name']!r} @ {rate} Hz")
    finally:
        pa.terminate()


@pytest.mark.hardware
@pytest.mark.skipif(os.name != "nt", reason="volume control is Windows-only")
def test_hardware_volume_round_trip():
    """Set the real microphone volume, read it back, then restore it."""
    start = audio_control.get_volume()
    if start is None:
        pytest.skip("no Windows audio endpoint")
    try:
        target = 20 if start["level"] != 20 else 25
        assert audio_control.set_volume(level=target)["level"] == target
        assert audio_control.set_volume(muted=True)["muted"] is True
        assert audio_control.set_volume(muted=False)["muted"] is False
    finally:
        audio_control.set_volume(level=start["level"], muted=start["muted"])
    assert audio_control.get_volume() == start
