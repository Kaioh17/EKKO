import logging

from fastapi import APIRouter, HTTPException

from memory import archive, day_log, short_term, store
from backend.schemas.memory import (
    AddLineIn,
    ArchiveEntryOut,
    ArchiveOut,
    DayLogOut,
    DayTurnOut,
    EditLineIn,
    LongTermMemoryOut,
    MemoryLineOut,
    ShortMemoryOut,
    WriteShortMemoryIn,
)

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
        raise HTTPException(422, str(exc)) from exc
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


def _long_term() -> LongTermMemoryOut:
    return get_long_term()


def _line_error(exc: Exception) -> HTTPException:
    return HTTPException(409 if isinstance(exc, LookupError) else 422, str(exc))


@router.post("/long-term/lines", status_code=201)
def add_long_term_line(body: AddLineIn) -> LongTermMemoryOut:
    try:
        store.add_line(body.section, body.text)
    except ValueError as exc:
        raise _line_error(exc) from exc
    logger.info("long-term memory line added to %s", body.section)
    return _long_term()


@router.put("/long-term/lines")
def edit_long_term_line(body: EditLineIn) -> LongTermMemoryOut:
    try:
        store.edit_line(body.section, body.index, body.expected, body.text)
    except (ValueError, LookupError) as exc:
        raise _line_error(exc) from exc
    logger.info("long-term memory line edited in %s", body.section)
    return _long_term()


@router.delete("/long-term/lines")
def delete_long_term_line(section: str, index: int, expected: str) -> LongTermMemoryOut:
    try:
        store.delete_line(section, index, expected)
    except (ValueError, LookupError) as exc:
        raise _line_error(exc) from exc
    logger.info("long-term memory line deleted from %s", section)
    return _long_term()


@router.get("/day-log")
def get_day_log() -> DayLogOut:
    turns = day_log.recent_turns()
    _block, stats = day_log.render_block(turns)
    return DayLogOut(
        turns=[DayTurnOut(**{k: t.get(k, "") for k in ("ts", "source", "kind", "user", "assistant")}) for t in turns],
        window_hours=day_log.WINDOW_HOURS,
        token_budget=day_log.TOKEN_BUDGET,
        tokens_est=stats["day_tokens_est"],
        truncated_turns=stats["truncated_turns"],
    )


@router.delete("/day-log/turn", status_code=204)
def delete_day_log_turn(ts: str) -> None:
    if not day_log.delete_turn(ts):
        raise HTTPException(404, "no such turn")


@router.delete("/day-log", status_code=204)
def clear_day_log() -> None:
    day_log.clear_all()
    logger.info("24h conversation log cleared")


@router.get("/archive")
def get_archive() -> ArchiveOut:
    raw = archive.read_archive()
    entries = [
        ArchiveEntryOut(
            key=key,
            date=key[:10],
            summary=str(v.get("summary", "")),
            topics=[str(t) for t in v.get("topics", [])] if isinstance(v.get("topics"), list) else [],
            turns=int(v.get("turns", 0) or 0),
            details=str(v.get("details", "")),
        )
        for key, v in sorted(raw.items(), reverse=True)
        if isinstance(v, dict)
    ]
    return ArchiveOut(entries=entries, keys_sent=min(len(entries), archive.KEY_CAP), key_cap=archive.KEY_CAP)


@router.delete("/archive", status_code=204)
def delete_archive_entry(key: str) -> None:
    if not archive.delete_entry(key):
        raise HTTPException(404, "no such archive entry")
    logger.info("archive entry deleted: %s", key)
