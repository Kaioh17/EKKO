"""DeepSeekProvider -- the thin transport wrapper brain/core.py calls.

POSTs to DeepSeek's OpenAI-compatible /chat/completions endpoint. Everything
else (schema, validation, domain/memory/research wiring, logging) lives in
llm_fallback/brain/. The one thing this provider adds is a hard spend cap:
DeepSeek is pay-as-you-go, so every call's cost is computed from its usage
and reported in `extras["cost_usd"]` (which brain/logging.py sums into
logs/deepseek/usage_summary.json), and call() refuses to start once that
running total reaches the cap. See brain/budget.py.
"""

import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Same reasoning as gemini/client.py: importing routing.host loads .env, so
# DEEPSEEK_API_KEY works with no per-caller load_dotenv().
from routing.host import load_dotenv  # noqa: E402

load_dotenv()

from llm_fallback.brain.budget import check_budget  # noqa: E402
from llm_fallback.brain.providers import NO_SEARCH_NOTE, RawResponse, register_provider  # noqa: E402
from llm_fallback.brain.transport import chat_completion  # noqa: E402

API_KEY_ENV = "DEEPSEEK_API_KEY"
API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT = 30.0
# Bounds the worst-case cost of one call. The reply is one small JSON
# object, so this is generous; it also caps any reasoning tokens.
MAX_OUTPUT_TOKENS = 1024

# USD per 1M tokens, from DeepSeek's official pricing page. DeepSeek bills
# less off-peak, but this deliberately always uses the *peak* rate: the
# budget check is a safety cap, so it should over-count rather than
# under-count. Re-verify before trusting -- API prices change.
_PRICING = {
    "deepseek-v4-flash": {"cache_hit": 0.014, "cache_miss": 0.44, "output": 1.32},
    "deepseek-v4-pro": {"cache_hit": 0.044, "cache_miss": 1.32, "output": 3.96},
}


def call_cost_usd(model: str, usage: dict) -> float:
    """Peak-rate cost of one call. Unknown models are priced as the more
    expensive `deepseek-v4-pro` rather than as free.
    """
    rates = _PRICING.get(model, _PRICING["deepseek-v4-pro"])
    hit = usage.get("prompt_cache_hit_tokens") or 0
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max((usage.get("prompt_tokens") or 0) - hit, 0)
    out = usage.get("completion_tokens") or 0
    return (hit * rates["cache_hit"] + miss * rates["cache_miss"] + out * rates["output"]) / 1_000_000


class DeepSeekProvider:
    name = "deepseek"
    default_model = DEFAULT_MODEL

    def capability_note(self, **kwargs) -> str:
        return NO_SEARCH_NOTE

    def call(
        self,
        prompt: str,
        system_instruction: str,
        timeout: float = DEFAULT_TIMEOUT,
        model: str = DEFAULT_MODEL,
        **kwargs,
    ) -> RawResponse:
        check_budget("deepseek", "DeepSeek")
        text, envelope, latency = chat_completion(
            "DeepSeek", API_URL, API_KEY_ENV, model, system_instruction, prompt, timeout,
            temperature=0.2, max_tokens=MAX_OUTPUT_TOKENS, stream=False,
        )
        usage = envelope.get("usage") or {}
        return RawResponse(
            text=text,
            model=envelope.get("model", model),
            usage={
                k: usage.get(k)
                for k in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
                if usage.get(k) is not None
            },
            latency_seconds=latency,
            extras={"cost_usd": round(call_cost_usd(model, usage), 8)},
        )


register_provider(DeepSeekProvider())
