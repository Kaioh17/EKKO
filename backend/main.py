"""The FastAPI app. Start it with `python -m backend` (see
backend/__main__.py), which binds 127.0.0.1 only, sets the per-launch
token and supervises the voice listener."""

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

import models
from backend import auth, runtime, supervisor, updates
from backend.routers import autostart, chat, control, keys, llm, memory, models as models_router, overview, reports, routing, settings, status
from backend.status_bus import status_bus
from paths import ROOT, data
from version import VERSION

_LOG_DIR = data("backend", "logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(_LOG_DIR / "backend.log", encoding="utf-8")],
)

class _DropClientResets(logging.Filter):
    """Windows' proactor loop logs a traceback whenever a client (the
    webview) drops a connection; that's normal here, not an error."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.exc_info and isinstance(record.exc_info[1], ConnectionResetError))


logging.getLogger("asyncio").addFilter(_DropClientResets())

if not auth.TOKEN:
    raise SystemExit("EKKO_API_TOKEN is not set; start the backend with `python -m backend`.")


def listener_argv() -> list[str]:
    if getattr(sys, "frozen", False):  # PyInstaller build: same exe, subcommand
        return [sys.executable, "listener"]
    return [sys.executable, "-u", "-m", "listener.vad_listener"]


def _fetch_models_then_listen() -> None:
    try:
        models.ensure()
    except models.ModelError as exc:
        logging.getLogger(__name__).error("model download failed: %s", exc)
    if supervisor.listener is not None:  # not stopped meanwhile
        supervisor.listener.start()


def _supervise_listener(port: str | None) -> None:
    # Children inherit os.environ at each spawn, so key edits reach the
    # next listener (backend/routers/keys.py restarts it).
    os.environ.update(EKKO_SUPERVISED="1", EKKO_BACKEND_URL=f"http://127.0.0.1:{port}")
    supervisor.listener = supervisor.Supervisor(listener_argv(), cwd=str(ROOT))
    # First run downloads the models before the listener starts (it would
    # just crash-loop without them).
    threading.Thread(target=_fetch_models_then_listen, daemon=True, name="models").start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    port = os.environ.get("EKKO_PORT")
    if port:  # real server (python -m backend), not a TestClient
        runtime.write(int(port), auth.TOKEN)
        threading.Thread(target=chat.warm, daemon=True, name="chat-warmup").start()
        if getattr(sys, "frozen", False):
            threading.Thread(target=updates.run, args=(status_bus.client_count, VERSION), daemon=True, name="update-check").start()
    if os.environ.get("EKKO_RUN_LISTENER") == "1":
        _supervise_listener(port)
    try:
        yield
    finally:
        if supervisor.listener is not None:
            supervisor.listener.stop()
            supervisor.listener = None
        if port:
            runtime.remove()


app = FastAPI(title="ekko-backend", dependencies=[Depends(auth.require_token)], lifespan=lifespan)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=auth.ALLOWED_HOSTS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=auth.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

for _router in (autostart, chat, control, keys, llm, memory, models_router, overview, reports, routing, settings, status):
    app.include_router(_router.router)
