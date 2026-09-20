"""Lets scripts/render_report.py and scripts/daily_briefing.py mirror their
rich-terminal report windows into ekko-ui, without giving up those windows
-- see those scripts' own module docstrings for why the terminal display
stays (a visual companion, not a replacement). Broadcasts over the same
status_bus every /ws/status subscriber already listens on, tagged
type="report" instead of "status" so the UI can tell them apart on one
socket rather than needing a second one.
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.status_bus import status_bus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportIn(BaseModel):
    kind: str
    title: str
    payload: dict


@router.post("/push", status_code=204)
async def push_report(body: ReportIn) -> None:
    logger.info("report pushed: kind=%s title=%r", body.kind, body.title)
    await status_bus.broadcast({"type": "report", "kind": body.kind, "title": body.title, "payload": body.payload})
