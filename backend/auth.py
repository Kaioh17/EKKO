"""Shared-token gate for every HTTP and WebSocket route.

The token is generated per launch by whoever starts the backend (the
Tauri app, dev.ps1/dev.sh, or the logon task) and handed over in
EKKO_API_TOKEN; it is never stored in .env or the JS bundle.

- HTTP: X-Ekko-Token header only. A query-string token would land in
  access logs and history, so ?token= is not accepted.
- WebSocket: browsers can't set headers there, so the UI offers the
  subprotocols ["ekko", <token>] and the server answers with "ekko".
- Browser-origin requests must also come from the app's own origin,
  which blocks cross-site requests from any web page.
"""

import hmac
import os

from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection

TOKEN = os.environ.get("EKKO_API_TOKEN", "")
WS_SUBPROTOCOL = "ekko"
ALLOWED_ORIGINS = ["http://localhost:1420", "http://tauri.localhost", "tauri://localhost"]
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]


def _supplied(conn: HTTPConnection) -> str:
    if conn.scope["type"] == "websocket":
        protocols = conn.scope.get("subprotocols") or []
        if len(protocols) == 2 and protocols[0] == WS_SUBPROTOCOL:
            return protocols[1]
        return ""
    return conn.headers.get("x-ekko-token", "")


def require_token(conn: HTTPConnection) -> None:
    origin = conn.headers.get("origin")
    ok = bool(TOKEN) and hmac.compare_digest(_supplied(conn), TOKEN) and (origin is None or origin in ALLOWED_ORIGINS)
    if ok:
        return
    if conn.scope["type"] == "websocket":
        raise WebSocketException(code=1008)
    raise HTTPException(403, "forbidden")
