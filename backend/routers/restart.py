"""Manual restart button: launches system/restart.ps1 (stop.ps1 + start.ps1
with the popup and restart.log) detached, so the listener cycle outlives this
request. Only the listener restarts; this backend keeps running.
"""

import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

_SCRIPT = Path(__file__).resolve().parents[2] / "system" / "restart.ps1"
_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP; ponytail: Windows only

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/restart", tags=["restart"])


@router.post("", status_code=202)
def restart() -> dict:
    if not _SCRIPT.exists():
        raise HTTPException(503, f"{_SCRIPT} not found.")
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_SCRIPT)],
        creationflags=_DETACHED,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    logger.info("manual restart requested, launched %s", _SCRIPT)
    return {"started": True}
