from typing import Literal

from pydantic import BaseModel, Field

from memory.review import ReviewAction


class MemoryLineOut(BaseModel):
    date: str
    text: str


class LongTermMemoryOut(BaseModel):
    sections: dict[str, list[MemoryLineOut]]


class AddLineIn(BaseModel):
    section: str
    text: str = Field(max_length=500)


class EditLineIn(BaseModel):
    section: str
    index: int = Field(ge=0)
    expected: str  # the text the UI saw at that index; a mismatch is a 409
    text: str = Field(max_length=500)


class DayTurnOut(BaseModel):
    ts: str
    source: str
    kind: str
    user: str
    assistant: str
    cls: Literal["drop", "ephemeral", "keep"]  # see memory.day_log.classify


class DayLogOut(BaseModel):
    turns: list[DayTurnOut]
    window_hours: float
    token_budget: int
    tokens_est: int  # what the next prompt's conversation window carries
    truncated_turns: int  # turns outside that window (covered by today's summary)


class ArchiveEntryOut(BaseModel):
    key: str
    date: str
    summary: str
    topics: list[str]
    turns: int
    details: str


class ArchiveOut(BaseModel):
    entries: list[ArchiveEntryOut]
    keys_sent: int  # how many keys ride along with every prompt
    key_cap: int


class WindowOut(BaseModel):
    turns: list[DayTurnOut]  # oldest first, whole
    tokens_est: int
    budget: int
    evicted_turns: int  # last-24h turns that no longer fit


class TodaySummaryOut(BaseModel):
    date: str
    summary: str
    topics: list[str] = []
    covered_until: str
    updated_at: str
    pending_turns: int = 0  # evicted from the window, not folded in yet


class TrimOut(BaseModel):
    removed: int
    tokens_freed: int


class ReviewItemOut(BaseModel):
    id: str  # ts of the newest version; send it back to resolve
    title: str
    preview: str
    filename: str  # suggested file name
    expires_at: str  # when the newest version leaves the 24h log
    versions: int


class ReviewOut(BaseModel):
    items: list[ReviewItemOut]


class ReviewResolveIn(BaseModel):
    id: str
    action: ReviewAction
    filename: str | None = Field(default=None, max_length=100)
    folder: str | None = Field(default=None, max_length=260)  # blank = ~/ekko-sandbox


class ReviewResolveOut(BaseModel):
    message: str


class ContextSectionOut(BaseModel):
    name: str
    text: str
    tokens: int


class ContextOut(BaseModel):
    sections: list[ContextSectionOut]
    total_tokens: int
