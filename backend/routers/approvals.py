"""Permission requests from EKKO (e.g. the file gateway's "Approve?") shown
as a popup in ekko-ui instead of a terminal prompt. The requester POSTs and
waits; the UI answers over POST /api/approvals/{id}. Anything but an explicit
yes -- no answer in time included -- is a refusal."""

import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.status_bus import status_bus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

_pending: dict[str, asyncio.Future[bool]] = {}


class ApprovalIn(BaseModel):
    title: str
    lines: list[str] = []
    timeout_s: float = Field(60.0, gt=0, le=300)


class AnswerIn(BaseModel):
    approved: bool


@router.post("")
async def request_approval(body: ApprovalIn) -> dict:
    approval_id = uuid.uuid4().hex
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    _pending[approval_id] = future
    logger.info("approval %s requested: %r -> %d socket(s)", approval_id[:8], body.title, status_bus.client_count())
    await status_bus.broadcast({"type": "approval", "id": approval_id, "title": body.title, "lines": body.lines})
    try:
        approved = await asyncio.wait_for(future, body.timeout_s)
    except TimeoutError:
        approved = False
    finally:
        _pending.pop(approval_id, None)
        await status_bus.broadcast({"type": "approval_done", "id": approval_id})
    logger.info("approval %s %s", approval_id[:8], "approved" if approved else "refused (denied or timed out)")
    return {"approved": approved}


@router.post("/{approval_id}", status_code=204)
async def answer(approval_id: str, body: AnswerIn) -> None:
    future = _pending.get(approval_id)
    logger.info("approval %s answered: approved=%s (waiting=%s)", approval_id[:8], body.approved, future is not None and not future.done())
    if future is None or future.done():
        raise HTTPException(404, "that request is no longer waiting")
    future.set_result(body.approved)
