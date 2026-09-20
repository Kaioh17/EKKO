"""One model per settings section. Field defaults are the real code
defaults, so an empty DB section resolves to exactly what the CLI uses.

`locked(topic)` marks a field that needs a code change first: the API
never writes it and always serves the code default. `topic` is the
ekko-ui Help topic id explaining the steps."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from listener.vad_listener import (
    DEFAULT_ACTIVE_WINDOW_S,
    DEFAULT_COMMAND_VERIFY_THRESHOLD,
    DEFAULT_FEEDBACK_TAIL_MS,
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_VAD_THRESHOLD,
    DEFAULT_WAKE_THRESHOLD,
    DEFAULT_WAKE_WORD,
    DEFAULT_WHISPER_MODEL,
)
from routing.matcher import DEFAULT_THRESHOLD as DEFAULT_ROUTING_THRESHOLD


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
    active_window_s: float = Field(DEFAULT_ACTIVE_WINDOW_S, gt=0)
    feedback_tail_ms: int = Field(DEFAULT_FEEDBACK_TAIL_MS, ge=0)
    whisper_model: Literal["tiny", "base", "small", "medium", "large-v3"] = DEFAULT_WHISPER_MODEL
    no_save: bool = False
    no_verify: bool = False
    no_transcribe: bool = False
    no_execute: bool = False
    no_llm_fallback: bool = False
    no_manual_wake: bool = False
    no_hard_stop: bool = False
