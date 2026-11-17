"""routing/progress.py end to end, no network: a real subprocess's STEP: lines
reach the sink and never reach stdout; with no sink nothing is asked for; the
avatar endpoint rejects a malformed step; the Spotify device wake retries on 404."""
import sys

import httpx
import pytest
from pydantic import ValidationError

from backend.mcp import spotify_server
from backend.routers.status import AvatarIn
from routing import execute, progress

HANDLER = (
    "import sys\n"
    "sys.path.insert(0, r'{root}')\n"
    "from routing import progress\n"
    "with progress.track('x', 'Step X') as s:\n"
    "    s.warn('no')\n"
    "print('hello'); print('SPEAK: hi'); sys.exit(4)\n"
)


def _handler():
    from paths import ROOT

    return [sys.executable, "-c", HANDLER.format(root=ROOT)]


def test_steps_stream_to_sink_and_are_stripped_from_stdout():
    got: list[dict] = []
    code, out, _ = execute._run(_handler(), 20, got.append)
    assert code == 4 and out == "hello\nSPEAK: hi\n"
    assert [(e["id"], e["state"], e["detail"]) for e in got] == [("x", "running", ""), ("x", "warn", "no")]


def test_without_a_sink_the_handler_is_not_asked_for_steps():
    code, out, _ = execute._run(_handler(), 20, None)
    assert code == 4 and out == "hello\nSPEAK: hi\n"


def test_avatar_rejects_an_unknown_step_state():
    ok = {"id": "a", "label": "A", "state": "running"}
    assert AvatarIn(type="step", step=ok).step.state == "running"
    with pytest.raises(ValidationError):
        AvatarIn(type="step", step={**ok, "state": "bogus"})


def test_spotify_retries_play_against_the_woken_device(monkeypatch):
    calls: list[dict] = []
    answers = iter([404, 404, 204])  # first try, then one not-ready retry, then ok

    def request(method, url, **kw):
        calls.append(kw.get("params") or {})
        return httpx.Response(next(answers), request=httpx.Request(method, url))

    monkeypatch.setattr(spotify_server.httpx, "request", request)
    monkeypatch.setattr(spotify_server, "_wake_device", lambda: "dev1")
    monkeypatch.setattr(spotify_server, "_h", lambda: {})
    monkeypatch.setattr(spotify_server.time, "sleep", lambda s: None)
    assert spotify_server._do("PUT", "/me/player/play") == {}
    assert calls == [{}, {"device_id": "dev1"}, {"device_id": "dev1"}]


def test_progress_self_check():
    got: list[dict] = []
    token = progress.sink.set(got.append)
    try:
        with pytest.raises(ValueError):
            with progress.track("c", "C"):
                raise ValueError("boom")
    finally:
        progress.sink.reset(token)
    assert [e["state"] for e in got] == ["running", "failed"]
