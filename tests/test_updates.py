"""The headless update check: backend/updates.py tells a user whose window is
closed (start-at-sign-in) that a new version exists, once per version."""

import http.server
import json
import threading

import pytest

from backend import db, updates
from paths import ROOT


@pytest.fixture(autouse=True)
def clean_state():
    db.clear_section("updates")
    yield
    db.clear_section("updates")


def test_is_newer():
    assert updates.is_newer("0.2.0", "0.1.0")
    assert updates.is_newer("v0.10.0", "0.9.0")
    assert not updates.is_newer("0.1.0", "0.1.0")
    assert not updates.is_newer("0.1.0", "0.2.0")
    for junk in ("", "latest", "1.2", "1.2.3; rm -rf", "1.2.3-beta"):
        assert not updates.is_newer(junk, "0.1.0"), junk


def test_url_matches_the_apps_updater_endpoint():
    conf = json.loads((ROOT / "ekko-ui" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["plugins"]["updater"]["endpoints"] == [updates.LATEST_URL]


def _serve(body: bytes, status: int = 200):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/latest.json"


def test_fetch_latest():
    srv, url = _serve(json.dumps({"version": "0.2.0", "platforms": {}}).encode())
    try:
        assert updates.fetch_latest(url) == "0.2.0"
    finally:
        srv.shutdown()


@pytest.mark.parametrize("body,status", [(b"not json", 200), (b"{}", 200), (b'{"version": 3}', 200), (b"{}", 404)])
def test_fetch_latest_bad_responses(body, status):
    srv, url = _serve(body, status)
    try:
        assert updates.fetch_latest(url) is None
    finally:
        srv.shutdown()


def test_fetch_latest_unreachable():
    assert updates.fetch_latest("http://127.0.0.1:1/x") is None


class Env:
    def __init__(self, latest="0.2.0", window_open=False):
        self.toasts = []
        self.latest, self.window_open = latest, window_open

    def check(self):
        return updates.check_once(lambda: self.latest, self.toasts.append, self.window_open, "0.1.0")


def test_notifies_once_per_version():
    env = Env()
    assert env.check() == "0.2.0"
    env.check()
    assert env.toasts == ["0.2.0"]
    env.latest = "0.3.0"
    env.check()
    assert env.toasts == ["0.2.0", "0.3.0"]


def test_silent_when_window_is_open():
    env = Env(window_open=True)
    env.check()
    assert env.toasts == []


def test_silent_when_up_to_date_or_unreachable():
    for env in (Env(latest="0.1.0"), Env(latest=None)):
        env.check()
        assert env.toasts == []


def test_toast_text_is_a_plain_version():
    # The version comes from the network; it must never reach a shell as anything but digits.
    assert updates.toast_command("0.2.0")
    with pytest.raises(ValueError):
        updates.toast_command("0.2.0'; calc; '")


def test_run_survives_a_failed_check(monkeypatch):
    calls = []

    def boom(*a):
        calls.append(a)
        raise RuntimeError("network down")

    class Stop(Exception):
        pass

    def stop(_seconds):
        raise Stop

    monkeypatch.setattr(updates, "check_once", boom)
    monkeypatch.setattr(updates.time, "sleep", stop)
    with pytest.raises(Stop):
        updates.run(lambda: 0, "0.1.0")
    assert len(calls) == 1
