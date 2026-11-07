import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.status_bus import status_bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["status"])

_VALID_STATES = {"idle", "listening", "processing", "speaking", "error"}


class PushStatusIn(BaseModel):
    state: str


class AvatarIn(BaseModel):
    type: Literal["transcript", "response", "clear_response", "level", "meta", "offer"]
    text: str = ""
    query: str | None = None  # "offer" only
    urls: list[str] | None = None  # "offer" only
    domain: str | None = None  # "meta" only; "" = general
    model: str | None = None  # "meta" only
    timeout_s: float | None = None
    level: float | None = Field(default=None, ge=0, le=1)  # mic level, "level" messages only


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket) -> None:
    await status_bus.connect(websocket)
    logger.info("status socket connected: %s", websocket.client)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await status_bus.disconnect(websocket)
        logger.info("status socket disconnected: %s", websocket.client)


@router.post("/api/status/push", status_code=204)
async def push_status(body: PushStatusIn) -> None:
    # ponytail: manual trigger for now -- wire listener/vad_listener.py's
    # VoiceUIState transitions to call this for real once that's needed.
    if body.state not in _VALID_STATES:
        raise HTTPException(422, f"state must be one of {sorted(_VALID_STATES)}")
    logger.info("status pushed: %s", body.state)
    await status_bus.broadcast({"type": "status", "state": body.state})


@router.post("/api/avatar", status_code=204)
async def push_avatar(body: AvatarIn) -> None:
    """The listener's transcript / spoken response, for the avatar window."""
    await status_bus.broadcast(body.model_dump(exclude_none=True))
