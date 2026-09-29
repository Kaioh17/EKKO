"""The shared, provider-agnostic "brain" behind every llm_fallback provider.

Everything that doesn't depend on *which* model answers -- the JSON
schema, `validate_pick()`'s safety re-check, domain/memory/short_memory/
research wiring, logging, and the `attempt_fallback()` orchestration --
lives here. A provider directory (`llm_fallback/gemini/`,
`llm_fallback/claude_code/`, `llm_fallback/ollama/`, ...) holds only a thin
`provider.py` transport call: given a prompt and a system instruction,
return raw text plus usage/latency. See llm_fallback/README.md.
"""

from llm_fallback.brain.core import attempt_fallback  # noqa: E402,F401
from llm_fallback.brain.failover import attempt_with_failover  # noqa: E402,F401
from llm_fallback.brain.outcome import FallbackOutcome  # noqa: E402,F401
from llm_fallback.brain.providers import Provider, RawResponse, load_providers, register_provider  # noqa: E402,F401

# Importing brain/ is what makes every provider selectable: each
# provider.py registers itself at import (see providers.load_providers()).
load_providers()
