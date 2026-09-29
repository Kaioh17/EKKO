"""POST /api/chat: the HTTP wrapper around chatbot/'s text turn. The engine
(routing models, gitignored chatbot/) is swapped for a fake, so this covers
the wrapper only: validation, the 503 when chat is missing, the reply, and
the session deadline carried from one turn to the next."""

from backend.routers import chat
from tests.conftest import AUTH


def _engine(seen: list):
    def handle(transcript, router, **kw):
        seen.append((transcript, router, kw))
        kw["on_text"]("one")
        kw["on_text"]("two")
        return "key", None, 123.0, "gemini"

    return chat._Engine(handle, "rtr", "domains", "facts", 30.0)


def test_reply_joins_chunks_and_carries_the_session(client, monkeypatch):
    seen: list = []
    monkeypatch.setattr(chat, "_engine", lambda: _engine(seen))
    monkeypatch.setattr(chat, "_session_until", None)

    r = client.post("/api/chat", json={"message": "  hello  "}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"reply": "one\n\ntwo", "model": "gemini"}
    transcript, router, kw = seen[0]
    assert (transcript, router) == ("hello", "rtr")
    assert (kw["domain_router"], kw["pending_facts"], kw["session_timeout"], kw["session_until"]) == ("domains", "facts", 30.0, None)

    client.post("/api/chat", json={"message": "again"}, headers=AUTH)
    assert seen[1][2]["session_until"] == 123.0


def test_blank_message_is_rejected(client, monkeypatch):
    monkeypatch.setattr(chat, "_engine", lambda: None)
    assert client.post("/api/chat", json={"message": "   "}, headers=AUTH).status_code == 422


def test_503_when_chat_is_missing(client, monkeypatch):
    monkeypatch.setattr(chat, "_engine", lambda: None)
    assert client.post("/api/chat", json={"message": "hi"}, headers=AUTH).status_code == 503
