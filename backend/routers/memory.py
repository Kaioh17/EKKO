import logging

from fastapi import APIRouter, HTTPException

from memory import short_term, store
from backend.schemas.memory import LongTermMemoryOut, MemoryLineOut, ShortMemoryOut, WriteShortMemoryIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _out(turn) -> ShortMemoryOut | None:
    return ShortMemoryOut(**vars(turn)) if turn is not None else None


@router.get("/short-term")
def get_short_term() -> ShortMemoryOut | None:
    return _out(short_term.read_short_memory())


@router.get("/short-term/active")
def get_active_short_term() -> ShortMemoryOut | None:
    return _out(short_term.read_active_short_memory())


@router.post("/short-term")
def write_short_term(body: WriteShortMemoryIn) -> ShortMemoryOut | None:
    try:
        short_term.write_short_memory(**body.model_dump())
    except ValueError as exc:
        logger.warning("short-term write rejected: %s", exc)
        raise HTTPException(422, str(exc))
    logger.info("short-term memory written")
    return _out(short_term.read_active_short_memory())


@router.post("/short-term/clear", status_code=204)
def clear_short_term() -> None:
    short_term.clear_short_memory()
    logger.info("short-term memory cleared")


@router.get("/long-term")
def get_long_term() -> LongTermMemoryOut:
    bundle = store.read_memory()
    return LongTermMemoryOut(
        sections={
            section: [MemoryLineOut(date=line.date, text=line.text) for line in lines]
            for section, lines in bundle.sections.items()
        }
    )
