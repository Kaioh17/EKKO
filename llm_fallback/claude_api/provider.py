"""ClaudeApiProvider -- the thin transport wrapper brain/core.py calls.

Calls the Anthropic Messages API through the official `anthropic` SDK,
authenticated by ANTHROPIC_API_KEY (or an `ant auth login` profile). Not to
be confused with claude_code/, which spawns the `claude -p` CLI against a
subscription: this one is pay-as-you-go, so it shares the spend cap in
brain/budget.py with deepseek and openai. Everything else (schema,
validation, domain/memory/research wiring, logging) lives in
llm_fallback/brain/.
"""

import sys
import time
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Importing routing.host loads .env, same as gemini/client.py.
from routing.host import load_dotenv  # noqa: E402

load_dotenv()

from llm_fallback.brain.budget import check_budget  # noqa: E402
from llm_fallback.brain.providers import NO_SEARCH_NOTE, ProviderError, RawResponse, register_provider  # noqa: E402

# Haiku, matching claude_code's default: this is a short classification /
# one-line-answer call on a live voice path, where latency and cost matter
# more than depth. Set EKKO_CLAUDE_API_MODEL (e.g. claude-sonnet-5) to
# change it.
def configured_model() -> str:
    from backend.config import get

    return get("llm").claude_api_model


DEFAULT_TIMEOUT = 20.0
# Bounds the worst-case cost of one call; the reply is one small JSON object.
MAX_OUTPUT_TOKENS = 1024

# USD per 1M tokens (input, output), from Anthropic's published pricing.
# Cache reads bill at 0.1x input, cache writes at 1.25x. Unknown models are
# priced as the most expensive listed, never as free. Re-verify before
# trusting -- API prices change.
_PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}
_UNKNOWN_MODEL_PRICE = (10.00, 50.00)


def call_cost_usd(model: str, usage: dict) -> float:
    rate_in, rate_out = _PRICING.get(model, _UNKNOWN_MODEL_PRICE)
    uncached = usage.get("input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    write = usage.get("cache_creation_input_tokens") or 0
    out = usage.get("output_tokens") or 0
    return (uncached * rate_in + read * rate_in * 0.1 + write * rate_in * 1.25 + out * rate_out) / 1_000_000


class ClaudeApiProvider:
    name = "claude_api"

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
        check_budget("claude_api", "Claude API")
        model = model or configured_model()
        try:
            import anthropic
        except ImportError:
            raise ProviderError("the `anthropic` package isn't installed -- pip install anthropic") from None

        # max_retries=0: brain/failover.py owns retry/handoff, so a slow SDK
        # retry loop would only delay moving on to the next provider.
        client = anthropic.Anthropic(timeout=timeout, max_retries=0)
        start = time.perf_counter()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system_instruction,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APITimeoutError:
            raise ProviderError(f"Claude API did not respond within {timeout}s") from None
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude API returned HTTP {exc.status_code}: {exc.message}") from None
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"couldn't reach the Claude API ({exc})") from None
        except anthropic.AnthropicError as exc:  # e.g. no credentials configured
            raise ProviderError(str(exc)) from None
        latency = round(time.perf_counter() - start, 3)

        text = "".join(block.text for block in response.content if block.type == "text")
        if response.stop_reason == "refusal" or not text:
            raise ProviderError(f"Claude API returned no text (stop_reason={response.stop_reason})")

        usage = {
            k: getattr(response.usage, k, None)
            for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        }
        usage = {k: v for k, v in usage.items() if v is not None}
        return RawResponse(
            text=text,
            model=response.model,
            usage=usage,
            latency_seconds=latency,
            extras={"cost_usd": round(call_cost_usd(model, usage), 8)},
        )


register_provider(ClaudeApiProvider())
