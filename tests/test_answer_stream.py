import json

from llm_fallback.brain.parsing import AnswerStream, remaining_answer


def _feed(reply: str, size: int) -> tuple[list[str], AnswerStream]:
    stream = AnswerStream()
    chunks = []
    for i in range(0, len(reply), size):
        chunks += stream.feed(reply[i : i + size])
    return chunks, stream


def _reply(answer, intent=None, follow_up=None) -> str:
    return "```json\n" + json.dumps({"intent": intent, "slots": {}, "answer": answer, "urls": [], "follow_up": follow_up})


def test_sentences_in_order_for_any_slicing():
    answer = 'Butter is churned cream. It takes "about" ten minutes\nby hand! Salt it to taste.'
    for size in (1, 2, 3, 7, 1000):
        chunks, stream = _feed(_reply(answer), size)
        assert "".join(chunks) == answer == stream.spoken, size
        assert chunks[0] == "Butter is churned cream. ", size


def test_command_pick_is_never_spoken():
    assert _feed(_reply("Opening it now. Done.", intent="open_app"), 5)[0] == []
    assert _feed(_reply(None), 5)[0] == []


def test_trailing_question_is_held_for_the_caller():
    chunks, stream = _feed(_reply("It is sunny. Want the forecast?", follow_up="Want the forecast?"), 4)
    assert chunks == ["It is sunny. "]
    assert remaining_answer("It is sunny.", stream.spoken) == ("", False)
    assert remaining_answer("It is sunny. Want the forecast?", stream.spoken) == ("Want the forecast?", False)


def test_first_chunk_breaks_at_a_long_clause():
    answer = "Well, the short version of the whole story is this, it rains a lot there."
    chunks, _ = _feed(_reply(answer), 3)
    assert chunks[0] == "Well, the short version of the whole story is this, "
    assert "".join(chunks) == answer


def test_remaining_answer_mismatch_returns_everything():
    assert remaining_answer("Something else entirely.", "It is sunny. ") == ("Something else entirely.", True)
