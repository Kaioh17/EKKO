"""The Provider Protocol every `llm_fallback/<provider>/provider.py`
implements, plus a small name -> Provider registry.

A provider's only job is transport: given a fully-built prompt and system
instruction, make the call and hand back raw text plus whatever
usage/latency/model metadata it has. It never touches JSON-schema
parsing, `validate_pick()`, domain composition, memory, research, or
logging -- `brain/core.py` does all of that once, uniformly, regardless
of which provider answered.

Adding a new provider is: write one `provider.py` implementing `Provider`
(usually ~40-80 lines; the urllib ones use brain/transport.py), call
`register_provider()` with it, add its row to llm_fallback/catalog.py
(which feeds the settings schema, the keys API and the Models panel), add
its usage fields to backend/routers/overview.py's _USAGE_FIELDS, and
optionally write a `<name>_delta.md` only if real testing shows that model
needs different prompt wording. tests/test_catalog.py fails if any of
those is missed.
"""

import importlib
from dataclasses import dataclass, field
from typing import Protocol

from llm_fallback.catalog import PROVIDERS


@dataclass(frozen=True)
class RawResponse:
    """What one provider call produces, before any JSON-schema parsing.
    `text` is the model's raw reply (still possibly markdown-fenced,
    still possibly not valid JSON -- brain/parsing.py handles both).
    """

    text: str
    model: str
    usage: dict = field(default_factory=dict)
    latency_seconds: float | None = None
    # Provider-specific accounting with no shared meaning across
    # providers (Claude's session_id/total_cost_usd, Ollama's
    # total_duration_ns) -- carried through to FallbackOutcome.extras
    # untouched.
    extras: dict = field(default_factory=dict)
    # Real, already-fetched URLs a provider's own search/grounding tool
    # returned (e.g. Gemini's groundingMetadata), when it has such a
    # mechanism -- preferred over whatever the model's own `urls` field
    # claims, same "don't trust the model's own claim, use what the tool
    # actually returned" reasoning validate_pick() applies to an intent
    # pick. None when the provider has no such mechanism, or didn't use
    # it this call.
    grounded_urls: list[str] | None = None


class ProviderError(Exception):
    """Any transport failure (missing key, HTTP error, timeout, unreachable
    endpoint, empty or malformed reply). brain/core.py catches it and
    degrades to an outcome with bundle=answer=None.
    """


# The capability_note of every provider that has no search/grounding tool.
NO_SEARCH_NOTE = (
    "You have no search tool for this call -- answer only from "
    "your own knowledge, and say so plainly rather than guessing if "
    "a question needs live/current information you don't have. "
    "Always leave `urls` as []."
)


class ProviderBlocked(Exception):
    """Raised by a provider's call() *before* any request is made when it
    refuses to run on purpose (e.g. DeepSeek's spend cap). Unlike an
    ordinary transport failure, the person should be told why, so
    brain/core.py turns `spoken` into the outcome's answer instead of
    treating it as a silent NO_MATCH.
    """

    def __init__(self, spoken: str):
        super().__init__(spoken)
        self.spoken = spoken


class Provider(Protocol):
    """One provider = one transport. name/default_model are plain
    attributes; call() and capability_note() are the two methods
    brain/core.py relies on.
    """

    name: str
    default_model: str

    def capability_note(self, **kwargs) -> str:
        """The one sentence (or two) telling the model what search/tool
        capability, if any, is available for this specific call --
        threaded into brain/prompt.py's build_prompt(). Providers with a
        static capability (Claude's WebSearch, Ollama's none) can ignore
        kwargs entirely; Gemini's is dynamic on `enable_search`.
        """
        ...

    def call(
        self,
        prompt: str,
        system_instruction: str,
        timeout: float,
        **kwargs,
    ) -> RawResponse:
        """Makes the call and returns a RawResponse. Raises a
        provider-specific exception on any transport failure (missing
        key, timeout, unreachable endpoint, non-zero exit) -- brain/core.py
        catches `Exception` around this call and degrades to an outcome
        with bundle=answer=None rather than propagating, same never-raises
        contract every provider already kept independently.
        """
        ...


_REGISTRY: dict[str, Provider] = {}


def register_provider(provider: Provider) -> None:
    _REGISTRY[provider.name] = provider


def get_provider(name: str) -> Provider:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown provider {name!r}, registered: {sorted(_REGISTRY)}") from None


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def load_providers() -> None:
    """Import every provider module so each registers itself. One that
    fails to import is reported and skipped, so it doesn't take the others
    down, but it is never silent.
    """
    for name in PROVIDERS:
        try:
            importlib.import_module(f"llm_fallback.{name}.provider")
        except ImportError as exc:
            print(f"  [llm_fallback] provider {name!r} unavailable: {exc}")
