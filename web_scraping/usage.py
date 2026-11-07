"""Usage per provider. Two sources, shown together by the UI:
  - EKKO's own tally (searches this calendar month, last call speed and
    error) kept in <data>/web_scraping/usage.json, always available;
  - the provider's real numbers when it reports them (live_usage() in its
    module), else the UI links the provider's dashboard instead.
"""

import json
import threading
import time
from datetime import date

from paths import data

_PATH = data("web_scraping", "usage.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(provider: str, elapsed_ms: int | None = None, error: str | None = None, remaining: int | None = None) -> None:
    """Never raises: a failed tally write must not fail a search."""
    month = date.today().strftime("%Y-%m")
    try:
        with _lock:
            state = _load()
            p = state.setdefault(provider, {})
            if p.get("month") != month:
                p.update(month=month, searches=0)
            p["searches"] = p.get("searches", 0) + (0 if error else 1)
            p["last_at"] = int(time.time())
            p["last_ms"] = elapsed_ms if not error else p.get("last_ms")
            p["last_error"] = error
            if remaining is not None:
                p["remaining"] = remaining  # provider-reported quota left, from response headers
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            _PATH.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def local(provider: str) -> dict:
    p = _load().get(provider, {})
    this_month = p.get("month") == date.today().strftime("%Y-%m")
    return {
        "searches_this_month": p.get("searches", 0) if this_month else 0,
        "last_ms": p.get("last_ms"),
        "last_at": p.get("last_at"),
        "last_error": p.get("last_error"),
        "remaining": p.get("remaining"),
    }
