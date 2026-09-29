"""One model per settings section. Field defaults are the real code
defaults, so an empty DB section resolves to exactly what the CLI uses.

`locked(topic)` marks a field that needs a code change first: the API
never writes it and always serves the code default. `topic` is the
ekko-ui Help topic id explaining the steps."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from listener.defaults import (
    DEFAULT_ACTIVE_WINDOW_S,
    DEFAULT_COMMAND_MIN_SILENCE_MS,
    DEFAULT_COMMAND_VERIFY_THRESHOLD,
    DEFAULT_FEEDBACK_TAIL_MS,
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_VAD_THRESHOLD,
    DEFAULT_WAKE_THRESHOLD,
    DEFAULT_WAKE_WORD,
    DEFAULT_WHISPER_MODEL,
)
from llm_fallback.catalog import PAID_PROVIDERS, PROVIDERS
from routing.defaults import DEFAULT_THRESHOLD as DEFAULT_ROUTING_THRESHOLD


def locked(topic: str) -> dict:
    return {"json_schema_extra": {"locked": topic}}


def locked_fields(model: type[BaseModel]) -> dict[str, str]:
    """{field: help topic} for every locked field on `model`."""
    return {
        name: info.json_schema_extra["locked"]
        for name, info in model.model_fields.items()
        if isinstance(info.json_schema_extra, dict) and "locked" in info.json_schema_extra
    }


class GeneralSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vad_threshold: float = Field(DEFAULT_VAD_THRESHOLD, ge=0, le=1)
    wake_word: str = Field(DEFAULT_WAKE_WORD, min_length=1, **locked("wake-word"))
    wake_threshold: float = Field(DEFAULT_WAKE_THRESHOLD, ge=0, le=1)
    verify_threshold: float | None = Field(None, ge=0, le=1)  # None = auto, per clip length
    command_verify_threshold: float = Field(DEFAULT_COMMAND_VERIFY_THRESHOLD, ge=0, le=1)
    routing_threshold: float = Field(DEFAULT_ROUTING_THRESHOLD, ge=0, le=1)
    min_silence_ms: int = Field(DEFAULT_MIN_SILENCE_MS, ge=0)
    command_min_silence_ms: int = Field(DEFAULT_COMMAND_MIN_SILENCE_MS, ge=0)
    active_window_s: float = Field(DEFAULT_ACTIVE_WINDOW_S, gt=0)
    feedback_tail_ms: int = Field(DEFAULT_FEEDBACK_TAIL_MS, ge=0)
    whisper_model: Literal["auto", "tiny", "base", "small", "medium", "large-v3"] = DEFAULT_WHISPER_MODEL
    no_save: bool = False
    no_verify: bool = False
    no_transcribe: bool = False
    no_execute: bool = False
    no_llm_fallback: bool = False
    no_manual_wake: bool = False
    no_hard_stop: bool = False


Provider = Literal[*PROVIDERS]
PaidProvider = Literal[*PAID_PROVIDERS]


class LlmSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Provider = "gemini"  # where the failover chain starts
    failover_enabled: bool = True
    failover_order: list[Provider] = Field(
        default_factory=lambda: list(PROVIDERS), min_length=1
    )
    failover_cooldown_s: float = Field(120.0, ge=0)
    research_enabled: bool = True
    openai_model: str = Field("gpt-4.1-mini", min_length=1)
    claude_api_model: str = Field("claude-haiku-4-5", min_length=1)
    budget_usd: dict[PaidProvider, float] = Field(
        default_factory=lambda: dict.fromkeys(PAID_PROVIDERS, 2.0)
    )

    @model_validator(mode="after")
    def _check(self):
        if len(set(self.failover_order)) != len(self.failover_order):
            raise ValueError("failover_order has duplicates")
        if self.provider not in self.failover_order:
            raise ValueError("provider must be in failover_order")
        if any(v < 0 for v in self.budget_usd.values()):
            raise ValueError("budgets must be >= 0")
        return self


class VoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tts_engine: Literal["piper", "openai"] = "piper"
    openai_tts_model: str = Field("gpt-4o-mini-tts", min_length=1)
    openai_tts_voice: str = Field("coral", min_length=1)
    openai_tts_instructions: str = ""


class SystemSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    os: Literal["auto", "windows", "linux", "mac"] = "auto"


SECTIONS: dict[str, type[BaseModel]] = {
    "general": GeneralSettings,
    "llm": LlmSettings,
    "voice": VoiceSettings,
    "system": SystemSettings,
}
