"""One model per settings section. Field defaults are the real code
defaults, so an empty DB section resolves to exactly what the CLI uses.

`locked(topic)` marks a field that needs a code change first: the API
never writes it and always serves the code default. `topic` is the
ekko-ui Help topic id explaining the steps."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from listener.defaults import (
    DEFAULT_ACTIVE_WINDOW_S,
    DEFAULT_COMMAND_MIN_SILENCE_MS,
    DEFAULT_COMMAND_VERIFY_THRESHOLD,
    DEFAULT_CUSTOM_WORDS,
    DEFAULT_FEEDBACK_TAIL_MS,
    DEFAULT_MAX_NO_SPEECH_PROB,
    DEFAULT_MIN_AVG_LOGPROB,
    DEFAULT_MIN_COMMAND_MS,
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_PUSH_TO_TALK_HOTKEY,
    DEFAULT_PREROLL_MS,
    DEFAULT_VAD_THRESHOLD,
    DEFAULT_WAKE_THRESHOLD,
    DEFAULT_WAKE_WORD,
    DEFAULT_WORD_CORRECTION_THRESHOLD,
    DEFAULT_WHISPER_DEVICE,
    DEFAULT_WHISPER_MODEL,
)
from listener.hotkey import normalize_chord
from listener.stt_models import MODELS as PARAKEET_MODELS
from llm_fallback.catalog import PAID_PROVIDERS, PROVIDERS
from routing.defaults import DEFAULT_THRESHOLD as DEFAULT_ROUTING_THRESHOLD
from web_scraping.catalog import PROVIDERS as SEARCH_PROVIDERS


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

    @field_validator("push_to_talk_hotkey")
    @classmethod
    def _chord(cls, v: str) -> str:
        return normalize_chord(v)  # ValueError becomes a 422 the UI can show

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
    preroll_ms: int = Field(DEFAULT_PREROLL_MS, ge=0, le=2000)  # audio kept from before speech starts
    min_command_ms: int = Field(DEFAULT_MIN_COMMAND_MS, ge=0)  # shorter segments are clicks, not commands
    # Whisper's own doubt about a transcript: reject above / below these.
    max_no_speech_prob: float = Field(DEFAULT_MAX_NO_SPEECH_PROB, ge=0, le=1)
    min_avg_logprob: float = Field(DEFAULT_MIN_AVG_LOGPROB, le=0)
    input_device: str | None = None  # device name; None = system default
    output_device: str | None = None
    whisper_model: Literal["auto", "tiny", "base", "small", "medium", "large-v3"] = DEFAULT_WHISPER_MODEL
    whisper_device: Literal["auto", "gpu", "cpu"] = DEFAULT_WHISPER_DEVICE
    # auto: Whisper on a GPU, Parakeet on CPU. Parakeet must be downloaded first (/api/stt/models).
    stt_engine: Literal["auto", "whisper", "parakeet"] = "auto"
    parakeet_model: Literal[*PARAKEET_MODELS] = "tdt-0.6b-v2"
    # Spelled this way in transcripts when a close match is heard (listener/text_correct.py).
    custom_words: list[str] = Field(default_factory=lambda: list(DEFAULT_CUSTOM_WORDS))
    word_correction_threshold: float = Field(DEFAULT_WORD_CORRECTION_THRESHOLD, ge=0, le=1)  # 0 = off
    whisper_cpu_threads: int = Field(0, ge=0, le=256)  # 0 = auto, half the logical cores
    # Optional: hold a key, speak, let go. No wake word, no spoken ack, no wait for a pause.
    push_to_talk: bool = False
    push_to_talk_hotkey: str = DEFAULT_PUSH_TO_TALK_HOTKEY  # saved in `keyboard`'s spelling
    # What ekko does after the wake word: say hi, a short chime, or nothing.
    ack_style: Literal["speech", "chime", "none"] = "speech"
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
    gemini_model: str = Field("gemini-3.1-flash-lite", min_length=1)
    groq_model: str = Field("openai/gpt-oss-20b", min_length=1)
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


class SearchSettings(BaseModel):
    """Live web search (web_scraping/). On/off is llm.research_enabled."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal[*SEARCH_PROVIDERS] = "brave"
    max_results: int = Field(5, ge=1, le=10)  # more results = more prompt tokens and, on some plans, more credits


class VoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tts_engine: Literal["piper", "openai", "kokoro", "chattts"] = "piper"
    # Speak an LLM answer sentence by sentence while it is still generating.
    tts_streaming: bool = True
    openai_tts_model: str = Field("gpt-4o-mini-tts", min_length=1)
    openai_tts_voice: str = Field("coral", min_length=1)
    openai_tts_instructions: str = ""
    kokoro_voice: str = Field("af_heart", min_length=1)
    chattts_seed: int = Field(2, ge=0)  # picks the speaker; same seed, same voice


class SystemSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    os: Literal["auto", "windows", "linux", "mac"] = "auto"


SECTIONS: dict[str, type[BaseModel]] = {
    "general": GeneralSettings,
    "llm": LlmSettings,
    "voice": VoiceSettings,
    "search": SearchSettings,
    "system": SystemSettings,
}
