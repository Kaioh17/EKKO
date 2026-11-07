import json

from llm_fallback.brain.parsing import AnswerStream
from llm_fallback.brain.transport import _read_sse


def _sse(*deltas: str) -> list[bytes]:
    events = [{"model": "m", "choices": [{"delta": {"content": d}}]} for d in deltas]
    events.append({"model": "m", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 5}})
    return [f"data: {json.dumps(e)}\n".encode() for e in events] + [b"data: [DONE]\n"]


def test_sse_folds_to_envelope_and_streams_answer_tokens():
    reply = '{"intent": null, "answer": "Hi there, friend.", "follow_up": null}'
    deltas = [reply[i : i + 3] for i in range(0, len(reply), 3)]
    seen, stream, got = [], AnswerStream(), []

    def sink(delta):
        if delta is None:
            seen.append("start")
            return
        stream.feed(delta)
        got.append(stream.partial)

    envelope = _read_sse(_sse(*deltas), sink)

    assert seen == ["start"]
    assert envelope["choices"][0]["message"]["content"] == reply
    assert envelope["usage"] == {"prompt_tokens": 3, "completion_tokens": 5}
    assert got[-1] == "Hi there, friend."
    assert any(0 < len(p) < len("Hi there, friend.") for p in got)  # grew token by token
