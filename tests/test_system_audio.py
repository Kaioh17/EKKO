"""scripts/system_audio.py against a fake backend (no audio hardware needed)."""

import pytest

from scripts import system_audio as sa


class Fake:
    def __init__(self, out=0.6):
        self.v = {"out": out, "in": 0.3}
        self.muted = None
        self.fail = 0

    def get(self, kind):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("stale handle")
        return self.v[kind]

    def set(self, kind, v):
        self.v[kind] = v

    def mute(self, kind, muted):
        self.muted = muted

    def reset(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(sa, "_BACKEND", f)
    monkeypatch.setattr(sa, "_BACKEND_TRIED", True)
    monkeypatch.setattr(sa, "_saved", None)
    return f


def test_step_and_clamp(fake):
    assert sa.change_volume(10) == 70
    assert sa.set_volume(250) == 100
    assert sa.change_volume(10) == 100
    assert sa.set_volume(-5) == 0


def test_mic_forced_to_full(fake):
    assert sa.mic_to_max() and fake.v["in"] == 1.0


def test_duck_restore_is_exact_and_double_duck_keeps_original(fake):
    assert sa.duck() and fake.v["out"] == 0.10
    assert sa.duck()  # second wake word while ducked
    assert sa.restore() and fake.v["out"] == 0.6
    assert sa.restore()  # no-op when not ducked
    assert fake.v["out"] == 0.6


def test_stale_handle_retries_once_then_gives_up(fake):
    fake.fail = 1
    assert sa.get_volume() == 60
    fake.fail = 5
    assert sa.get_volume() is None  # never raises


def test_restore_failure_keeps_saved_for_retry(fake):
    sa.duck()
    fake.fail = 0
    orig = fake.set
    fake.set = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    assert sa.restore() is False
    fake.set = orig
    assert sa.restore() and fake.v["out"] == 0.6


def test_no_backend_is_noop(monkeypatch):
    monkeypatch.setattr(sa, "_BACKEND", None)
    monkeypatch.setattr(sa, "_BACKEND_TRIED", True)
    assert sa.get_volume() is None and not sa.mic_to_max() and not sa.duck()


def test_cli_exit_codes(fake, capsys):
    assert sa._cli("up") == 0
    assert sa._cli("60") == 0
    assert sa._cli("100") == 0
    assert sa._cli("100") == 3  # already there
    assert sa._cli("bogus") == 1
