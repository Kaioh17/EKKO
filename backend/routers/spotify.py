"""Spotify connection for the Connections page: status, sign-in, where the
token is stored, and a prompt box to test the MCP agent (backend/mcp/)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.mcp import agent, spotify_auth

router = APIRouter(prefix="/api/spotify", tags=["spotify"])


class ConfigIn(BaseModel):
    client_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9]*$")
    store: str | None = None


class PromptIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)


def _status() -> dict:
    cfg = spotify_auth.config()
    return {"connected": spotify_auth.connected(), "client_id": cfg["client_id"], "store": cfg["store"],
            "stores": list(spotify_auth.STORES), "redirect_uri": spotify_auth.REDIRECT_URI}


@router.get("")
def status() -> dict:
    return _status()


@router.put("/config")
def set_config(body: ConfigIn) -> dict:
    try:
        spotify_auth.set_config(body.client_id, body.store)
    except (ValueError, ImportError) as exc:
        raise HTTPException(400, str(exc))
    return _status()


@router.post("/connect")
def connect() -> dict:
    """Returns the URL for the UI to open in the user's browser."""
    try:
        return {"url": spotify_auth.begin()}
    except (RuntimeError, OSError) as exc:
        raise HTTPException(400, str(exc))


@router.delete("", status_code=204)
def disconnect() -> None:
    spotify_auth.clear_token()


@router.post("/prompt")
async def prompt(body: PromptIn) -> dict:
    if not spotify_auth.connected():
        raise HTTPException(400, "Spotify is not connected")
    try:
        return await agent.run(body.prompt)
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
