"""What speech recognition can run on here, for the Audio page: the processor
and thread settings, the CPU thread calibrator (listener/calibrate.py), the
Parakeet model downloads, and which engine the listener actually loaded."""

import json
import logging
import os
import threading

from fastapi import APIRouter, HTTPException, Response

from backend.routers.settings import load_general
from listener import stt, stt_models, stt_report
from listener.defaults import pick_whisper, whisper_cpu_threads

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stt", tags=["stt"])

_calibrating = threading.Lock()


def _gpu_available() -> bool:
    # ponytail: a visible CUDA device, not proof its libraries load. The
    # listener makes that check at startup and falls back to CPU itself
    # (_load_whisper_model in listener/vad_listener.py).
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception as exc:
        logger.warning("GPU check failed: %s", exc)
        return False


@router.get("/hardware")
def hardware() -> dict:
    return {"gpu": _gpu_available(), "cores": os.cpu_count() or 1, "auto_threads": whisper_cpu_threads()}


@router.post("/calibrate")
def calibrate_threads() -> dict:
    """Times the CPU model at several thread counts (takes a while: one
    model load per count). Returns the pick; the UI saves it."""
    from listener.calibrate import calibrate, pick

    if not _calibrating.acquire(blocking=False):
        raise HTTPException(409, "a calibration is already running")
    try:
        size = pick_whisper(load_general().whisper_model, 0)[0]
        timings = calibrate(size)
    finally:
        _calibrating.release()
    logger.info("thread calibration for %s: %s", size, {n: round(t, 3) for n, t in timings.items()})
    return {"model": size, "threads": pick(timings), "timings_ms": {n: round(t * 1000) for n, t in timings.items()}}


@router.get("/models")
def models() -> list[dict]:
    return [stt_models.status(model_id) for model_id in stt_models.MODELS]


@router.post("/models/{model_id}/download", status_code=200)
def download_model(model_id: str, response: Response) -> dict:
    """Start fetching a Parakeet model (about 660 MB). The UI polls GET /models."""
    if model_id not in stt_models.MODELS:
        raise HTTPException(404, f"unknown model {model_id!r}")
    state = stt_models.start_download(model_id)
    if state == "running":
        raise HTTPException(409, "that model is already downloading")
    if state == "started":
        logger.info("downloading speech model %s", model_id)
        response.status_code = 202
    return {"state": state}


@router.get("/status")
def status() -> dict:
    """The engine the listener loaded (and why, if it isn't the one asked for; None
    before it has started), and how the last 50 requests went."""
    try:
        engine = json.loads(stt.RUNTIME_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        engine = None
    return {"engine": engine, "recent": stt_report.recent()}
