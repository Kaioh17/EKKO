"""The spec's validation: a multi-turn reference ("explain item 3 from
earlier") resolves no matter how long the earlier answers were or how long
ago they were given, across voice and chat, with matched commands in
between. Goes through the real memory.context.gather() and
core.attempt_fallback() with a fake provider that records the prompt."""

import datetime
import json
import sys

import pytest

from llm_fallback.brain import core
from llm_fallback.brain.providers import RawResponse
from memory import day_log
from memory.context import gather


class _Fake:
    default_model = "fake-1"

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def capability_note(self, **_kw):
        return ""

    def call(self, prompt, system, model, **_kw):
        self.prompts.append(prompt)
        return RawResponse(text=json.dumps(self.reply), model=model, usage={}, latency_seconds=0.1)


def _answer(topic: str) -> str:
    filler = "Some background detail that makes this a long, structured answer. " * 15
    items = "\n".join(f"{i}. {topic} step {i}: {filler}" for i in range(1, 4))
    return f"## {topic}\n\n{items}\n\n3b. Last point about {topic}: fold in the egg whites gently."


@pytest.fixture
def ask(monkeypatch):
    day_log.clear_all()

    def go(transcript, reply=None, **kw):
        fake = _Fake(reply or {"intent": None, "slots": {}, "answer": "ok", "recall": []})
        monkeypatch.setattr(core, "get_provider", lambda _name: fake)
        ctx = gather()
        out = core.attempt_fallback(transcript, provider="gemini", log_path=False, enable_research=False, source="chat", **ctx.as_kwargs(), **kw)
        return out, fake.prompts[0], ctx

    yield go
    day_log.clear_all()


def _ago(minutes):
    return datetime.datetime.now() - datetime.timedelta(minutes=minutes)


def test_item_from_a_long_earlier_answer_is_in_the_prompt_whole(ask):
    for i, topic in enumerate(["sponge cake", "pancakes", "souffle"]):
        a = _answer(topic)
        assert len(a) > 1500  # far past the old 600-char cap
        day_log.record_turn(f"how do I make {topic}", a, source="chat", now=_ago(60 - 10 * i))
    day_log.record_turn("open task manager", "Opening Task Manager.", source="voice", kind="command", now=_ago(25))

    # Ten minutes of silence: no timer may drop any of it.
    _out, prompt, ctx = ask("explain item 3 from earlier")
    payload = json.loads(prompt[prompt.index("{"): prompt.index("\n\nRespond with ONLY")])

    conv = payload["conversation"]
    assert "souffle step 3" in conv and "fold in the egg whites gently" in conv
    assert "Opening Task Manager." in conv and "|voice|command]" in conv
    assert "short_memory" not in payload and "conversation_24h" not in payload
    assert ctx.stats["window_turns"] == 4 and ctx.stats["evicted_turns"] == 0
    assert "Your previous answer and follow_up question are its last entry" in prompt


def test_window_drops_oldest_whole_turns_when_over_budget(ask):
    for i in range(12):
        day_log.record_turn(f"question {i}", _answer(f"topic {i}"), source="chat", now=_ago(120 - i))
    _out, prompt, ctx = ask("and the last one?")
    assert ctx.stats["evicted_turns"] > 0
    assert "topic 11 step 3" in prompt and "question 0\"" not in prompt and "topic 0 step" not in prompt


@pytest.mark.skipif(sys.platform != "win32", reason="file_operation is os: [windows] in intents.yaml")
def test_clarified_file_request_is_rebuilt_only_from_user_words(ask):
    day_log.record_turn("create a text file called black", "Which folder?", source="voice", now=_ago(3))
    reply = {"intent": "file_operation", "slots": {"instruction": "create a text file called black in documents"}, "answer": None, "recall": []}
    out, _, _ = ask("in documents", reply=reply)
    assert out.bundle.slots["instruction"] == "create a text file called black in documents"

    invented = {"intent": "file_operation", "slots": {"instruction": "create a pdf called black in documents"}, "answer": None, "recall": []}
    out, _, _ = ask("in documents", reply=invented)
    assert out.bundle.slots["instruction"] == "in documents"  # "pdf" was never said: proposal refused
