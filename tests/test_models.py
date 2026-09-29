import hashlib
import http.server
import threading

import pytest

import models
from conftest import DATA_DIR

BODY = b"fake model bytes"


@pytest.fixture
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(BODY)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/m.onnx"
    srv.shutdown()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(models, "state", {"phase": "idle", "current": None, "error": None})
    yield
    for f in (DATA_DIR / "models").glob("*"):
        f.unlink()


def test_download_verifies_and_installs(server, monkeypatch):
    good = models.Model("t.onnx", server, hashlib.sha256(BODY).hexdigest(), "nowhere/t.onnx")
    monkeypatch.setattr(models, "MODELS", (good,))
    assert models.missing() == [good]
    models.ensure()
    assert (DATA_DIR / "models" / "t.onnx").read_bytes() == BODY
    assert models.state["phase"] == "ready" and models.missing() == []


def test_bad_checksum_is_refused_and_leaves_nothing(server, monkeypatch):
    bad = models.Model("t.onnx", server, "0" * 64, "nowhere/t.onnx")
    monkeypatch.setattr(models, "MODELS", (bad,))
    with pytest.raises(models.ModelError, match="checksum"):
        models.ensure()
    assert models.state["phase"] == "failed"
    assert list((DATA_DIR / "models").glob("*")) == []


def test_unreachable_host_fails_cleanly(monkeypatch):
    m = models.Model("t.onnx", "http://127.0.0.1:1/x", "0" * 64, "nowhere/t.onnx")
    monkeypatch.setattr(models, "MODELS", (m,))
    with pytest.raises(models.ModelError):
        models.ensure()


def test_legacy_dev_location_is_used():
    # A dev checkout keeps the weights in the repo; those are found without a download.
    real = next(m for m in models.MODELS if m.name == "hey_ekko.onnx")
    assert models.path(real.name) in (models.ROOT / real.legacy, DATA_DIR / "models" / real.name)


def test_models_endpoint(client):
    from conftest import AUTH

    body = client.get("/api/models", headers=AUTH).json()
    assert set(body) == {"phase", "current", "error", "missing"}
