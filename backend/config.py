"""Settings reads for everything (backend and voice pipeline): DB first,
the section model's defaults for anything missing. No FastAPI import, so
the pipeline can call get() cheaply on every use and pick up UI edits
without a restart.

Usage:
    from backend.config import get
    get("llm").failover_order
"""

import logging

from pydantic import BaseModel, ValidationError

from backend import db
from backend.schemas.settings import SECTIONS, locked_fields

logger = logging.getLogger(__name__)


def load[M: BaseModel](section: str, model: type[M]) -> M:
    lock = locked_fields(model)
    stored = {k: v for k, v in db.get_section(section).items() if k not in lock}
    try:
        return model(**stored)
    except ValidationError as exc:
        # Stale/hand-edited row: default just the bad fields, keep the rest.
        bad = {e["loc"][0] for e in exc.errors() if e["loc"]}
        logger.warning("ignoring invalid stored %s settings: %s", section, sorted(bad))
        try:
            return model(**{k: v for k, v in stored.items() if k not in bad})
        except ValidationError:  # a cross-field rule still fails: defaults
            return model()


def get(section: str) -> BaseModel:
    return load(section, SECTIONS[section])
