"""Spotify MCP plumbing, no network: token store move, tool list, routes."""
import asyncio
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.mcp import spotify_auth as sa
from paths import ROOT
from tests.conftest import AUTH


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "_CONFIG", tmp_path / "spotify.json")
    monkeypatch.setattr(sa, "_TOKEN_FILE", tmp_path / "tok.json")
    # CI has no OS keyring (and dev machines shouldn't be touched): use a dict.
    import keyring
    vault: dict = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: vault.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: vault.get((s, u)))
    monkeypatch.setattr(keyring, "delete_password", lambda s, u: vault.pop((s, u), None))


def test_token_moves_between_stores():
    sa.set_config(client_id="abc")
    sa.save_token({"access_token": "a", "expires_at": 9e12, "refresh_token": "r"})
    assert sa.access_token() == "a"
    sa.set_config(store="keyring")
    assert not sa._TOKEN_FILE.exists() and sa.load_token()["refresh_token"] == "r"
    sa.set_config(store="file")
    assert sa._TOKEN_FILE.exists()
    sa.clear_token()
    assert not sa.connected()


def test_routes(client):
    assert client.get("/api/spotify", headers=AUTH).json()["connected"] is False
    assert client.post("/api/spotify/connect", headers=AUTH).status_code == 400  # no client id yet
    assert client.post("/api/spotify/prompt", json={"prompt": "hi"}, headers=AUTH).status_code == 400


def test_mcp_server_lists_tools():
    async def go():
        p = StdioServerParameters(command=sys.executable, args=["-m", "backend.mcp.spotify_server"], cwd=str(ROOT))
        async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            return {t.name for t in (await s.list_tools()).tools}
    assert {"play", "pause", "now_playing"} <= asyncio.run(go())
