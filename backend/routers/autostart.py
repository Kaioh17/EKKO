"""Opt-in "start ekko at sign-in" (Windows): registers or removes the
"EKKO Listener" Task Scheduler task via system/install_task.ps1, so the
backend and voice listener run headless after logon and the desktop app
attaches to them. Per-user, no admin rights. Elsewhere it reports
unsupported (use a systemd user unit or launchd agent)."""

import logging
import os
import subprocess
import sys

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from paths import ROOT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/autostart", tags=["autostart"])

TASK = "EKKO Listener"
_NO_WINDOW = 0x08000000


class AutostartIn(BaseModel):
    enabled: bool


def _run(argv: list[str]) -> int:
    flags = _NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(argv, capture_output=True, timeout=60, creationflags=flags).returncode


def _powershell(script: str, *args: str) -> list[str]:
    path = ROOT / "system" / script
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path), *args]


@router.get("")
def get_autostart() -> dict:
    if os.name != "nt":
        return {"supported": False, "enabled": False}
    return {"supported": True, "enabled": _run(["schtasks.exe", "/Query", "/TN", TASK]) == 0}


@router.put("")
def set_autostart(body: AutostartIn) -> dict:
    if os.name != "nt":
        raise HTTPException(501, "Start at sign-in is only available on Windows for now.")
    if body.enabled:
        # Frozen app: register the bundled exe; development: the script falls back to pvenv.
        args = ["-Exe", sys.executable] if getattr(sys, "frozen", False) else []
        code = _run(_powershell("install_task.ps1", *args))
    else:
        code = _run(_powershell("uninstall_task.ps1"))
    if code != 0:
        logger.error("autostart %s failed (exit %s)", "enable" if body.enabled else "disable", code)
        raise HTTPException(500, "Couldn't change the sign-in setting.")
    return {"supported": True, "enabled": body.enabled}
