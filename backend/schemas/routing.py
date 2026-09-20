from pydantic import BaseModel


class SlotOut(BaseModel):
    name: str
    type: str
    values: list[str] = []
    triggers: list[str] = []


class IntentOut(BaseModel):
    key: str
    examples: list[str]
    handler: str
    slots: list[SlotOut] = []


class ThresholdOut(BaseModel):
    threshold: float


class DomainOut(BaseModel):
    key: str
    threshold: float
    continuity_boost: float
