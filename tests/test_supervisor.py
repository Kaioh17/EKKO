import sys
import time

from backend.supervisor import Supervisor

# Lives briefly, then crashes, so the supervisor has to restart it.
CHILD = "import time, sys; time.sleep(0.5); sys.exit(3)"


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_send_restart_stop():
    sup = Supervisor([sys.executable, "-c", CHILD], backoff_s=0.1)
    assert not sup.send({"cmd": "wake"})  # not running yet
    sup.start()
    try:
        assert wait_for(lambda: sup.running)
        first = sup.pid
        assert sup.send({"cmd": "wake"})
        assert sup.commands.get_nowait() == {"cmd": "wake"}
        assert wait_for(lambda: sup.pid not in (None, first))  # crashed and came back
        assert sup.restarts >= 1
    finally:
        sup.stop()
    assert not sup.running


def test_listener_output_reaches_logger(caplog, tmp_path, monkeypatch):
    from backend import supervisor

    monkeypatch.setattr(supervisor, "LOG_PATH", tmp_path / "listener.log")
    monkeypatch.setattr(supervisor, "_open_log", lambda: open(tmp_path / "listener.log", "ab"))
    child = "print('  transcript: hi'); print('  exit 4  [error]  play_media: nope'); import time; time.sleep(30)"
    sup = Supervisor([sys.executable, "-u", "-c", child])
    with caplog.at_level("INFO", logger="ekko.listener"):
        sup.start()
        try:
            assert wait_for(lambda: len([r for r in caplog.records if r.name == "ekko.listener"]) >= 2)
        finally:
            sup.stop()
    levels = {r.getMessage().strip(): r.levelname for r in caplog.records if r.name == "ekko.listener"}
    assert levels == {"transcript: hi": "INFO", "exit 4  [error]  play_media: nope": "WARNING"}
    assert b"play_media: nope" in (tmp_path / "listener.log").read_bytes()


def test_listener_long_poll(client):
    from backend import supervisor
    from conftest import AUTH

    assert client.get("/api/listener/next?timeout=0", headers=AUTH).status_code == 503
    sup = Supervisor([sys.executable, "-c", "import time; time.sleep(30)"])
    supervisor.listener = sup
    sup.start()
    try:
        assert wait_for(lambda: sup.running)
        assert client.post("/api/wake", headers=AUTH).status_code == 200
        assert client.get("/api/listener/next?timeout=1", headers=AUTH).json() == {"cmd": "wake"}
        assert client.get("/api/listener/next?timeout=0", headers=AUTH).status_code == 204
    finally:
        sup.stop()
        supervisor.listener = None


def test_shutdown_endpoint(client):
    from backend.routers import control
    from conftest import AUTH

    assert client.post("/api/shutdown", headers=AUTH).status_code == 503  # not started via python -m backend
    hit = []
    control.shutdown_hook = lambda: hit.append(1)
    try:
        assert client.post("/api/shutdown", headers=AUTH).status_code == 202
        assert hit == [1]
    finally:
        control.shutdown_hook = None
