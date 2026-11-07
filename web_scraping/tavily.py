"""Tavily Search API: https://api.tavily.com. Standalone; see __init__."""

import os
import time

import httpx

from web_scraping import usage
from web_scraping.base import Hit, SearchError, SearchReport

URL = "https://api.tavily.com/search"
USAGE_URL = "https://api.tavily.com/usage"
# Brave's freshness codes -> Tavily's time_range, so callers use one vocabulary.
_RANGE = {"pd": "day", "pw": "week", "pm": "month", "py": "year"}


def _key() -> str:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise SearchError("TAVILY_API_KEY is not set")
    return key


def search(query: str, count: int = 5, freshness: str | None = None, timeout: float = 10.0) -> SearchReport:
    """freshness: "pd" | "pw" | "pm" | "py" or None. Raises SearchError."""
    body = {"query": query[:400], "max_results": count}
    if freshness:
        body["time_range"] = _RANGE[freshness]
    t0 = time.perf_counter()
    try:
        r = httpx.post(URL, json=body, timeout=timeout, headers={"Authorization": f"Bearer {_key()}"})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SearchError(f"Tavily search failed: {exc}") from exc
    ms = round((time.perf_counter() - t0) * 1000)
    usage.record("tavily", ms)
    return SearchReport(query, "tavily", ms, [
        Hit(i.get("title", ""), i.get("url", ""), i.get("content", "")) for i in r.json().get("results", [])
    ])


def live_usage() -> dict | None:
    """Real credits from Tavily: {"used", "limit", "plan"}; None if unreachable."""
    try:
        r = httpx.get(USAGE_URL, timeout=5.0, headers={"Authorization": f"Bearer {_key()}"})
        r.raise_for_status()
        j = r.json()
    except (httpx.HTTPError, SearchError, ValueError):
        return None
    acct = j.get("account") or {}
    key = j.get("key") or {}
    return {
        "used": acct.get("plan_usage", key.get("usage")),
        "limit": acct.get("plan_limit", key.get("limit")),
        "plan": acct.get("current_plan"),
    }
