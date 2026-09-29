"""backend.main's lifespan: what `python -m backend` (EKKO_PORT set) does at
startup and shutdown that a plain TestClient run does not."""

from fastapi.testclient import TestClient

from backend import main, runtime, supervisor
from tests.conftest import AUTH


def test_real_server_publishes_and_removes_runtime_file(monkeypatch):
    events = []
    monkeypatch.setenv("EKKO_PORT", "51234")
    monkeypatch.delenv("EKKO_RUN_LISTENER", raising=False)
    monkeypatch.setattr(runtime, "write", lambda port, token: events.append(("write", port)))
    monkeypatch.setattr(runtime, "remove", lambda: events.append(("remove",)))
    monkeypatch.setattr(main.chat, "warm", lambda: None)
    with TestClient(main.app, base_url="http://127.0.0.1") as c:
        assert events == [("write", 51234)]
        assert c.get("/api/overview", headers=AUTH).status_code in (200, 404, 422)
    assert events == [("write", 51234), ("remove",)]
    assert supervisor.listener is None


def test_listener_is_supervised_then_stopped(monkeypatch):
    started = []

    class FakeSupervisor:
        def __init__(self, argv, cwd):
            self.argv, self.stopped = argv, False

        def start(self):
            started.append(self)

        def stop(self):
            self.stopped = True

    monkeypatch.setenv("EKKO_PORT", "51235")
    monkeypatch.setenv("EKKO_RUN_LISTENER", "1")
    monkeypatch.setattr(runtime, "write", lambda *a: None)
    monkeypatch.setattr(runtime, "remove", lambda: None)
    monkeypatch.setattr(main.chat, "warm", lambda: None)
    monkeypatch.setattr(main.supervisor, "Supervisor", FakeSupervisor)
    monkeypatch.setattr(main.models, "ensure", lambda: None)
    # the lifespan writes these straight into os.environ; setenv makes monkeypatch undo that
    monkeypatch.setenv("EKKO_SUPERVISED", "")
    monkeypatch.setenv("EKKO_BACKEND_URL", "")
    with TestClient(main.app, base_url="http://127.0.0.1"):
        listener = supervisor.listener
        assert isinstance(listener, FakeSupervisor)
        assert main.os.environ["EKKO_BACKEND_URL"] == "http://127.0.0.1:51235"
    assert listener.stopped and supervisor.listener is None
