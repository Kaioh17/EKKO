"""llm_fallback/catalog.py is the one list of providers; everything that
used to keep its own copy must agree with it and with what registers."""

from conftest import AUTH

from backend.routers.overview import _USAGE_FIELDS
from backend.schemas.settings import LlmSettings
from llm_fallback.brain.providers import available_providers, load_providers
from llm_fallback.catalog import CATALOG, KEY_NAMES, PAID_PROVIDERS, PROVIDERS


def test_every_catalog_provider_registers():
    load_providers()
    assert sorted(available_providers()) == sorted(PROVIDERS)


def test_paid_providers_have_keys_and_caps():
    assert PAID_PROVIDERS == ("deepseek", "claude_api", "openai")
    assert all(p.key_env for p in CATALOG if p.paid)
    assert set(LlmSettings().budget_usd) == set(PAID_PROVIDERS)


def test_schema_default_order_is_catalog_order():
    assert LlmSettings().failover_order == list(PROVIDERS)


def test_overview_covers_every_provider():
    assert set(_USAGE_FIELDS) == set(PROVIDERS)


def test_keys_api_lists_catalog_keys(client):
    assert list(client.get("/api/keys", headers=AUTH).json()) == list(KEY_NAMES)


def test_providers_endpoint(client):
    rows = client.get("/api/llm/providers", headers=AUTH).json()
    assert [r["name"] for r in rows] == list(PROVIDERS)
    assert all(r["model"] for r in rows)
    by_name = {r["name"]: r for r in rows}
    assert by_name["openai"]["model"] == LlmSettings().openai_model
    assert by_name["openai"]["model_setting"] == "openai_model"
    assert by_name["gemini"]["model_setting"] is None
