"""Spotify OAuth (Authorization Code + PKCE) and where its tokens live.

PKCE means no client secret: only the app's Client ID (public) is needed.
The refresh token is the real secret; the user picks where it is kept:
  "file"     DATA_DIR/backend/spotify_token.json  (owner-only on POSIX)
  "keyring"  the OS keychain (Windows Credential Manager, macOS Keychain, ...)
Client ID + that choice live in DATA_DIR/backend/spotify.json (not secret).
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from paths import data

REDIRECT_URI = "http://127.0.0.1:8888/callback"  # register this exactly in the Spotify dashboard
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
STORES = ("file", "keyring")

_CONFIG = data("backend", "spotify.json")
_TOKEN_FILE = data("backend", "spotify_token.json")
_KEYRING = ("ekko", "spotify")


def _write_private(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def config() -> dict:
    cfg = {"client_id": "", "store": "file"}
    if _CONFIG.is_file():
        cfg.update(json.loads(_CONFIG.read_text(encoding="utf-8")))
    return cfg


def set_config(client_id: str | None = None, store: str | None = None) -> dict:
    """Changing the store moves an existing token to the new place."""
    cfg, tok = config(), load_token()
    if client_id is not None:
        cfg["client_id"] = client_id.strip()
    if store is not None:
        if store not in STORES:
            raise ValueError(f"store must be one of {STORES}")
        if store == "keyring":
            import keyring  # noqa: F401  # fail now, not at connect time
        if store != cfg["store"]:
            clear_token()
            cfg["store"] = store
            _write_private(_CONFIG, json.dumps(cfg))
            if tok:
                save_token(tok)
    _write_private(_CONFIG, json.dumps(cfg))
    return cfg


def load_token() -> dict | None:
    if config()["store"] == "keyring":
        import keyring
        raw = keyring.get_password(*_KEYRING)
        return json.loads(raw) if raw else None
    return json.loads(_TOKEN_FILE.read_text(encoding="utf-8")) if _TOKEN_FILE.is_file() else None


def save_token(tok: dict) -> None:
    if config()["store"] == "keyring":
        import keyring
        keyring.set_password(*_KEYRING, json.dumps(tok))
    else:
        _write_private(_TOKEN_FILE, json.dumps(tok))


def clear_token() -> None:
    _TOKEN_FILE.unlink(missing_ok=True)
    try:
        import keyring
        keyring.delete_password(*_KEYRING)
    except Exception:  # not installed, or nothing stored
        pass


def _token_request(**form) -> dict:
    r = httpx.post(TOKEN_URL, data={"client_id": config()["client_id"], **form}, timeout=15)
    r.raise_for_status()
    body = r.json()
    return {"access_token": body["access_token"], "expires_at": time.time() + body["expires_in"] - 30,
            "refresh_token": body.get("refresh_token", form.get("refresh_token"))}


def access_token() -> str:
    """A valid access token, refreshed (and re-saved) when stale."""
    tok = load_token()
    if not tok:
        raise RuntimeError("Spotify is not connected")
    if tok["expires_at"] < time.time():
        tok = _token_request(grant_type="refresh_token", refresh_token=tok["refresh_token"])
        save_token(tok)
    return tok["access_token"]


def connected() -> bool:
    try:
        return load_token() is not None
    except Exception:
        return False


# ── the one-off sign-in ────────────────────────────────────────────
_pending: dict = {}  # state -> verifier; one sign-in at a time


def begin() -> str:
    """Start a loopback listener on :8888 and return the URL to open in the browser."""
    cid = config()["client_id"]
    if not cid:
        raise RuntimeError("Set the Spotify Client ID first")
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    _pending.clear()
    _pending[state] = verifier
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            msg = f"Spotify sign-in failed. {q.get('error', [''])[0]}"
            if q.get("state", [""])[0] in _pending and "code" in q:
                try:
                    save_token(_token_request(grant_type="authorization_code", code=q["code"][0],
                                              redirect_uri=REDIRECT_URI, code_verifier=_pending.pop(q["state"][0])))
                    msg = "Spotify connected. You can close this tab."
                except Exception as exc:  # shown in the page; the token never is
                    msg = f"Spotify sign-in failed. {exc}"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode())
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 8888), Handler)  # raises if :8888 is taken
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Timer(300, server.shutdown).start()  # give up after 5 min
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT_URI, "scope": SCOPES,
        "state": state, "code_challenge_method": "S256", "code_challenge": challenge})
