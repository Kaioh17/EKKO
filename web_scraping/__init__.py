"""Live web search for EKKO. Plug in anywhere with:

    from web_scraping import search, available
    if available():
        report = search("how is AMPX doing right now")  # SearchReport
        report.as_prompt_block()  # text for an LLM prompt
        report.hits               # [Hit(title, url, snippet, age)]

The provider (Brave or Tavily) is the UI's `search` setting. search()
raises SearchError; research() never raises (returns None), for callers
that must not break a voice turn.
"""

import os
import re

from web_scraping import brave, tavily, usage
from web_scraping.base import Hit, SearchError, SearchReport  # noqa: F401 -- public API
from web_scraping.catalog import CATALOG, KEY_NAMES, PROVIDERS  # noqa: F401 -- public API

PROVIDER_MODULES = {"brave": brave, "tavily": tavily}
_KEY_ENV = {p.name: p.key_env for p in CATALOG}
_TODAY = re.compile(r"\b(today|tonight|right now|breaking|this morning|live)\b", re.I)


def _settings():
    from backend.config import get  # lazy: the pipeline imports this without FastAPI

    return get("search")


def available() -> bool:
    """Research is switched on (llm.research_enabled) and the chosen provider has a key."""
    from backend.config import get

    return get("llm").research_enabled and bool(os.environ.get(_KEY_ENV[_settings().provider]))


def search(query: str, count: int | None = None, freshness: str | None = None, name: str | None = None) -> SearchReport:
    s = _settings()
    name = name or s.provider
    try:
        return PROVIDER_MODULES[name].search(query, count or s.max_results, freshness)
    except SearchError as exc:
        usage.record(name, error=str(exc))
        raise


def research(question: str) -> SearchReport | None:
    """What a voice/chat turn calls: one search; None when unavailable, failed or empty."""
    if not available():
        return None
    try:
        report = search(question, freshness="pd" if _TODAY.search(question) else None)
    except SearchError as exc:
        print(f"[web_scraping] {exc}")
        return None
    return report if report.hits else None
