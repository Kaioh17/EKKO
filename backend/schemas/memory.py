from pydantic import BaseModel, Field


class ShortMemoryOut(BaseModel):
    prior_question: str
    prior_answer: str
    follow_up: str
    follow_up_answer: str | None = None
    timestamp: str = ""
    status: str = "active"


class WriteShortMemoryIn(BaseModel):
    prior_question: str | None = None
    prior_answer: str | None = None
    follow_up: str | None = None
    follow_up_answer: str | None = None


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
    tokens_est: int  # what the next prompt would carry, after the budget cut
    truncated_turns: int


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
