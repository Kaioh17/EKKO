"""The UI's Wake and Restart buttons, the listener's command long-poll,
and the desktop app's shutdown call. 503 when no listener runs under this
backend (tests, --no-listener)."""

import logging
import queue
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, Response

from backend import supervisor
from version import VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["control"])

# Set by backend/__main__.py to stop uvicorn cleanly (lifespan cleanup
# runs, so the listener is stopped too).
shutdown_hook: Callable[[], None] | None = None


def _listener() -> supervisor.Supervisor:
    if supervisor.listener is None:
        raise HTTPException(503, "The voice listener isn't running under this backend.")
    return supervisor.listener


@router.post("/wake")
def wake() -> dict:
    if not _listener().send({"cmd": "wake"}):
        raise HTTPException(503, "The voice listener is starting or restarting, try again in a moment.")
    logger.info("wake queued for listener")
    return {"sent": "wake"}


@router.post("/restart", status_code=202)
def restart() -> dict:
    _listener().restart()
    logger.info("listener restart requested")
    return {"started": True}


@router.get("/listener/next", response_model=None)
def next_command(timeout: float = Query(25.0, ge=0, le=60)) -> dict | Response:
    """Long-poll for the listener: the next queued command, or 204 after `timeout`."""
    try:
        return _listener().commands.get(timeout=timeout) if timeout else _listener().commands.get_nowait()
    except queue.Empty:
        return Response(status_code=204)


@router.post("/shutdown", status_code=202)
def shutdown() -> dict:
    if shutdown_hook is None:
        raise HTTPException(503, "shutdown is only available when started with `python -m backend`")
    logger.info("shutdown requested by the desktop app")
    shutdown_hook()
    return {"stopping": True}


@router.get("/health")
def health() -> dict:
    return {"ok": True, "version": VERSION}
