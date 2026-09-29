"""Entry point for the backend (and, in the frozen build, the listener).

    python -m backend                 # API on 127.0.0.1 + supervised listener
    python -m backend --no-listener   # API only (or EKKO_NO_LISTENER=1)
    python -m backend listener [...]  # the voice listener itself

EKKO_PORT and EKKO_API_TOKEN come from whoever starts it (Tauri, dev.ps1,
dev.sh); a missing token is generated here, and a missing port means a
free one. Either way they are published to EKKO_DATA_DIR/runtime.json so
the desktop app can attach.
"""

import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str]) -> None:
    if argv[:1] == ["listener"]:
        import runpy

        sys.argv = ["vad_listener", *argv[1:]]
        runpy.run_module("listener.vad_listener", run_name="__main__")
        return

    os.environ.setdefault("EKKO_API_TOKEN", secrets.token_hex(32))
    os.environ.setdefault("EKKO_PORT", str(_free_port()))
    if "--no-listener" not in argv and os.environ.get("EKKO_NO_LISTENER") != "1":
        os.environ["EKKO_RUN_LISTENER"] = "1"

    import uvicorn

    from backend.main import app

    # access_log off: request lines are noise here, and must never be a
    # place a credential could land.
    # Graceful-shutdown timeout: the webview's keep-alive connections would
    # otherwise hold shutdown (and the lifespan cleanup) open indefinitely.
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=int(os.environ["EKKO_PORT"]),
            access_log=False,
            log_level="info",
            timeout_graceful_shutdown=2,
        )
    )
    from backend.routers import control
    from procutil import alive

    control.shutdown_hook = lambda: setattr(server, "should_exit", True)
    parent = int(os.environ.get("EKKO_PARENT_PID") or 0)
    if parent:
        # Spawned by the desktop app: follow it down if it dies without
        # calling /api/shutdown. Polled, never a blocking stdin read (that
        # deadlocks native DLL loads on Windows, see backend/supervisor.py).
        def _watch_parent() -> None:
            while alive(parent):
                time.sleep(1)
            server.should_exit = True

        threading.Thread(target=_watch_parent, daemon=True, name="parent-watch").start()
    server.run()


if __name__ == "__main__":
    main(sys.argv[1:])
