"""UI-editable settings: DB first, code defaults (the model's field
defaults) for anything missing. New section = one model in
backend/schemas/settings.py + one entry in SECTIONS; its routes are
generated below."""

import logging
import sqlite3

from fastapi import APIRouter, Body, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from backend import db
from backend.schemas.settings import GeneralSettings, locked_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

SECTIONS: dict[str, type[BaseModel]] = {
    "general": GeneralSettings,
}


def load[M: BaseModel](section: str, model: type[M]) -> M:
    lock = locked_fields(model)
    stored = {k: v for k, v in db.get_section(section).items() if k not in lock}
    try:
        return model(**stored)
    except ValidationError as exc:
        # Stale/hand-edited row: default just the bad fields, keep the rest.
        bad = {e["loc"][0] for e in exc.errors() if e["loc"]}
        logger.warning("ignoring invalid stored %s settings: %s", section, sorted(bad))
        return model(**{k: v for k, v in stored.items() if k not in bad})


def save[M: BaseModel](section: str, model: type[M], changes: dict) -> M:
    lock = locked_fields(model)
    if blocked := sorted(set(changes) & set(lock)):
        raise HTTPException(
            409,
            {
                "message": f"{', '.join(blocked)} can't be changed from the UI; it needs a code change first.",
                "help": {k: lock[k] for k in blocked},
            },
        )
    try:
        merged = model(**{**load(section, model).model_dump(), **changes})
    except ValidationError as exc:
        raise RequestValidationError(exc.errors())
    try:
        db.put_section(section, merged.model_dump(include=set(changes)))
    except sqlite3.Error as exc:
        logger.error("settings write failed: %s", exc)
        raise HTTPException(503, "settings database unavailable")
    logger.info("%s settings updated: %s", section, sorted(changes))
    return merged


def reset[M: BaseModel](section: str, model: type[M]) -> M:
    try:
        db.clear_section(section)
    except sqlite3.Error as exc:
        logger.error("settings reset failed: %s", exc)
        raise HTTPException(503, "settings database unavailable")
    logger.info("%s settings reset to defaults", section)
    return model()


def load_general() -> GeneralSettings:
    return load("general", GeneralSettings)


def _register(section: str, model: type[BaseModel]) -> None:
    @router.get(f"/{section}", response_model=model, name=f"get_{section}")
    def get_():
        return load(section, model)

    @router.get(f"/{section}/locked", name=f"get_{section}_locked")
    def get_locked() -> dict[str, str]:
        """{field: help topic} the UI must render read-only with a Help link."""
        return locked_fields(model)

    @router.patch(f"/{section}", response_model=model, name=f"patch_{section}")
    def patch_(changes: dict = Body(...)):
        return save(section, model, changes)

    @router.post(f"/{section}/reset", response_model=model, name=f"reset_{section}")
    def reset_():
        return reset(section, model)


for _section, _model in SECTIONS.items():
    _register(_section, _model)
