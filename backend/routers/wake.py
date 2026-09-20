"""Bridges the UI's wake button to listener/vad_listener.py's manual-wake
hotkey (see its module docstring). That hotkey is a global OS-level chord,
not something this process's `keyboard.add_hotkey()` owns, so synthesizing
the same keypress here reaches an already-running vad_listener.py process
exactly like a real key press would -- no new IPC between this process and
that one, and no change to vad_listener.py itself.
"""

import logging

from fastapi import APIRouter, HTTPException

from listener.vad_listener import DEFAULT_MANUAL_WAKE_HOTKEY

try:
    import keyboard
except ImportError:
    keyboard = None

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wake", tags=["wake"])


@router.post("")
def wake() -> dict:
    if keyboard is None:
        logger.error("wake requested but `keyboard` package is not installed")
        raise HTTPException(503, "`keyboard` package not installed, can't send the manual wake hotkey.")
    keyboard.send(DEFAULT_MANUAL_WAKE_HOTKEY)
    logger.info("sent manual wake hotkey %r", DEFAULT_MANUAL_WAKE_HOTKEY)
    return {"sent": DEFAULT_MANUAL_WAKE_HOTKEY}
