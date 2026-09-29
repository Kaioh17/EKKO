"""EKKO_DATA_DIR/runtime.json: how the Tauri app finds a backend that is
already running (started by the logon task), so there is only ever one
backend and one listener. Holds the per-launch token, so it is written
owner-only and removed on shutdown.
"""

import json
import os
from pathlib import Path

from paths import data
from procutil import alive
from version import VERSION

PATH = data("runtime.json")


def write(port: int, token: str, path: Path = PATH) -> None:
    """`version` lets the desktop app (ekko-ui/src-tauri/src/lib.rs) tell a
    stale backend from a current one -- e.g. after an update replaces
    these files while the old sign-in-task backend is still running --
    and ask it to shut down rather than attach to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token, "pid": os.getpid(), "version": VERSION}, f)
    os.replace(tmp, path)


def read(path: Path = PATH) -> dict | None:
    """The running backend's {port, token, pid}, or None if absent or stale."""
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if alive(int(info.get("pid", 0))) else None


def remove(path: Path = PATH) -> None:
    path.unlink(missing_ok=True)
