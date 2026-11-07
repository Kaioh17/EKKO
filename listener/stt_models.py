"""Parakeet speech models: what exists, where they live, and downloading one
from the UI. Light on purpose (no torch, no onnx): the backend imports it.

The files are the int8 ONNX export from istupakov/parakeet-tdt-0.6b-v*-onnx
that onnx-asr loads (https://github.com/istupakov/onnx-asr). The repos also
hold a 2.4 GB fp32 encoder, which is deliberately not fetched.
"""

import threading
from dataclasses import dataclass
from pathlib import Path

from paths import data

ROOT = data("models", "stt")

_FILES = (
    "config.json",
    "vocab.txt",
    "nemo128.onnx",
    "encoder-model.int8.onnx",
    "decoder_joint-model.int8.onnx",
)


@dataclass(frozen=True)
class ParakeetModel:
    id: str
    label: str
    repo: str  # Hugging Face repo
    onnx_name: str  # the name onnx-asr knows it by
    size_bytes: int  # of `files`, for the progress bar
    files: tuple[str, ...] = _FILES


MODELS: dict[str, ParakeetModel] = {
    "tdt-0.6b-v2": ParakeetModel(
        "tdt-0.6b-v2", "Parakeet v2 · English", "istupakov/parakeet-tdt-0.6b-v2-onnx",
        "nemo-parakeet-tdt-0.6b-v2", 661_000_000,
    ),
    "tdt-0.6b-v3": ParakeetModel(
        "tdt-0.6b-v3", "Parakeet v3 · 25 European languages", "istupakov/parakeet-tdt-0.6b-v3-onnx",
        "nemo-parakeet-tdt-0.6b-v3", 670_000_000,
    ),
}


def _snapshot(repo_id, allow_patterns, local_dir):  # replaced in tests
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo_id, allow_patterns=list(allow_patterns), local_dir=str(local_dir))


def model_dir(model_id: str) -> Path:
    return ROOT / model_id


def is_downloaded(model_id: str) -> bool:
    d = model_dir(model_id)
    # An interrupted download can leave an empty file, so size counts too.
    return all((d / f).is_file() and (d / f).stat().st_size > 0 for f in MODELS[model_id].files)


def _bytes_on_disk(model_id: str) -> int:
    # Includes huggingface_hub's in-progress files, which sit under .cache/.
    d = model_dir(model_id)
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0


# model_id -> {"state": "running"|"done"|"error", "error": str|None, "thread": Thread}
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def start_download(model_id: str, snapshot=None) -> str:
    """"started", "running" (already going) or "done" (already here). KeyError for an unknown id."""
    spec = MODELS[model_id]
    with _lock:
        if _jobs.get(model_id, {}).get("state") == "running":
            return "running"
        if is_downloaded(model_id):
            return "done"
        job = {"state": "running", "error": None}
        _jobs[model_id] = job

    def run():
        try:
            model_dir(model_id).mkdir(parents=True, exist_ok=True)
            (snapshot or _snapshot)(spec.repo, spec.files, model_dir(model_id))
            job["state"] = "done" if is_downloaded(model_id) else "error"
            job["error"] = None if job["state"] == "done" else "the download finished but files are missing"
        except Exception as exc:  # network, disk full, a missing package: shown in the UI
            job["state"], job["error"] = "error", f"{type(exc).__name__}: {exc}"

    job["thread"] = threading.Thread(target=run, daemon=True, name=f"ekko-stt-download-{model_id}")
    job["thread"].start()
    return "started"


def wait(model_id: str, timeout: float = 10) -> None:
    thread = _jobs.get(model_id, {}).get("thread")
    if thread:
        thread.join(timeout)


def status(model_id: str) -> dict:
    spec = MODELS[model_id]
    job = _jobs.get(model_id, {})
    downloaded = is_downloaded(model_id)
    state = job.get("state", "done" if downloaded else "idle")
    if downloaded and state != "running":
        state = "done"
    progress = 1.0 if downloaded else min(0.99, _bytes_on_disk(model_id) / spec.size_bytes)
    return {
        "id": model_id,
        "label": spec.label,
        "size_mb": round(spec.size_bytes / 1_000_000),
        "downloaded": downloaded,
        "state": state,
        "progress": round(progress, 3),
        "error": job.get("error") if state == "error" else None,
    }
