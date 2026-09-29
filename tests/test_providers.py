"""The shared HTTP transport and the three providers built on it, against
a fake urlopen (no network)."""

import io
import json
import urllib.error

import pytest

from llm_fallback.brain import transport
from llm_fallback.brain.providers import ProviderError
from llm_fallback.deepseek import provider as deepseek
from llm_fallback.ollama import provider as ollama
from llm_fallback.openai import provider as openai


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def wire(monkeypatch):
    """wire(reply) queues one reply (dict or exception); requests land in wire.sent."""
    queue, sent = [], []

    def urlopen(request, timeout):
        sent.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Reply(json.dumps(reply).encode())

    monkeypatch.setattr(transport.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(deepseek, "check_budget", lambda *a: None)
    monkeypatch.setattr(openai, "check_budget", lambda *a: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")

    def push(reply):
        queue.append(reply)

    push.sent = sent
    return push


def _chat(text="{}", **usage):
    return {"choices": [{"message": {"content": text}}], "usage": usage}


def test_deepseek_call(wire):
    wire(_chat('{"intent": null}', prompt_tokens=100, completion_tokens=10, prompt_cache_hit_tokens=40, prompt_cache_miss_tokens=60))
    raw = deepseek.DeepSeekProvider().call("hi", "sys", model="deepseek-v4-flash")
    assert raw.text == '{"intent": null}'
    assert raw.usage == {"prompt_tokens": 100, "completion_tokens": 10, "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60}
    assert raw.extras["cost_usd"] == pytest.approx((40 * 0.014 + 60 * 0.44 + 10 * 1.32) / 1e6, rel=1e-6)
    url, headers, body = wire.sent[0]
    assert url == deepseek.API_URL
    assert headers["Authorization"] == "Bearer ds-key"
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == deepseek.MAX_OUTPUT_TOKENS and body["temperature"] == 0.2


def test_openai_call(wire):
    wire({**_chat("{}", prompt_tokens=1000, completion_tokens=20), "model": "gpt-4.1-mini-2025"})
    raw = openai.OpenAIProvider().call("hi", "sys", model="gpt-4.1-mini")
    assert raw.model == "gpt-4.1-mini-2025"
    assert raw.usage == {"prompt_tokens": 1000, "completion_tokens": 20}
    assert raw.extras["cost_usd"] == pytest.approx((1000 * 0.40 + 20 * 1.60) / 1e6, rel=1e-6)
    assert wire.sent[0][2]["max_completion_tokens"] == openai.MAX_OUTPUT_TOKENS


def test_openai_cached_tokens(wire):
    wire({"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 60}}})
    assert openai.OpenAIProvider().call("hi", "sys", model="gpt-4.1").usage["cached_tokens"] == 60


def test_missing_key_is_a_provider_error(wire, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ProviderError, match="OPENAI_API_KEY is not set"):
        openai.OpenAIProvider().call("hi", "sys", model="gpt-4.1")
    assert not wire.sent


def test_empty_reply_is_a_provider_error(wire):
    wire(_chat(""))
    with pytest.raises(ProviderError, match="empty reply"):
        deepseek.DeepSeekProvider().call("hi", "sys", model="deepseek-v4-flash")


def test_bad_shape(wire):
    wire({"nope": 1})
    with pytest.raises(ProviderError, match="unexpected DeepSeek response shape"):
        deepseek.DeepSeekProvider().call("hi", "sys", model="deepseek-v4-flash")


def _http_error(code, body):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body.encode()))


def test_http_error_keeps_provider_body(wire):
    wire(_http_error(401, '{"error": {"message": "bad key"}}'))
    with pytest.raises(ProviderError, match=r"OpenAI returned HTTP 401: .*bad key"):
        openai.OpenAIProvider().call("hi", "sys", model="gpt-4.1")


def test_unreachable_and_timeout(wire):
    wire(urllib.error.URLError("refused"))
    with pytest.raises(ProviderError, match=r"couldn't reach DeepSeek \(refused\)"):
        deepseek.DeepSeekProvider().call("hi", "sys", model="m")
    wire(TimeoutError())
    with pytest.raises(ProviderError, match="did not respond within"):
        deepseek.DeepSeekProvider().call("hi", "sys", model="m", timeout=3)


def test_ollama_call_and_errors(wire):
    wire({"response": '{"intent": null}', "prompt_eval_count": 5, "eval_count": 7, "total_duration": 9})
    raw = ollama.OllamaProvider().call("hi", "sys", model="qwen")
    assert raw.text == '{"intent": null}'
    assert raw.usage == {"prompt_eval_count": 5, "eval_count": 7}
    assert raw.extras["duration_ns"] == 9
    assert wire.sent[0][0] == "http://localhost:11434/api/generate"
    assert wire.sent[0][2]["format"] == "json" and wire.sent[0][2]["stream"] is False

    wire(_http_error(404, '{"error": "model not found"}'))
    with pytest.raises(ProviderError, match="HTTP 404: model not found"):
        ollama.OllamaProvider().call("hi", "sys", model="qwen")
    wire(urllib.error.URLError("refused"))
    with pytest.raises(ProviderError, match="is `ollama serve` running"):
        ollama.OllamaProvider().call("hi", "sys", model="qwen")
    wire({"error": "boom"})
    with pytest.raises(ProviderError, match="Ollama error: boom"):
        ollama.OllamaProvider().call("hi", "sys", model="qwen")
