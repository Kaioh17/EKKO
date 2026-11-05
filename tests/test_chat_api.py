"""POST /api/chat: the HTTP wrapper around chatbot/'s text turn. The engine
(routing models, gitignored chatbot/) is swapped for a fake, so this covers
the wrapper only: validation, the 503 when chat is missing, the reply, and
the today-summary nudge it hands the engine. Conversation memory itself is
the day log, see tests/test_memory_followup.py."""

from backend.routers import chat
from memory import today_summary
from tests.conftest import AUTH


def _engine(seen: list):
    def handle(transcript, router, **kw):
        seen.append((transcript, router, kw))
        kw["on_text"]("one")
        kw["on_text"]("two")
        return "key", None, "gemini"

    return chat._Engine(handle, "rtr", "domains", "facts")


def test_reply_joins_chunks(client, monkeypatch):
    seen: list = []
    monkeypatch.setattr(chat, "_engine", lambda: _engine(seen))

    r = client.post("/api/chat", json={"message": "  hello  "}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert (body["reply"], body["model"]) == ("one\n\ntwo", "gemini")
    assert body["stats"]["handled_by"] == "none" and body["stats"]["total_s"] >= 0  # no LLM outcome reported
    transcript, router, kw = seen[0]
    assert (transcript, router) == ("hello", "rtr")
    assert (kw["domain_router"], kw["pending_facts"]) == ("domains", "facts")
    assert kw["on_recorded"] is today_summary.refresh_async
    assert "session_until" not in kw


def test_blank_message_is_rejected(client, monkeypatch):
    monkeypatch.setattr(chat, "_engine", lambda: None)
    assert client.post("/api/chat", json={"message": "   "}, headers=AUTH).status_code == 422


def test_503_when_chat_is_missing(client, monkeypatch):
    monkeypatch.setattr(chat, "_engine", lambda: None)
    assert client.post("/api/chat", json={"message": "hi"}, headers=AUTH).status_code == 503


def test_provider_pick_disables_failover(client, monkeypatch):
    seen: list = []
    monkeypatch.setattr(chat, "_engine", lambda: _engine(seen))
    client.post("/api/chat", json={"message": "hi", "provider": "ollama"}, headers=AUTH)
    client.post("/api/chat", json={"message": "hi"}, headers=AUTH)
    assert (seen[0][2]["provider"], seen[0][2]["failover"]) == ("ollama", False)
    assert (seen[1][2]["provider"], seen[1][2]["failover"]) == (None, None)
    assert client.post("/api/chat", json={"message": "hi", "provider": "nope"}, headers=AUTH).status_code == 422


def test_chat_text_never_has_em_dashes():
    from chatbot.chat_listener import no_em_dashes

    assert no_em_dashes("a — b—c") == "a, b, c"
    assert no_em_dashes(None) is None


def test_stats_from_the_llm_outcome(client, monkeypatch):
    from llm_fallback.brain import budget
    from llm_fallback.brain.outcome import FallbackOutcome

    outcome = FallbackOutcome(
        bundle=None, answer="hi", urls=["https://a.example"], raw_intent=None, raw_slots={}, reason="",
        provider="deepseek", model="deepseek-v4-flash",
        usage={"prompt_tokens": 1000, "completion_tokens": 200, "prompt_cache_hit_tokens": 600},
        latency_seconds=2.5, error=None, source="chat",
        extras={"cost_usd": 0.0012, "failover_from": ["gemini"]},
        signals={"web_search": False, "web_research": True, "recall_hits": 1},
    )

    def handle(transcript, router, **kw):
        kw["on_outcome"](outcome, {"window_turns": 4, "window_tokens_est": 900})
        kw["on_text"]("hi")
        return "ambiguous_answer", None, "deepseek-v4-flash"

    monkeypatch.setattr(chat, "_engine", lambda: chat._Engine(handle, "rtr", None, None))
    monkeypatch.setattr(budget, "spent_usd", lambda p: 0.5)
    monkeypatch.setattr(budget, "budget_usd", lambda p: 2.0)

    s = client.post("/api/chat", json={"message": "hi"}, headers=AUTH).json()["stats"]
    assert (s["handled_by"], s["provider"], s["llm_s"]) == ("llm", "deepseek", 2.5)
    assert s["tokens"] == {"input": 1000, "output": 200, "cached": 600}
    assert s["cost_usd"] == 0.0012 and s["budget"] == {"spent_usd": 0.5, "cap_usd": 2.0}
    assert s["failover_from"] == ["gemini"] and s["web_research"] and not s["web_search"]
    assert (s["sources"], s["recall_hits"], s["context_turns"], s["context_tokens"]) == (1, 1, 4, 900)


def test_token_shapes_across_providers():
    t = chat._tokens
    assert t({"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}).model_dump() == {"input": 100, "output": 5, "cached": 90}
    assert t({"promptTokenCount": 30, "candidatesTokenCount": 7}).model_dump() == {"input": 30, "output": 7, "cached": None}
    assert t({"prompt_eval_count": 20, "eval_count": 3}).model_dump() == {"input": 20, "output": 3, "cached": None}
    assert t({}) is None
