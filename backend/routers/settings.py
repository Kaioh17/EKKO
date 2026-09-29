"""UI-editable settings: DB first, code defaults (the model's field
defaults) for anything missing. New section = one model plus one SECTIONS
entry in backend/schemas/settings.py; its routes are generated below.
Reads go through backend/config.py, which the voice pipeline uses too."""

import logging
import sqlite3

from fastapi import APIRouter, Body, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from backend import db, supervisor
from backend.config import load
from backend.schemas.settings import SECTIONS, GeneralSettings, locked_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

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
        raise RequestValidationError(exc.errors(include_context=False)) from None
    try:
        db.put_section(section, merged.model_dump(include=set(changes)))
    except sqlite3.Error as exc:
        logger.error("settings write failed: %s", exc)
        raise HTTPException(503, "settings database unavailable") from exc
    logger.info("%s settings updated: %s", section, sorted(changes))
    _reload_listener(section)
    return merged


def reset[M: BaseModel](section: str, model: type[M]) -> M:
    try:
        db.clear_section(section)
    except sqlite3.Error as exc:
        logger.error("settings reset failed: %s", exc)
        raise HTTPException(503, "settings database unavailable") from exc
    logger.info("%s settings reset to defaults", section)
    _reload_listener(section)
    return model()


# Sections the listener only reads at startup (argparse defaults, the TTS
# voice, the OS that picks intents and handlers); llm is re-read on every use.
_RESTART_ON = {"general", "voice", "system"}


def _reload_listener(section: str) -> None:
    if section in _RESTART_ON:
        supervisor.restart_listener()


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
