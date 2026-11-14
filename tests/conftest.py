"""Every test runs against a throwaway data dir and a fixed token, set
before any ekko module is imported (paths and the token are read once at
import)."""

import os
import sys
import tempfile
from pathlib import Path

DATA_DIR = Path(tempfile.mkdtemp(prefix="ekko-test-"))
os.environ["EKKO_DATA_DIR"] = str(DATA_DIR)
os.environ["EKKO_API_TOKEN"] = "t" * 64
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

# sounddevice initializes PortAudio at import, which raises on a CI box with no
# audio server; no test records or plays anything, so a stub is enough.
try:
    import sounddevice  # noqa: F401
except Exception:
    from unittest.mock import MagicMock

    sys.modules["sounddevice"] = MagicMock()

TOKEN = os.environ["EKKO_API_TOKEN"]
AUTH = {"X-Ekko-Token": TOKEN}


def reset_db():
    """Delete the test DB; on Windows a background thread from the previous
    test can still hold it for a moment, so retry briefly."""
    import time

    for _ in range(50):
        try:
            (DATA_DIR / "backend" / "ekko.db").unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.1)
    (DATA_DIR / "backend" / "ekko.db").unlink(missing_ok=True)  # still locked: raise


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend.main import app

    reset_db()
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c
