"""Model weights ekko needs but doesn't ship in the repo or the installer
(large, or trained by the author). Fetched once into DATA_DIR/models on
first run, checksum-verified, then never touched again.

Usage:
    python models.py            # list what's present, download what's missing
"""

import hashlib
import logging
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from paths import ROOT, data

logger = logging.getLogger(__name__)

RELEASE = "https://github.com/Kaioh17/EKKO/releases/download/models-v1"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alba/medium"


@dataclass(frozen=True)
class Model:
    name: str
    url: str
    sha256: str
    legacy: str  # where a dev checkout keeps it, relative to the repo root


MODELS = (
    Model("hey_ekko.onnx", f"{RELEASE}/hey_ekko.onnx",
          "b84a25fb4a40a749bb989e9a90d530991847d81afb6d60cee44c859a66eceedd", "listener/models/hey_ekko.onnx"),
    Model("en_GB-alba-medium.onnx", f"{PIPER}/en_GB-alba-medium.onnx",
          "401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b", "feedback/models/en_GB-alba-medium.onnx"),
    Model("en_GB-alba-medium.onnx.json", f"{PIPER}/en_GB-alba-medium.onnx.json",
          "aa965a2f02ecced632c2694e1fc72bbff6d65f265fab567ca945918c73dd89f4", "feedback/models/en_GB-alba-medium.onnx.json"),
)

# Read by GET /api/models so the app can show first-run progress.
state = {"phase": "idle", "current": None, "error": None}  # phase: idle | downloading | ready | failed


class ModelError(RuntimeError):
    pass


def path(name: str) -> Path:
    """Where `name` is: DATA_DIR/models first, then a dev checkout's old
    location; otherwise where it will be downloaded to."""
    target = data("models", name)
    if target.is_file():
        return target
    legacy = next((ROOT / m.legacy for m in MODELS if m.name == name), None)
    return legacy if legacy is not None and legacy.is_file() else target


def missing() -> list[Model]:
    return [m for m in MODELS if not path(m.name).is_file()]


def _download(model: Model) -> None:
    target = data("models", model.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(model.url, timeout=60) as resp:  # noqa: S310 -- https, pinned URLs
            while chunk := resp.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != model.sha256:
            raise ModelError(f"{model.name}: checksum mismatch, refusing to use the download")
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)


def ensure() -> None:
    """Download whatever is missing. Raises ModelError; state tracks progress."""
    todo = missing()
    if not todo:
        state.update(phase="ready", current=None, error=None)
        return
    try:
        for model in todo:
            state.update(phase="downloading", current=model.name, error=None)
            logger.info("downloading %s", model.name)
            _download(model)
    except (OSError, ModelError) as exc:
        state.update(phase="failed", error=str(exc))
        raise ModelError(str(exc)) from exc
    state.update(phase="ready", current=None, error=None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for m in MODELS:
        print(f"{'ok     ' if path(m.name).is_file() else 'MISSING'} {m.name}")
    ensure()
    print("all models present")
