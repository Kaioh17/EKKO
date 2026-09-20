from fastapi import APIRouter

from routing import config as routing_config
from routing import domains as routing_domains
from backend.routers.settings import load_general
from backend.schemas.routing import DomainOut, IntentOut, SlotOut, ThresholdOut

router = APIRouter(prefix="/api/routing", tags=["routing"])


@router.get("/intents")
def get_intents() -> list[IntentOut]:
    cfg = routing_config.load_config()
    return [
        IntentOut(
            key=intent.key,
            examples=list(intent.examples),
            handler=intent.handler,
            slots=[
                SlotOut(name=s.name, type=s.type, values=list(s.values), triggers=list(s.triggers))
                for s in intent.slots
            ],
        )
        for intent in cfg.intents
    ]


@router.get("/threshold")
def get_threshold() -> ThresholdOut:
    return ThresholdOut(threshold=load_general().routing_threshold)


@router.get("/domains")
def get_domains() -> list[DomainOut]:
    registry = routing_domains.load_registry()
    return [
        DomainOut(key=key, threshold=spec.threshold, continuity_boost=spec.continuity_boost)
        for key, spec in registry.items()
    ]
