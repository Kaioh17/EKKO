"""The HTTP half every urllib-based provider (deepseek, openai, ollama)
shares: one JSON POST with uniform error handling, plus the OpenAI-style
`chat/completions` round trip that deepseek and openai both speak.
Providers keep only what differs: the request body, usage fields and cost.
"""

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextvars import ContextVar

from llm_fallback.brain.providers import ProviderError


# Set by a caller (backend/routers/chat.py) that wants the reply as it is
# generated. chat_completion() then streams and calls it with each raw text
# delta, and with None when a new model call starts (retry / failover), so the
# listener can drop what it buffered. Unset = one blocking call, as before.
delta_sink: ContextVar[Callable[[str | None], None] | None] = ContextVar("delta_sink", default=None)


def _read_sse(response, sink: Callable[[str | None], None]) -> dict:
    """Fold an OpenAI-style SSE stream back into the non-streaming envelope shape."""
    sink(None)
    text, envelope = [], {}
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[5:])
        envelope["model"] = chunk.get("model", envelope.get("model"))
        envelope["usage"] = chunk.get("usage") or envelope.get("usage")
        for choice in chunk.get("choices") or []:
            delta = (choice.get("delta") or {}).get("content")
            if delta:
                text.append(delta)
                sink(delta)
    envelope["choices"] = [{"message": {"content": "".join(text)}}]
    return envelope


def post_json(
    label: str, url: str, body: dict, timeout: float, headers: dict | None = None, hint: str = "",
    sink: Callable[[str | None], None] | None = None,
) -> tuple[dict, float]:
    """POST `body` as JSON. Returns (decoded reply, seconds). Raises
    ProviderError, worded with `label`, for HTTP errors, an unreachable
    endpoint, a timeout or a reply that isn't JSON. With `sink`, asks for an
    OpenAI-style SSE stream and feeds it to the sink (see delta_sink).
    """
    if sink:
        body = {**body, "stream": True, "stream_options": {"include_usage": True}}
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        # An explicit User-Agent: Cloudflare-fronted APIs (Groq) 403 urllib's default "Python-urllib" (error 1010).
        headers={"Content-Type": "application/json", "User-Agent": "ekko/1.0", **(headers or {})},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = _read_sse(response, sink) if sink else json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(raw).get("error")
        except (json.JSONDecodeError, AttributeError):
            error = None
        raise ProviderError(f"{label} returned HTTP {exc.code}: {error if isinstance(error, str) else raw}") from None
    except urllib.error.URLError as exc:
        raise ProviderError(f"couldn't reach {label} ({exc.reason}){hint}") from None
    except TimeoutError:
        raise ProviderError(f"{label} did not respond within {timeout}s") from None
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{label} response wasn't valid JSON: {exc}") from None
    return envelope, round(time.perf_counter() - start, 3)


def chat_completion(
    label: str, url: str, api_key_env: str, model: str, system: str, prompt: str, timeout: float, **body_extra
) -> tuple[str, dict, float]:
    """One OpenAI-compatible JSON-mode chat call. Returns (reply text,
    full envelope, seconds); an empty reply is a ProviderError so the
    failover chain moves on.
    """
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise ProviderError(f"{api_key_env} is not set -- add it to .env")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        **body_extra,
    }
    envelope, latency = post_json(label, url, body, timeout, {"Authorization": f"Bearer {api_key}"}, sink=delta_sink.get())
    try:
        text = envelope["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"unexpected {label} response shape: {str(envelope)[:200]}") from None
    if not text:
        raise ProviderError(f"{label} returned an empty reply")
    return text, envelope, latency
