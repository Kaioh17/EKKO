"""Brave Search API: https://api.search.brave.com. Standalone; see __init__."""

import os
import time

import httpx

from web_scraping import usage
from web_scraping.base import Hit, SearchError, SearchReport

URL = "https://api.search.brave.com/res/v1/web/search"


def _remaining(headers) -> int | None:
    # X-RateLimit-Remaining is "<per-second>, <per-month>" on paid plans.
    # ponytail: header format is from memory of Brave's docs, not verified live; a bad parse just yields None.
    try:
        return int(headers.get("x-ratelimit-remaining", "").split(",")[-1])
    except ValueError:
        return None


def search(query: str, count: int = 5, freshness: str | None = None, timeout: float = 5.0) -> SearchReport:
    """freshness: "pd" | "pw" | "pm" | "py" or None. Raises SearchError."""
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        raise SearchError("BRAVE_API_KEY is not set")
    params = {"q": query[:400], "count": count}
    if freshness:
        params["freshness"] = freshness
    t0 = time.perf_counter()
    try:
        r = httpx.get(URL, params=params, timeout=timeout,
                      headers={"X-Subscription-Token": key, "Accept": "application/json"})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SearchError(f"Brave search failed: {exc}") from exc
    ms = round((time.perf_counter() - t0) * 1000)
    usage.record("brave", ms, remaining=_remaining(r.headers))
    items = r.json().get("web", {}).get("results", [])
    return SearchReport(query, "brave", ms, [
        Hit(i.get("title", ""), i.get("url", ""), i.get("description", ""), i.get("age", "")) for i in items
    ])


def live_usage() -> dict | None:
    return None  # no usage endpoint; the dashboard link covers it
