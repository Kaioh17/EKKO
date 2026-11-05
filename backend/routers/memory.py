import logging

from fastapi import APIRouter, HTTPException

from memory import archive, context, day_log, review, store, today_summary
from backend.schemas.memory import (
    AddLineIn,
    ArchiveEntryOut,
    ArchiveOut,
    ContextOut,
    ContextSectionOut,
    DayLogOut,
    DayTurnOut,
    EditLineIn,
    LongTermMemoryOut,
    MemoryLineOut,
    ReviewItemOut,
    ReviewOut,
    ReviewResolveIn,
    ReviewResolveOut,
    TodaySummaryOut,
    TrimOut,
    WindowOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _turn(t: dict) -> DayTurnOut:
    return DayTurnOut(
        ts=t.get("ts") or "",
        source=t.get("source") or "",
        kind=t.get("kind") or "",
        user=t.get("user") or "",
        assistant=t.get("assistant") or "",
        cls=day_log.classify(t),
    )


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
    _in_window, _block, stats = day_log.window(turns)
    return DayLogOut(
        turns=[_turn(t) for t in turns],
        window_hours=day_log.WINDOW_HOURS,
        token_budget=day_log.WINDOW_TOKEN_BUDGET,
        tokens_est=stats["window_tokens_est"],
        truncated_turns=stats["evicted_turns"],
    )


@router.delete("/day-log/turn", status_code=204)
def delete_day_log_turn(ts: str) -> None:
    if not day_log.delete_turn(ts):
        raise HTTPException(404, "no such turn")


@router.post("/day-log/trim")
def trim_day_log() -> TrimOut:
    """Drop the exchanges classified `drop` (failed turns, silence) to free window tokens."""
    result = day_log.trim()
    logger.info("day log trimmed: %s", result)
    return TrimOut(**result)


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


@router.get("/window")
def get_window() -> WindowOut:
    """The live conversation window: what the next LLM call sees, whole."""
    turns, _, stats = day_log.window(day_log.recent_turns())
    return WindowOut(
        turns=[_turn(t) for t in turns],
        tokens_est=stats["window_tokens_est"],
        budget=day_log.WINDOW_TOKEN_BUDGET,
        evicted_turns=stats["evicted_turns"],
    )


@router.get("/today-summary")
def get_today_summary() -> TodaySummaryOut | None:
    s = today_summary.read_today()
    return TodaySummaryOut(**s, pending_turns=len(today_summary.pending_turns())) if s else None


@router.post("/today-summary/refresh")
def refresh_today_summary() -> TodaySummaryOut | None:
    try:
        today_summary.refresh(force=True)
    except Exception as exc:  # noqa: BLE001 -- surface the provider failure to the UI
        logger.warning("today summary refresh failed: %s", exc)
        raise HTTPException(502, f"Summary failed: {exc}") from exc
    return get_today_summary()


@router.delete("/today-summary", status_code=204)
def clear_today_summary() -> None:
    today_summary.clear()
    logger.info("today summary cleared")


@router.get("/review")
def get_review() -> ReviewOut:
    """Instructions in the 24h log that will be lost unless the user saves them."""
    return ReviewOut(items=[ReviewItemOut.model_validate(i, from_attributes=True) for i in review.candidates()])


@router.post("/review/resolve")
def resolve_review(body: ReviewResolveIn) -> ReviewResolveOut:
    try:
        message = review.resolve(body.id, body.action, body.filename, body.folder)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Could not save: {exc}") from exc
    logger.info("review: %s -> %s", body.action, message)
    return ReviewResolveOut(message=message)


@router.get("/context")
def get_context() -> ContextOut:
    """The memory the next message carries (see context.describe)."""
    sections = [
        ContextSectionOut(name=n, text=t.strip(), tokens=day_log.est_tokens(t.strip())) for n, t in context.describe()
    ]
    return ContextOut(sections=sections, total_tokens=sum(s.tokens for s in sections))
