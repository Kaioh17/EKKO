"""Spotify as an MCP server (stdio). Run alone with
`python -m backend.mcp.spotify_server`; backend/mcp/agent.py spawns it.

Needs Spotify Premium and a running Spotify app (any device); sign in via
the Connections page first (see spotify_auth.py)."""

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from backend.mcp import spotify_auth

API = "https://api.spotify.com/v1"
mcp = MCPServer("spotify")


def _h() -> dict:
    return {"Authorization": f"Bearer {spotify_auth.access_token()}"}


def _call(method: str, path: str, **kw) -> dict:
    """Errors become ToolError so the model sees the message (anything else is hidden)."""
    try:
        return _do(method, path, **kw)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise ToolError(str(exc)) from exc


def _do(method: str, path: str, **kw) -> dict:
    """One Spotify call. If nothing is playing anywhere (404 NO_ACTIVE_DEVICE),
    wake the first device Spotify knows about and retry once."""
    for attempt in (0, 1):
        r = httpx.request(method, API + path, headers=_h(), timeout=15, **kw)
        if r.status_code == 404 and attempt == 0 and path.startswith("/me/player") and path != "/me/player/devices":
            devices = httpx.get(API + "/me/player/devices", headers=_h(), timeout=15).json().get("devices", [])
            if not devices:
                raise RuntimeError("No Spotify device found. Open the Spotify app first.")
            httpx.put(API + "/me/player", json={"device_ids": [devices[0]["id"]]}, headers=_h(), timeout=15)
            continue
        if r.status_code == 403:
            raise RuntimeError("Spotify refused: playback control needs Premium.")
        r.raise_for_status()
        return r.json() if r.content else {}
    raise RuntimeError("Could not reach a Spotify device.")


def _find(query: str, kind: str) -> dict:
    items = _call("GET", "/search", params={"q": query, "type": kind, "limit": 1})[f"{kind}s"]["items"]
    if not items:
        raise ToolError(f"No {kind} found for {query!r}")
    return items[0]


@mcp.tool()
def play(query: str = "", kind: str = "track") -> str:
    """Play music. With a query, search for it and play the top hit (kind: track, artist, album or playlist). With no query, resume playback."""
    if not query:
        _call("PUT", "/me/player/play")
        return "Resumed."
    hit = _find(query, kind)
    body = {"uris": [hit["uri"]]} if kind == "track" else {"context_uri": hit["uri"]}
    _call("PUT", "/me/player/play", json=body)
    who = f" by {hit['artists'][0]['name']}" if kind in ("track", "album") else ""
    return f"Playing {hit['name']}{who}."


@mcp.tool()
def pause() -> str:
    """Pause playback."""
    _call("PUT", "/me/player/pause")
    return "Paused."


@mcp.tool()
def next_track() -> str:
    """Skip to the next track."""
    _call("POST", "/me/player/next")
    return "Skipped."


@mcp.tool()
def previous_track() -> str:
    """Go back to the previous track."""
    _call("POST", "/me/player/previous")
    return "Went back."


@mcp.tool()
def set_volume(percent: int) -> str:
    """Set Spotify's volume, 0 to 100."""
    _call("PUT", "/me/player/volume", params={"volume_percent": max(0, min(100, percent))})
    return f"Volume {percent}%."


@mcp.tool()
def shuffle(on: bool) -> str:
    """Turn shuffle on or off."""
    _call("PUT", "/me/player/shuffle", params={"state": str(on).lower()})
    return f"Shuffle {'on' if on else 'off'}."


@mcp.tool()
def add_to_queue(query: str) -> str:
    """Search for a track and add it to the queue."""
    hit = _find(query, "track")
    _call("POST", "/me/player/queue", params={"uri": hit["uri"]})
    return f"Queued {hit['name']} by {hit['artists'][0]['name']}."


@mcp.tool()
def now_playing() -> str:
    """What is playing right now, and on which device."""
    p = _call("GET", "/me/player")
    if not p.get("item"):
        return "Nothing is playing."
    state = "Playing" if p["is_playing"] else "Paused on"
    return f"{state} {p['item']['name']} by {p['item']['artists'][0]['name']} ({p['device']['name']}, volume {p['device'].get('volume_percent')}%)."


@mcp.tool()
def list_devices() -> str:
    """List the devices Spotify can play on."""
    d = _call("GET", "/me/player/devices").get("devices", [])
    return "\n".join(f"{x['name']} ({x['type']}){' [active]' if x['is_active'] else ''}" for x in d) or "No devices. Open the Spotify app."


if __name__ == "__main__":
    mcp.run()
