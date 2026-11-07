"""What the Search panel needs: the provider catalog (costs, pros/cons, how to
get a key, dashboard link), usage, and a one-shot connection test. Provider
choice and result count are the generated /api/settings/search routes; keys
are /api/keys. Never returns key values."""

import os
from dataclasses import asdict

from fastapi import APIRouter, HTTPException

import web_scraping
from web_scraping import usage
from web_scraping.base import SearchError
from web_scraping.catalog import CATALOG

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/providers")
def list_providers() -> list[dict]:
    return [asdict(p) for p in CATALOG]


@router.get("/usage")
def get_usage() -> dict[str, dict]:
    """{provider: {searches_this_month, last_ms, last_at, last_error, remaining, live}}.
    `live` is the provider's own numbers (null when it has none or the key is unset)."""
    out = {}
    for p in CATALOG:
        has_key = bool(os.environ.get(p.key_env))
        live = web_scraping.PROVIDER_MODULES[p.name].live_usage() if has_key and p.live_usage else None
        out[p.name] = {**usage.local(p.name), "live": live}
    return out


@router.post("/test/{name}")
def test_provider(name: str) -> dict:
    """One real search (costs one credit) so the user can confirm a key works and see its speed."""
    if name not in web_scraping.PROVIDER_MODULES:
        raise HTTPException(404, f"unknown provider {name!r}")
    try:
        report = web_scraping.search("what is the date today", count=3, name=name)
    except SearchError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"elapsed_ms": report.elapsed_ms, "results": len(report.hits)}
