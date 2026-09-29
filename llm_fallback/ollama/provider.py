"""OllamaProvider -- the thin transport wrapper brain/core.py calls.

POSTs to a local Ollama server's /api/generate. Everything else (schema,
validation, domain/memory/research wiring, logging) lives in
llm_fallback/brain/ now.
"""

import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from llm_fallback.brain.providers import NO_SEARCH_NOTE, ProviderError, RawResponse, register_provider  # noqa: E402
from llm_fallback.brain.transport import post_json  # noqa: E402

DEFAULT_HOST = "http://localhost:11434"
# qwen2.5:7b-instruct-q4_K_M got every re-tested case right at comparable
# warm-call latency (~1.5s) against this machine's hardware (RTX 4070
# Laptop, 8GB VRAM) -- see llm_fallback/ollama's former README.md /
# "Known issue" section, since folded into ollama_delta.md's framing
# advice. Must be pulled first: `ollama pull qwen2.5:7b-instruct-q4_K_M`.
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"
# Measured, not guessed: the *first* call against a freshly pulled model
# pays a one-time cold-load cost paging weights into VRAM (order of 20s+
# on this machine's hardware) before warm calls drop to ~1.3-2s. A tight
# timeout here reads as "Ollama hung" when it's actually just loading.
DEFAULT_TIMEOUT = 60.0
# Ollama unloads a model from VRAM after 5 minutes idle by default, which
# would put every first call after a pause back through the cold-load
# cost DEFAULT_TIMEOUT's comment describes.
DEFAULT_KEEP_ALIVE = "30m"


class OllamaProvider:
    name = "ollama"
    default_model = DEFAULT_MODEL

    def capability_note(self, **kwargs) -> str:
        return NO_SEARCH_NOTE

    def call(
        self,
        prompt: str,
        system_instruction: str,
        timeout: float = DEFAULT_TIMEOUT,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        keep_alive: str = DEFAULT_KEEP_ALIVE,
        **kwargs,
    ) -> RawResponse:
        envelope, latency = post_json(
            "Ollama",
            f"{host}/api/generate",
            {
                "model": model,
                "system": system_instruction,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "keep_alive": keep_alive,
                "options": {"temperature": 0.2},
            },
            timeout,
            hint=f" at {host} -- is `ollama serve` running?",
        )

        if envelope.get("error"):
            raise ProviderError(f"Ollama error: {envelope['error']}")

        return RawResponse(
            text=envelope.get("response", ""),
            model=model,
            usage={
                "prompt_eval_count": envelope.get("prompt_eval_count"),
                "eval_count": envelope.get("eval_count"),
            },
            latency_seconds=latency,
            extras={
                "duration_ns": envelope.get("total_duration"),
                "load_duration_ns": envelope.get("load_duration"),
            },
        )


register_provider(OllamaProvider())
