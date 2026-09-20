from pydantic import BaseModel


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
