"""OpenAIProvider -- the thin transport wrapper brain/core.py calls.

POSTs to OpenAI's /v1/chat/completions with OPENAI_API_KEY. Pay-as-you-go,
so it shares the spend cap in brain/budget.py with deepseek and claude_api.
Everything else (schema, validation, domain/memory/research wiring,
logging) lives in llm_fallback/brain/.
"""

import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Importing routing.host loads .env, same as gemini/client.py.
from routing.host import load_dotenv  # noqa: E402

load_dotenv()

from llm_fallback.brain.budget import check_budget  # noqa: E402
from llm_fallback.brain.providers import NO_SEARCH_NOTE, RawResponse, register_provider  # noqa: E402
from llm_fallback.brain.transport import chat_completion  # noqa: E402

API_KEY_ENV = "OPENAI_API_KEY"
API_URL = "https://api.openai.com/v1/chat/completions"
# Small and cheap by default (llm.openai_model, same reason as claude_api's
# Haiku default); model names move fast, so re-check
# https://platform.openai.com/docs/models before trusting it.
def configured_model() -> str:
    from backend.config import get

    return get("llm").openai_model


DEFAULT_TIMEOUT = 20.0
# Bounds the worst-case cost of one call; the reply is one small JSON object.
MAX_OUTPUT_TOKENS = 1024

# USD per 1M tokens (input, cached input, output), from OpenAI's published
# pricing. A model missing here is priced at _UNKNOWN_MODEL_PRICE, a
# deliberately high guess, so a newly set EKKO_OPENAI_MODEL over-counts
# against the cap rather than under-counting. Add its real price here.
_PRICING = {
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4o": (2.50, 1.25, 10.00),
}
_UNKNOWN_MODEL_PRICE = (5.00, 2.50, 20.00)


def call_cost_usd(model: str, usage: dict) -> float:
    rate_in, rate_cached, rate_out = _PRICING.get(model, _UNKNOWN_MODEL_PRICE)
    prompt = usage.get("prompt_tokens") or 0
    cached = usage.get("cached_tokens") or 0
    out = usage.get("completion_tokens") or 0
    return (max(prompt - cached, 0) * rate_in + cached * rate_cached + out * rate_out) / 1_000_000


class OpenAIProvider:
    name = "openai"

    @property
    def default_model(self) -> str:
        return configured_model()

    def capability_note(self, **kwargs) -> str:
        return NO_SEARCH_NOTE

    def call(
        self,
        prompt: str,
        system_instruction: str,
        timeout: float = DEFAULT_TIMEOUT,
        model: str | None = None,
        **kwargs,
    ) -> RawResponse:
        check_budget("openai", "OpenAI")
        model = model or configured_model()
        text, envelope, latency = chat_completion(
            "OpenAI", API_URL, API_KEY_ENV, model, system_instruction, prompt, timeout,
            max_completion_tokens=MAX_OUTPUT_TOKENS,
        )
        raw_usage = envelope.get("usage") or {}
        usage = {
            "prompt_tokens": raw_usage.get("prompt_tokens"),
            "completion_tokens": raw_usage.get("completion_tokens"),
            "cached_tokens": (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        }
        usage = {k: v for k, v in usage.items() if v is not None}
        return RawResponse(
            text=text,
            model=envelope.get("model", model),
            usage=usage,
            latency_seconds=latency,
            extras={"cost_usd": round(call_cost_usd(model, usage), 8)},
        )


register_provider(OpenAIProvider())
