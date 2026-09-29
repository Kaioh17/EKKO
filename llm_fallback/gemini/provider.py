"""GeminiProvider -- the thin transport wrapper brain/core.py calls.

Everything that isn't "make the API call and hand back raw text" lives in
llm_fallback/brain/ now; this file only ever talks to
llm_fallback/gemini/client.py's generate_content().
"""

import sys
import time
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from llm_fallback.brain.providers import NO_SEARCH_NOTE, RawResponse, register_provider  # noqa: E402
from llm_fallback.gemini.client import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    extract_text,
    generate_content,
)

# Off by default: confirmed against the live API that the `google_search`
# grounding tool has ZERO quota on a pure free-tier (no-billing) key --
# see llm_fallback/gemini/README.md's "Known issue" section. Flip via
# enable_search=True (or --search on the CLI) once a billing account is
# linked.
ENABLE_SEARCH = False


def _grounding_urls(candidate: dict) -> list[str]:
    """Pulls real, already-fetched result URLs out of
    `groundingMetadata.groundingChunks[].web.uri` -- Gemini's search
    grounding tool's own record of what it looked at, preferred over
    whatever the model's own `urls` field claims.
    """
    chunks = candidate.get("groundingMetadata", {}).get("groundingChunks", [])
    urls = []
    for chunk in chunks:
        uri = chunk.get("web", {}).get("uri")
        if isinstance(uri, str):
            urls.append(uri)
    return urls[:3]


class GeminiProvider:
    name = "gemini"
    default_model = DEFAULT_MODEL

    def capability_note(self, enable_search: bool = ENABLE_SEARCH, **kwargs) -> str:
        if enable_search:
            return (
                "It's fine to use Google Search for a factual or current-events "
                "question. `urls` is 0-3 real search result URLs worth reading "
                "further, only when answer is non-null and you actually searched "
                "for it -- never invented, [] otherwise."
            )
        return NO_SEARCH_NOTE

    def call(
        self,
        prompt: str,
        system_instruction: str,
        timeout: float = DEFAULT_TIMEOUT,
        model: str = DEFAULT_MODEL,
        enable_search: bool = ENABLE_SEARCH,
        **kwargs,
    ) -> RawResponse:
        start = time.perf_counter()
        envelope = generate_content(
            prompt,
            system_instruction=system_instruction,
            tools=[{"google_search": {}}] if enable_search else None,
            model=model,
            timeout=timeout,
        )
        latency = round(time.perf_counter() - start, 3)

        candidates = envelope.get("candidates") or []
        grounded_urls = _grounding_urls(candidates[0]) if candidates else []
        return RawResponse(
            text=extract_text(envelope),
            model=model,
            usage=envelope.get("usageMetadata", {}),
            latency_seconds=latency,
            grounded_urls=grounded_urls or None,
        )


register_provider(GeminiProvider())
