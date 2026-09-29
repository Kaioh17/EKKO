"""What the Models panel needs to draw the provider list: the static
catalog (llm_fallback/catalog.py) plus each registered provider's default
model, so the UI holds no copy of either."""

from dataclasses import asdict

from fastapi import APIRouter

from llm_fallback.brain.providers import available_providers, get_provider
from llm_fallback.catalog import CATALOG

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/providers")
def list_providers() -> list[dict]:
    registered = set(available_providers())
    return [
        {**asdict(info), "model": get_provider(info.name).default_model if info.name in registered else None}
        for info in CATALOG
    ]
