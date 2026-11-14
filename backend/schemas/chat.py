from pydantic import BaseModel, field_validator

from llm_fallback.catalog import PROVIDERS


class ChatIn(BaseModel):
    message: str
    # None = auto: the configured provider, with its failover chain.
    provider: str | None = None

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str | None) -> str | None:
        if value is not None and value not in PROVIDERS:
            raise ValueError(f"unknown provider {value!r}")
        return value


class TokensOut(BaseModel):
    input: int | None = None
    output: int | None = None
    cached: int | None = None  # part of `input` served from the provider's prompt cache


def _first(usage: dict, *keys: str) -> int | None:
    return next((int(usage[k]) for k in keys if isinstance(usage.get(k), (int, float))), None)


def tokens_from_usage(usage: dict) -> TokensOut | None:
    """One shape for every provider's usage dict (OpenAI/DeepSeek, Anthropic
    and the claude CLI, Gemini, Ollama all name these differently)."""
    if not usage:
        return None
    cached = _first(usage, "prompt_cache_hit_tokens", "cached_tokens", "cache_read_input_tokens", "cachedContentTokenCount")
    inp = _first(usage, "prompt_tokens", "promptTokenCount", "prompt_eval_count")
    if inp is None and "input_tokens" in usage:  # Anthropic counts cached input separately
        inp = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    out = _first(usage, "completion_tokens", "output_tokens", "candidatesTokenCount", "eval_count")
    return TokensOut(input=inp, output=out, cached=cached)


class BudgetOut(BaseModel):
    spent_usd: float
    cap_usd: float


class ChatStats(BaseModel):
    """The analytics line under a reply. Every field is optional so a new
    signal is one more field here and one more row in the UI."""

    handled_by: str  # "router" (deterministic match, no LLM) | "llm" | "none"
    total_s: float  # wall clock for the whole turn, as the user waited
    provider: str | None = None
    model: str | None = None
    llm_s: float | None = None  # the LLM call alone
    tokens: TokensOut | None = None
    cost_usd: float | None = None
    budget: BudgetOut | None = None  # pay-as-you-go providers only
    failover_from: list[str] = []  # providers that failed before this one answered
    web_search: bool = False  # the provider's own search / grounding
    web_research: bool = False  # EKKO's research pipeline fed the prompt
    sources: int = 0  # links attached to the answer
    recall_hits: int = 0  # archive days fetched
    domain: str | None = None
    truncated: bool = False  # reply hit the output cap; complete sentences kept
    context_turns: int | None = None  # conversation window sent with the prompt
    context_tokens: int | None = None
    error: str | None = None


class ResearchOffer(BaseModel):
    """Sources the answer found; opened in Brave only if the user says yes."""

    query: str
    urls: list[str]


class ChatOut(BaseModel):
    reply: str
    model: str | None
    stats: ChatStats | None = None
    research_offer: ResearchOffer | None = None
