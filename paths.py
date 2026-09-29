"""Where ekko reads code-shipped files from (ROOT) and writes user data to
(DATA_DIR). DATA_DIR mirrors the repo layout, so with EKKO_DATA_DIR unset
(dev) every file lands exactly where it always has; the installed app sets
it to the per-user app data dir."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("EKKO_DATA_DIR") or ROOT)


def data(*parts: str) -> Path:
    return DATA_DIR.joinpath(*parts)
