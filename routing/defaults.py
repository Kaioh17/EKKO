"""Routing values the backend needs without importing torch (the settings
schema and the overview stats read them)."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from paths import data  # noqa: E402

DEFAULT_THRESHOLD = 0.58
ROUTING_LOG_PATH = data("routing", "logs", "routing.jsonl")
