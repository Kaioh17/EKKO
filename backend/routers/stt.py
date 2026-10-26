"""What speech recognition can run on here, for the Voice page's Processor
setting, and the CPU thread calibrator (listener/calibrate.py)."""

import logging
import os
import threading

from fastapi import APIRouter, HTTPException

from backend.routers.settings import load_general
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
