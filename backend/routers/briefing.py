"""The daily briefing's non-settings routes. Its settings are the generic
/api/settings/briefing (backend/schemas/settings.py BriefingSettings); this
adds the Preview button, and the small JSON mirror the PowerShell/Linux
handlers read (they can't open the settings DB cheaply on the voice path)."""

import json
import logging
import sys
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import load
from backend.schemas.settings import BriefingSettings
from paths import ROOT, data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/briefing", tags=["briefing"])

# Read by scripts/windows/lib/system_metrics.ps1 and scripts/system_metrics.py.
MIRROR_PATH = data("backend", "briefing_mirror.json")


def _script():
    """scripts/daily_briefing.py, the one place the briefing is built
    (chat_listener imports it the same way)."""
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import daily_briefing

    return daily_briefing


def write_mirror() -> None:
    cfg = load("briefing", BriefingSettings)
    mirror = {
        "metrics": cfg.system_metrics,
        "cpu_high": cfg.cpu_high_percent,
        "ram_high": cfg.ram_high_percent,
        "disk_free_low": cfg.disk_free_low_percent,
        "battery_low": cfg.battery_low_percent,
        "speak_ok": cfg.speak_system_ok,
        "speak_alert": cfg.speak_system_alert,
        "speak_handoff": cfg.speak_handoff,
        # plain = screen only, so the handlers stay silent too
        "deliver": "sandbox" if cfg.mode == "plain" else cfg.deliver,
    }
    try:
        MIRROR_PATH.parent.mkdir(parents=True, exist_ok=True)
        MIRROR_PATH.write_text(json.dumps(mirror), encoding="utf-8")
    except OSError as exc:
        logger.warning("briefing mirror not written (handlers use defaults): %s", exc)


def startup() -> None:
    """Backend start: pick up a legacy personal yaml, then refresh the mirror."""
    try:
        _script().load_config()
    except Exception:  # noqa: BLE001 -- a briefing problem must not stop the backend
        logger.exception("briefing startup failed")
    write_mirror()


class PreviewIn(BaseModel):
    mode: Literal["plain", "llm"] | None = None  # None = the saved mode


@router.post("/preview")
def preview(body: PreviewIn) -> dict:
    """Runs the briefing now (no speech, no tabs, no system health) and
    returns its sections plus the tokens the narration used. Takes as long
    as the fetches, so the UI shows a spinner."""
    return _script().preview(body.mode)
