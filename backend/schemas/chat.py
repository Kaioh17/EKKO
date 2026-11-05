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


class ChatOut(BaseModel):
    reply: str
    model: str | None
    stats: ChatStats | None = None
