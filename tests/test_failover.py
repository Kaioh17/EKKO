"""attempt_with_failover(): which provider starts the chain, handoff,
cooldown, and what each hop is asked with. attempt_fallback is faked."""

import sys
import types

import pytest

from backend import db
from llm_fallback.brain import failover
from llm_fallback.brain.outcome import FallbackOutcome


def _outcome(provider, error=None, answer="ok", extras=None):
    return FallbackOutcome(
        bundle=None, answer=answer, urls=[], raw_intent=None, raw_slots={}, reason="", provider=provider,
        model="m", usage={}, latency_seconds=0.0, error=error, extras=extras or {},
    )


@pytest.fixture
def calls(monkeypatch):
    """Every attempt_fallback() call as (provider, kwargs); `fail` names providers that error."""
    class Calls(list):
        fail: set

    log, fail = Calls(), set()
    db.DB_PATH.unlink(missing_ok=True)
    failover._cooldown_until.clear()

    def fake(transcript, provider, **kwargs):
        log.append((provider, kwargs))
        return _outcome(provider, error="503" if provider in fail else None)

    monkeypatch.setattr(failover, "attempt_fallback", fake)
    monkeypatch.setattr(failover, "_log_handoff", lambda entry: None)
    log.fail = fail
    return log


def test_provider_is_read_per_call_not_frozen(calls):
    db.put_section("llm", {"provider": "openai", "failover_order": ["openai", "gemini"]})
    failover.attempt_with_failover("hi")
    db.put_section("llm", {"provider": "gemini", "failover_order": ["gemini", "openai"]})
    failover.attempt_with_failover("hi")
    assert [p for p, _ in calls] == ["openai", "gemini"]


def test_explicit_provider_starts_the_chain_there(calls):
    db.put_section("llm", {"failover_order": ["gemini", "deepseek", "ollama"]})
    calls.fail.add("deepseek")
    outcome = failover.attempt_with_failover("hi", provider="deepseek")
    assert [p for p, _ in calls] == ["deepseek", "ollama"]  # gemini is before the start: never tried
    assert outcome.provider == "ollama" and outcome.extras["failover_from"] == ["deepseek"]


def test_failed_provider_is_skipped_during_cooldown(calls):
    db.put_section("llm", {"failover_order": ["gemini", "ollama"]})
    calls.fail.add("gemini")
    failover.attempt_with_failover("one")
    calls.clear()
    failover.attempt_with_failover("two")
    assert [p for p, _ in calls] == ["ollama"]


def test_all_failing_reports_every_error(calls):
    db.put_section("llm", {"failover_order": ["gemini", "ollama"]})
    calls.fail.update({"gemini", "ollama"})
    outcome = failover.attempt_with_failover("hi")
    assert outcome.answer is None
    assert outcome.error == "all providers failed - gemini: 503; ollama: 503"


def test_failover_disabled_runs_only_the_start(calls):
    db.put_section("llm", {"failover_enabled": False})
    calls.fail.add("gemini")
    failover.attempt_with_failover("hi")
    assert [p for p, _ in calls] == ["gemini"]


def test_next_hop_drops_model_and_rebuilds_domain_override(calls, monkeypatch):
    fake_loader = types.SimpleNamespace(compose_system_instruction=lambda domain, base: f"{domain}+{base[:6]}")
    monkeypatch.setitem(sys.modules, "domains.loader", fake_loader)
    db.put_section("llm", {"failover_order": ["gemini", "ollama"]})
    calls.fail.add("gemini")
    failover.attempt_with_failover("hi", domain="finance", system_instruction_override="GEMINI-FLAVOURED", model="gemini-x")
    (_, first), (_, second) = calls
    assert first["system_instruction_override"] == "GEMINI-FLAVOURED" and first["model"] == "gemini-x"
    assert "model" not in second
    assert second["system_instruction_override"].startswith("finance+")


def test_next_hop_without_domain_keeps_the_override(calls):
    db.put_section("llm", {"failover_order": ["gemini", "ollama"]})
    calls.fail.add("gemini")
    failover.attempt_with_failover("hi", system_instruction_override="X", model="gemini-x")
    assert [k["system_instruction_override"] for _, k in calls] == ["X", "X"]
