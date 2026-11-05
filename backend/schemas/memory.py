from pydantic import BaseModel, Field


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
