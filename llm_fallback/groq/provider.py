"""GroqProvider -- the thin transport wrapper brain/core.py calls.

POSTs to Groq's OpenAI-compatible /chat/completions with GROQ_API_KEY.
Free tier (rate-limited, no spend cap), so unlike deepseek/openai it has no
budget check and reports no cost. Everything else (schema, validation,
domain/memory/research wiring, logging) lives in llm_fallback/brain/.
"""

import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Importing routing.host loads .env, same as the other API providers.
from routing.host import load_dotenv  # noqa: E402

load_dotenv()

from llm_fallback.brain.providers import NO_SEARCH_NOTE, RawResponse, register_provider  # noqa: E402
from llm_fallback.brain.transport import chat_completion  # noqa: E402

API_KEY_ENV = "GROQ_API_KEY"
API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_TIMEOUT = 60.0
MAX_OUTPUT_TOKENS = 4096  # same sizing as deepseek/openai


def configured_model() -> str:
    from backend.config import get

    return get("llm").groq_model


class GroqProvider:
    name = "groq"

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
        model = model or configured_model()
        text, envelope, latency = chat_completion(
            "Groq", API_URL, API_KEY_ENV, model, system_instruction, prompt, timeout,
            temperature=0.2, max_completion_tokens=MAX_OUTPUT_TOKENS,
        )
        usage = envelope.get("usage") or {}
        return RawResponse(
            text=text,
            model=envelope.get("model", model),
            usage={k: usage[k] for k in ("prompt_tokens", "completion_tokens") if usage.get(k) is not None},
            latency_seconds=latency,
        )


register_provider(GroqProvider())
