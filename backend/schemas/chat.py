from pydantic import BaseModel


class ChatIn(BaseModel):
    message: str


class ChatOut(BaseModel):
    reply: str
    model: str | None
