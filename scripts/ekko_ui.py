"""Best-effort mirror of a scripts/ terminal report into ekko-ui's backend
(backend/routers/reports.py) -- never raises, never blocks more than a
beat: these report windows are launched off the voice pipeline's critical
path (see daily_briefing.py's and render_report.py's own module
docstrings) and must keep rendering fine with the backend not running,
same bug-tolerant posture as their own fetch/LLM/TTS steps.

EKKO_UI_URL overrides the backend's address (default matches
backend/main.py's CORS origin / ekko-ui/src/api/client.ts's BASE_URL).
Read via routing/host.py's load_dotenv(), same as EKKO_OS -- so .env
covers this even for render_report.py, which never imports routing.host
itself.
"""

import os
import sys
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from routing import host  # noqa: E402,F401 -- import triggers its load_dotenv()

DEFAULT_URL = os.environ.get("EKKO_UI_URL", "http://127.0.0.1:8765")


def push_report(kind: str, title: str, payload: dict, base_url: str = DEFAULT_URL, timeout: float = 1.5) -> None:
    try:
        requests.post(f"{base_url}/api/reports/push", json={"kind": kind, "title": title, "payload": payload}, headers={"X-Ekko-Token": os.environ.get("EKKO_API_TOKEN", "")}, timeout=timeout)
    except requests.RequestException:
        pass
