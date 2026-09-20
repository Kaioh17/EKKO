from pydantic import BaseModel


class LlmUsageOut(BaseModel):
    provider: str
    calls: int
    avg_latency_s: float | None  # None = provider doesn't track latency
    tokens: int
    cached_tokens: int
    cost_usd: float | None  # None = free provider


class RoutingOverviewOut(BaseModel):
    recent_scores: list[float]  # oldest first
    match_rate: float | None
    threshold: float
    last_status: str | None  # "matched" | "missing_slot" | "no_match" (-> llm fallback)
    last_intent: str | None


class MemoryCountsOut(BaseModel):
    accepted: int
    rejected: int
    pending: int


class HotkeyOut(BaseModel):
    name: str
    chord: str
    enabled: bool


class GeneralOverviewOut(BaseModel):
    llm_total: LlmUsageOut
    llm_providers: list[LlmUsageOut]
    routing: RoutingOverviewOut
    memory: MemoryCountsOut
    hotkeys: list[HotkeyOut]
    ekko_os: str | None  # env-owned, read-only
    gemini_key_hint: str | None  # last 4 chars only, None = unset
