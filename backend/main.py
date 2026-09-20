import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from backend import auth

from backend.routers import chat, memory, overview, reports, restart, routing, settings, status, wake

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(_LOG_DIR / "backend.log", encoding="utf-8")],
)

if not auth.TOKEN:
    raise SystemExit("EKKO_API_TOKEN is not set in .env; refusing to start an unauthenticated API.")

app = FastAPI(title="ekko-backend", dependencies=[Depends(auth.require_token)])

# Bind uvicorn to 127.0.0.1: uvicorn backend.main:app --host 127.0.0.1 --port 8000
app.add_middleware(TrustedHostMiddleware, allowed_hosts=auth.ALLOWED_HOSTS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=auth.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(overview.router)
app.include_router(reports.router)
app.include_router(restart.router)
app.include_router(routing.router)
app.include_router(settings.router)
app.include_router(status.router)
app.include_router(wake.router)
