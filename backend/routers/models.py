"""First-run model download status, for the app's setup banner."""

from fastapi import APIRouter

import models

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def get_models() -> dict:
    return {**models.state, "missing": [m.name for m in models.missing()]}
