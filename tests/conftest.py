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

TOKEN = os.environ["EKKO_API_TOKEN"]
AUTH = {"X-Ekko-Token": TOKEN}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend.main import app

    (DATA_DIR / "backend" / "ekko.db").unlink(missing_ok=True)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c
