"""Pipeline code reads the DB, and the retired env vars no longer do anything."""

import pytest

from conftest import DATA_DIR


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    (DATA_DIR / "backend" / "ekko.db").unlink(missing_ok=True)
    for var in ("EKKO_LLM_PROVIDER", "EKKO_LLM_FAILOVER", "EKKO_LLM_FAILOVER_ORDER", "EKKO_LLM_FAILOVER_COOLDOWN_S",
                "EKKO_RESEARCH", "EKKO_TTS", "EKKO_OS", "EKKO_OPENAI_MODEL", "EKKO_OPENAI_BUDGET_USD"):
        monkeypatch.setenv(var, "garbage")


def put(section, values):
    from backend import db

    db.put_section(section, values)


def test_failover_reads_db():
    from llm_fallback.brain import failover

    assert failover.failover_order()[0] == "gemini"  # env garbage ignored
    assert failover.failover_enabled() is True
    put("llm", {"provider": "openai", "failover_order": ["openai", "deepseek"], "failover_enabled": False, "failover_cooldown_s": 5.0})
    assert failover.failover_order() == ("openai", "deepseek")
    assert failover.failover_enabled() is False
    assert failover._cooldown_seconds() == 5.0
    assert failover.failover_chain("deepseek") == ("deepseek",)


def test_default_provider_and_research_read_db():
    from llm_fallback.brain import core, research

    assert core.default_provider() == "gemini"
    put("llm", {"provider": "openai", "research_enabled": False})
    assert core.default_provider() == "openai"
    assert research.research_enabled() is False


def test_budget_and_model_read_db():
    from llm_fallback.brain import budget
    from llm_fallback.openai import provider as openai_provider

    assert budget.budget_usd("openai") == 2.0
    put("llm", {"budget_usd": {"deepseek": 2.0, "claude_api": 2.0, "openai": 7.5}, "openai_model": "gpt-x"})
    assert budget.budget_usd("openai") == 7.5
    assert openai_provider.configured_model() == "gpt-x"


def test_tts_engine_reads_db():
    from feedback import speech

    assert speech.tts_engine() == "piper"
    put("voice", {"tts_engine": "openai"})
    assert speech.tts_engine() == "openai"


def test_os_reads_db():
    from routing import host

    put("system", {"os": "linux"})
    assert host.current_os() == "linux"
