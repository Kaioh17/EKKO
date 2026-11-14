"""One model per settings section. Field defaults are the real code
defaults, so an empty DB section resolves to exactly what the CLI uses.

`locked(topic)` marks a field that needs a code change first: the API
never writes it and always serves the code default. `topic` is the
ekko-ui Help topic id explaining the steps."""

from typing import Literal
from urllib.parse import urlsplit

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
    # Who "play ..." / "queue ..." goes to when the request names no player.
    # auto: Spotify if installed, else Apple Music. An explicit "on apple music" always wins.
    preferred_music_platform: Literal["auto", "spotify", "apple_music"] = "auto"
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


BRIEFING_SECTION_IDS = ("system", "news", "stocks")  # a new briefing feature = a new id here + a builder in scripts/daily_briefing.py
BRIEFING_METRIC_IDS = ("cpu", "ram", "disk", "battery", "gpu", "network")

# The editable half of each LLM prompt. The JSON reply format is appended in
# code (scripts/daily_briefing.py), so editing these can't break parsing.
NEWS_PROMPT = (
    "For each Hacker News story title you are given, write ONE short, natural, spoken-style "
    "sentence conveying what the story is about -- based ONLY on its title, no invented specifics "
    "(no numbers, names, or claims the title doesn't already state or strongly imply). If the title "
    "is already self-explanatory, a light rephrasing that reads naturally aloud is enough; don't pad it."
)
# Per ticker: why it moved. Shown on screen and kept for the "explain the
# watchlist" follow-up, never read aloud unprompted.
STOCKS_PROMPT = (
    "For each stock you are given (with its price move and recent news), write one or two short "
    "sentences saying why it likely moved, based ONLY on the given news -- no invented facts, no "
    "financial advice framed as certainty. If nothing in the news explains the move, write exactly: "
    "No clear reason found."
)

# The narrator's own persona for a voice briefing (the per-call data, the
# length target and the JSON reply format are added in code).
BRIEFING_PROMPT = (
    "You are EKKO, delivering the user's morning briefing out loud. Speak in a warm, natural, "
    "spoken style: no markdown, no lists, no headings, no emoji. Work only from the data you are "
    "given and never invent facts, numbers or quotes. Tech news: go beyond the headline, say what "
    "happened, why it matters and what people are saying, using the article text and comments. "
    "Watchlist: read the values exactly as given (up or down, and by how much) and add NO "
    "commentary or reasons for any ticker. System health was already announced: only mention it "
    "again if something is flagged, and then in one short clause. Open with a brief greeting and "
    "close without asking any question; EKKO asks its own follow-up."
)
NARRATION_WORDS = {"short": "about 60 words", "regular": "about 150 words", "long": "about 300 words"}


class BriefingSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Literal[*BRIEFING_SECTION_IDS]
    enabled: bool = True


def _unique(items: list[str], upper: bool = False) -> list[str]:
    cleaned = (i.strip().upper() if upper else i.strip() for i in items)
    return list(dict.fromkeys(i for i in cleaned if i))


class BriefingSettings(BaseModel):
    """The daily briefing (scripts/daily_briefing.py). Order and on/off of
    `sections` drive voice, chat and the preview alike."""

    model_config = ConfigDict(extra="forbid")

    # plain: no LLM and no voice at all, the briefing only appears on screen.
    # llm: voice is one streamed narration written by the LLM (chat/preview
    # keep one short call per section).
    mode: Literal["plain", "llm"] = "llm"
    narration_length: Literal["short", "regular", "long"] = "regular"
    # Voice only. True = the old detached window: per-section narration calls
    # and its own TTS per section. Kept as a fallback, not the default.
    legacy_window: bool = False
    deliver: Literal["both", "sandbox", "voice"] = "both"  # sandbox = the on-screen cards
    sections: list[BriefingSection] = Field(default_factory=lambda: [BriefingSection(id=i) for i in BRIEFING_SECTION_IDS])

    # System health. The thresholds are shared with the "system diagnosis" command.
    system_metrics: list[Literal[*BRIEFING_METRIC_IDS]] = Field(default_factory=lambda: list(BRIEFING_METRIC_IDS))
    cpu_high_percent: int = Field(90, ge=1, le=100)
    ram_high_percent: int = Field(90, ge=1, le=100)
    disk_free_low_percent: int = Field(10, ge=1, le=100)
    battery_low_percent: int = Field(20, ge=1, le=100)

    # News (Hacker News search, one query per topic, merged and ranked by points).
    news_topics: list[str] = Field(default_factory=lambda: ["AI", "open source", "new tools"])
    news_window_hours: int = Field(24, ge=1, le=168)
    max_news_items: int = Field(10, ge=1, le=50)
    audible_news_count: int = Field(2, ge=0, le=10)  # stories narrated aloud
    chat_news_items: int = Field(5, ge=1, le=50)
    news_timeout_s: float = Field(8.0, ge=1, le=60)
    open_tabs: bool = True
    tab_news_count: int = Field(2, ge=0, le=10)  # top stories opened as tabs
    extra_tabs: list[str] = Field(default_factory=list)  # fixed pages opened every briefing

    # Stocks (Yahoo Finance symbols; ticker_names is what's spoken instead of "GC=F").
    tickers: list[str] = Field(default_factory=lambda: ["SPY", "GC=F"])
    ticker_names: dict[str, str] = Field(default_factory=lambda: {"GC=F": "gold"})
    stock_headlines: int = Field(3, ge=0, le=10)  # headlines fed to the LLM per ticker
    stock_timeout_s: float = Field(10.0, ge=1, le=60)
    # Reasons use Yahoo's free headlines. True also runs a web search per
    # ticker with no headlines (spends Brave/Tavily credits).
    reasons_use_search: bool = False

    # LLM narration. None = the provider chain from the LLM settings; models are set there too.
    llm_provider: Provider | None = None
    llm_timeout_s: float = Field(45.0, ge=1, le=120)
    briefing_system_prompt: str = Field(BRIEFING_PROMPT, min_length=1)
    news_prompt: str = Field(NEWS_PROMPT, min_length=1)  # chat/preview/legacy per-story sentence
    stocks_prompt: str = Field(STOCKS_PROMPT, min_length=1)  # why each ticker moved

    # What gets said. The first three are also used by the PowerShell/Linux handler.
    speak_system_ok: str = "System's fine."
    speak_system_alert: str = "Heads up,"
    speak_handoff: str = "Pulling up your briefing now."
    speak_news_single: str = "Top story:"
    speak_news_intro: str = "Here's what's happening:"
    speak_stocks_single: str = "On your watchlist:"
    speak_stocks_intro: str = "Here's your stock watchlist:"
    speak_watchlist_followup: str = "Do you want me to explain why the watch list looks this way?"
    speak_joiner: str = "Also,"

    @field_validator("news_topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        return _unique(v)

    @field_validator("tickers")
    @classmethod
    def _tickers(cls, v: list[str]) -> list[str]:
        return _unique(v, upper=True)

    @field_validator("extra_tabs")
    @classmethod
    def _tabs(cls, v: list[str]) -> list[str]:
        tabs = _unique(v)
        if bad := [t for t in tabs if urlsplit(t).scheme not in ("http", "https") or not urlsplit(t).netloc]:
            raise ValueError(f"not an http(s) address: {bad[0]}")
        return tabs

    @field_validator("sections")
    @classmethod
    def _sections(cls, v: list[BriefingSection]) -> list[BriefingSection]:
        seen = list(dict.fromkeys(s.id for s in v))
        if len(seen) != len(v):
            raise ValueError("sections has duplicates")
        return v + [BriefingSection(id=i) for i in BRIEFING_SECTION_IDS if i not in seen]


SECTIONS: dict[str, type[BaseModel]] = {
    "general": GeneralSettings,
    "llm": LlmSettings,
    "voice": VoiceSettings,
    "search": SearchSettings,
    "system": SystemSettings,
    "briefing": BriefingSettings,
}
