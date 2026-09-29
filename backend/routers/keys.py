"""Set-only API keys. Values live in EKKO_DATA_DIR/.env (the only thing
left in .env) and are never sent back: GET shows whether each key is set
and its last 4 characters, enough to tell two keys apart."""

import os
import re
import tempfile

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import supervisor
from llm_fallback.catalog import KEY_NAMES
from routing.host import DOTENV_PATH

router = APIRouter(prefix="/api/keys", tags=["keys"])


class KeyIn(BaseModel):
    # One printable line, no quotes: anything else could inject a second
    # KEY=value into .env.
    value: str = Field(min_length=1, max_length=512, pattern=r"^[\x21-\x7e]+$")


def _check(name: str) -> None:
    if name not in KEY_NAMES:
        raise HTTPException(404, f"unknown key {name!r}")


def _rewrite(name: str, value: str | None) -> None:
    """Replace (or drop, when value is None) `name`'s line in .env, keeping
    every other line; atomic, owner-only on POSIX."""
    lines = DOTENV_PATH.read_text(encoding="utf-8").splitlines() if DOTENV_PATH.is_file() else []
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    lines = [line for line in lines if not pattern.match(line)]
    if value is not None:
        lines.append(f"{name}={value}")
    DOTENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DOTENV_PATH.parent, prefix=".env.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, DOTENV_PATH)  # mkstemp already created it 0600


@router.get("")
def list_keys() -> dict[str, dict]:
    out = {}
    for name in KEY_NAMES:
        value = os.environ.get(name, "")
        out[name] = {"set": bool(value), "last4": value[-4:] if len(value) > 12 else None}
    return out


@router.put("/{name}", status_code=204)
def set_key(name: str, body: KeyIn) -> None:
    _check(name)
    _rewrite(name, body.value)
    os.environ[name] = body.value
    supervisor.restart_listener()  # the listener inherits this process's environment at spawn


@router.delete("/{name}", status_code=204)
def delete_key(name: str) -> None:
    _check(name)
    _rewrite(name, None)
    os.environ.pop(name, None)
    supervisor.restart_listener()
