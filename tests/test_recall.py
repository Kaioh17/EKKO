import json

from llm_fallback.brain import core
from llm_fallback.brain.parsing import parse_result
from llm_fallback.brain.providers import RawResponse, register_provider
from memory import archive

KEY = "2026-10-03 | installer build"


class _Fake:
    name = "fakerecall"
    default_model = "fake-1"

    def __init__(self):
        self.prompts = []

    def capability_note(self, **kw):
        return ""

    def call(self, prompt, system_instruction, timeout=None, **kw):
        self.prompts.append(prompt)
        if "recalled_archive" in prompt:
            reply = {"intent": None, "slots": {}, "answer": "You built the installer.", "recall": []}
        else:
            reply = {"intent": None, "slots": {}, "answer": None, "recall": [KEY]}
        return RawResponse(text=json.dumps(reply), model=self.default_model, usage={"n": 1}, latency_seconds=0.1)


def test_recall_fetches_archive_and_asks_again(tmp_path, monkeypatch):
    p = tmp_path / "archive.json"
    archive.write_entry(KEY, {"summary": "worked on the installer", "details": "NSIS vs WiX"}, p)
    monkeypatch.setattr(archive, "ARCHIVE_PATH", p)
    monkeypatch.setattr(archive.fetch, "__defaults__", (archive.FETCH_TOKEN_CAP, p))
    logged = []
    monkeypatch.setattr(core, "log_metrics", logged.append)
    fake = _Fake()
    register_provider(fake)

    out = core.attempt_fallback(
        "what did we decide about the installer",
        provider="fakerecall", enable_research=False, log_path=tmp_path / "fb.jsonl",
        archive_keys=[KEY], context_stats={"truncated_turns": 0},
    )

    assert out.answer == "You built the installer."
    assert len(fake.prompts) == 2 and "NSIS vs WiX" in fake.prompts[1] and "archive_index" in fake.prompts[0]
    assert out.usage == {"n": 2}
    assert logged[0]["recall_hits"] == 1 and logged[0]["recall_extra_latency_s"] is not None


def test_recall_ignored_when_answer_present():
    reply = json.dumps({"intent": None, "answer": "hi", "recall": ["x"]})
    assert parse_result(reply).recall == ()
