import dataclasses
import threading

import pytest
from conftest import AUTH

from listener import stt, stt_models


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(stt_models, "ROOT", tmp_path / "stt")
    monkeypatch.setattr(stt_models, "_jobs", {})
    return tmp_path / "stt"


def tiny(monkeypatch):
    spec = dataclasses.replace(stt_models.MODELS["tdt-0.6b-v2"], size_bytes=1000)
    monkeypatch.setitem(stt_models.MODELS, "tdt-0.6b-v2", spec)
    return spec


def writer(spec, gate=None, boom=None):
    """A stand-in for huggingface_hub.snapshot_download."""

    def snapshot(repo_id, allow_patterns, local_dir):
        assert repo_id == spec.repo and sorted(allow_patterns) == sorted(spec.files)
        if gate:
            gate.wait(5)
        if boom:
            raise boom
        for name in spec.files:
            (stt_models.Path(local_dir) / name).write_bytes(b"x" * 10)

    return snapshot


def test_catalog():
    v2, v3 = stt_models.MODELS["tdt-0.6b-v2"], stt_models.MODELS["tdt-0.6b-v3"]
    assert v2.repo == "istupakov/parakeet-tdt-0.6b-v2-onnx" and v2.onnx_name == "nemo-parakeet-tdt-0.6b-v2"
    assert v3.repo == "istupakov/parakeet-tdt-0.6b-v3-onnx"
    for m in (v2, v3):
        assert "encoder-model.int8.onnx" in m.files and "vocab.txt" in m.files
        assert not any(f.endswith(".data") or f == "encoder-model.onnx" for f in m.files)  # the 2.4 GB fp32 weights
        assert 500_000_000 < m.size_bytes < 800_000_000


def test_not_downloaded_until_every_file_exists(root, monkeypatch):
    spec = tiny(monkeypatch)
    assert not stt_models.is_downloaded("tdt-0.6b-v2")
    d = stt_models.model_dir("tdt-0.6b-v2")
    d.mkdir(parents=True)
    for name in spec.files[:-1]:
        (d / name).write_bytes(b"x")
    assert not stt_models.is_downloaded("tdt-0.6b-v2")
    (d / spec.files[-1]).write_bytes(b"x")
    assert stt_models.is_downloaded("tdt-0.6b-v2")
    (d / spec.files[0]).write_bytes(b"")  # an interrupted file
    assert not stt_models.is_downloaded("tdt-0.6b-v2")


def test_download_runs_in_the_background_and_finishes(root, monkeypatch):
    spec = tiny(monkeypatch)
    assert stt_models.start_download("tdt-0.6b-v2", snapshot=writer(spec)) == "started"
    stt_models.wait("tdt-0.6b-v2")
    assert stt_models.is_downloaded("tdt-0.6b-v2")
    s = stt_models.status("tdt-0.6b-v2")
    assert s["state"] == "done" and s["downloaded"] is True and s["progress"] == 1.0 and s["error"] is None
    assert stt_models.start_download("tdt-0.6b-v2", snapshot=writer(spec)) == "done"  # nothing to do


def test_second_start_while_running_is_refused_and_progress_follows_the_disk(root, monkeypatch):
    spec = tiny(monkeypatch)
    gate = threading.Event()
    assert stt_models.start_download("tdt-0.6b-v2", snapshot=writer(spec, gate)) == "started"
    assert stt_models.start_download("tdt-0.6b-v2", snapshot=writer(spec, gate)) == "running"
    d = stt_models.model_dir("tdt-0.6b-v2")
    d.mkdir(parents=True, exist_ok=True)
    (d / "encoder-model.int8.onnx.incomplete").write_bytes(b"x" * 250)  # a partial file counts
    s = stt_models.status("tdt-0.6b-v2")
    assert s["state"] == "running" and s["progress"] == pytest.approx(0.25)
    gate.set()
    stt_models.wait("tdt-0.6b-v2")
    assert stt_models.status("tdt-0.6b-v2")["state"] == "done"


def test_a_failed_download_reports_the_error_and_can_be_retried(root, monkeypatch):
    spec = tiny(monkeypatch)
    stt_models.start_download("tdt-0.6b-v2", snapshot=writer(spec, boom=OSError("network down")))
    stt_models.wait("tdt-0.6b-v2")
    s = stt_models.status("tdt-0.6b-v2")
    assert s["state"] == "error" and "network down" in s["error"] and not s["downloaded"]
    assert stt_models.start_download("tdt-0.6b-v2", snapshot=writer(spec)) == "started"
    stt_models.wait("tdt-0.6b-v2")
    assert stt_models.status("tdt-0.6b-v2")["state"] == "done"


def test_unknown_model_is_an_error():
    with pytest.raises(KeyError):
        stt_models.start_download("tdt-9b")


# ---- the routes -------------------------------------------------------------

def test_stt_routes_are_mounted(client):
    # backend/main.py never registered this router, so /hardware and
    # /calibrate (and the Audio page buttons that call them) were 404s.
    body = client.get("/api/stt/hardware", headers=AUTH).json()
    assert set(body) == {"gpu", "cores", "auto_threads"}


def test_models_route_lists_every_model(client, root):
    body = client.get("/api/stt/models", headers=AUTH).json()
    assert [m["id"] for m in body] == list(stt_models.MODELS)
    first = body[0]
    assert first["downloaded"] is False and first["state"] == "idle" and first["size_mb"] > 500
    assert {"id", "label", "downloaded", "state", "progress", "error", "size_mb"} <= set(first)


def test_download_route(client, root, monkeypatch):
    spec = tiny(monkeypatch)
    monkeypatch.setattr(stt_models, "_snapshot", writer(spec))
    assert client.post("/api/stt/models/nope/download", headers=AUTH).status_code == 404
    r = client.post("/api/stt/models/tdt-0.6b-v2/download", headers=AUTH)
    assert r.status_code == 202 and r.json()["state"] == "started"
    stt_models.wait("tdt-0.6b-v2")
    listed = {m["id"]: m for m in client.get("/api/stt/models", headers=AUTH).json()}
    assert listed["tdt-0.6b-v2"]["downloaded"] is True and listed["tdt-0.6b-v2"]["state"] == "done"
    assert client.post("/api/stt/models/tdt-0.6b-v2/download", headers=AUTH).json()["state"] == "done"


def test_download_route_refuses_a_second_start(client, root, monkeypatch):
    spec = tiny(monkeypatch)
    gate = threading.Event()
    monkeypatch.setattr(stt_models, "_snapshot", writer(spec, gate))
    assert client.post("/api/stt/models/tdt-0.6b-v2/download", headers=AUTH).status_code == 202
    assert client.post("/api/stt/models/tdt-0.6b-v2/download", headers=AUTH).status_code == 409
    gate.set()
    stt_models.wait("tdt-0.6b-v2")


def test_status_route_reports_what_the_listener_loaded(client, tmp_path, monkeypatch):
    monkeypatch.setattr(stt, "RUNTIME_PATH", tmp_path / "stt_runtime.json")
    assert client.get("/api/stt/status", headers=AUTH).json()["engine"] is None
    (tmp_path / "stt_runtime.json").write_text(
        '{"engine": "parakeet", "model": "tdt-0.6b-v2", "device": "cpu", "threads": 6, "fallback_reason": null}',
        encoding="utf-8",
    )
    body = client.get("/api/stt/status", headers=AUTH).json()
    assert body["engine"]["engine"] == "parakeet" and body["engine"]["threads"] == 6


def test_status_route_includes_recent_performance(client, tmp_path, monkeypatch):
    from listener import segment_log

    monkeypatch.setattr(stt, "RUNTIME_PATH", tmp_path / "stt_runtime.json")
    monkeypatch.setattr(segment_log, "LOG_PATH", tmp_path / "none.jsonl")
    empty = client.get("/api/stt/status", headers=AUTH).json()["recent"]
    assert empty["n_segments"] == 0 and empty["stt_p50_ms"] is None

    log = tmp_path / "segments.jsonl"
    log.write_text(
        '{"event": "segment", "kind": "command", "outcome": "accepted", "stt_ms": 250, "text": "open chrome"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(segment_log, "LOG_PATH", log)
    got = client.get("/api/stt/status", headers=AUTH).json()["recent"]
    assert got["n_segments"] == 1 and got["accepted"] == 1 and got["stt_p50_ms"] == 250
