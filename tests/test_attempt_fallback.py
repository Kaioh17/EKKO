"""attempt_fallback(): prompt -> provider.call() -> parse -> validate_pick
-> outcome, with a fake provider. Covers a command pick, an answer, the
one retry after a rejected pick, a spend-cap block, and a transport
failure (which must come back as an outcome, never an exception)."""

import json

import pytest

from llm_fallback.brain import core
from llm_fallback.brain.providers import ProviderBlocked, ProviderError, RawResponse


class FakeProvider:
    default_model = "fake-1"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def capability_note(self, **_kw):
        return "no tools"

    def call(self, prompt, system, model, **_kw):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return RawResponse(text=json.dumps(reply), model=model, usage={"tokens": 10}, latency_seconds=0.5, extras={"n": 1})


@pytest.fixture
def run(monkeypatch):
    def go(*replies, transcript="open task manager", **kwargs):
        fake = FakeProvider(*replies)
        monkeypatch.setattr(core, "get_provider", lambda _name: fake)
        outcome = core.attempt_fallback(transcript, provider="gemini", log_path=False, enable_research=False, **kwargs)
        return outcome, fake

    return go


def test_valid_command_pick(run):
    outcome, fake = run({"intent": "open_task_manager", "slots": {}, "reason": "clear"})
    assert outcome.bundle is not None and outcome.bundle.intent == "open_task_manager"
    assert (outcome.error, outcome.model, outcome.latency_seconds, outcome.usage) == (None, "fake-1", 0.5, {"tokens": 10})
    assert len(fake.prompts) == 1


def test_spoken_answer(run):
    outcome, _ = run({"intent": None, "answer": "Paris.", "urls": ["https://a.example"]}, transcript="capital of France")
    assert outcome.bundle is None and outcome.answer == "Paris."
    assert outcome.urls == ["https://a.example"]


def test_rejected_pick_is_retried_once_and_answered(run):
    # web_search with no trigger phrase in the transcript fails re-validation
    outcome, fake = run(
        {"intent": "web_search", "slots": {"query": "butter"}},
        {"intent": None, "answer": "Whisk cream until it separates.", "urls": []},
        transcript="how do I make butter",
    )
    assert outcome.bundle is None and outcome.answer == "Whisk cream until it separates."
    assert len(fake.prompts) == 2 and "rejected" in fake.prompts[1] and "rejected" not in fake.prompts[0]
    assert outcome.usage == {"tokens": 20}  # both calls counted


def test_retry_that_repeats_the_bad_pick_keeps_the_first_result(run):
    outcome, fake = run(
        {"intent": "web_search", "slots": {"query": "butter"}},
        {"intent": "web_search", "slots": {}},
        transcript="how do I make butter",
    )
    assert outcome.bundle is None and outcome.answer is None and outcome.raw_intent == "web_search"
    assert len(fake.prompts) == 2 and outcome.error is None


def test_spend_cap_is_spoken(run):
    outcome, _ = run(ProviderBlocked("DeepSeek hit its cap."))
    assert outcome.answer == "DeepSeek hit its cap." and outcome.extras == {"blocked": 1} and outcome.error is None


def test_transport_failure_is_an_outcome_not_an_exception(run):
    outcome, _ = run(ProviderError("HTTP 503"))
    assert (outcome.bundle, outcome.answer, outcome.error) == (None, None, "HTTP 503")


def test_chat_source_is_stamped_on_the_bundle(run):
    outcome, _ = run({"intent": "open_task_manager", "slots": {}}, source="chat")
    assert outcome.bundle.source == "chat"
