"""A reply cut off by the output-token cap (seen live: a long markdown chat
answer from deepseek-v4-flash at max_tokens=1024) keeps its complete
sentences instead of becoming "Sorry, could you say that again?"."""

import json

from llm_fallback.brain import core
from llm_fallback.brain.parsing import TRUNCATED_REASON, parse_result
from llm_fallback.brain.providers import RawResponse

ANSWER = (
    "That's an easy fix. You don't need butter today.\n\n"
    "## Best swap\n\n- **Use 3/4 the amount of oil.** That's about 80 ml.\n\n## Two things to watch\n\n-"
)
CUT = '{"intent": null, "slots": {}, "answer": ' + json.dumps(ANSWER)[:-1]  # no closing quote or brace


def test_cut_off_answer_keeps_complete_sentences():
    parsed = parse_result(CUT)
    assert parsed.reason == TRUNCATED_REASON
    assert parsed.answer.startswith("That's an easy fix.") and "That's about 80 ml." in parsed.answer
    assert not parsed.answer.endswith("-")


def test_cut_off_command_pick_is_not_salvaged():
    assert parse_result('{"intent": "open_chrome", "slots": {}, "answer": "Opening').answer is None
    assert parse_result('{"intent": null, "slo').answer is None


class _Fake:
    default_model = "fake-1"

    def capability_note(self, **_kw):
        return ""

    def call(self, prompt, system, model, **_kw):
        return RawResponse(text=CUT, model=model, usage={}, latency_seconds=0.1)


def test_chat_marks_a_cut_off_answer_voice_does_not(monkeypatch):
    monkeypatch.setattr(core, "get_provider", lambda _name: _Fake())
    chat = core.attempt_fallback("no butter", provider="gemini", log_path=False, enable_research=False, source="chat")
    voice = core.attempt_fallback("no butter", provider="gemini", log_path=False, enable_research=False)
    assert chat.answer.endswith("*(This answer was cut off. Ask me to continue.)*")
    assert voice.answer and "cut off" not in voice.answer
