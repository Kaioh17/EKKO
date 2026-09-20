"""Shared-token gate for every HTTP and WebSocket route. The token comes from
EKKO_API_TOKEN in .env; the UI sends it as X-Ekko-Token (or ?token= for
WebSocket, since browsers can't set WS headers). Browser-origin requests must
also come from an allowed origin, which blocks cross-site POSTs.
"""

import hmac
import os

from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection

from routing import host  # noqa: F401 -- import triggers its load_dotenv()

TOKEN = os.environ.get("EKKO_API_TOKEN", "")
ALLOWED_ORIGINS = ["http://localhost:1420", "http://tauri.localhost", "tauri://localhost"]
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]


def require_token(conn: HTTPConnection) -> None:
    supplied = conn.headers.get("x-ekko-token") or conn.query_params.get("token", "")
    origin = conn.headers.get("origin")
    ok = bool(TOKEN) and hmac.compare_digest(supplied, TOKEN) and (origin is None or origin in ALLOWED_ORIGINS)
    if ok:
        return
    if conn.scope["type"] == "websocket":
        raise WebSocketException(code=1008)
    raise HTTPException(403, "forbidden")
