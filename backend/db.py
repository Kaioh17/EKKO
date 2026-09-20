"""SQLite settings store. Source of truth for UI-editable settings; callers
fall back to code defaults when a section comes back empty.

Usage:
    python backend/db.py          # run the self-check
"""

import datetime
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent / "ekko.db"

_SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"


@contextmanager
def _connect(path: Path):
    """One transaction on a fresh connection; closes it after (sqlite3's own
    context manager only commits)."""
    conn = sqlite3.connect(path)
    try:
        with conn:
            conn.execute(_SCHEMA)
            yield conn
    finally:
        conn.close()


# Exact prefix match; LIKE would treat "_" in a section name as a wildcard.
_IN_SECTION = "substr(key, 1, ?) = ?"


def _prefix(section: str) -> tuple[int, str]:
    return len(section) + 1, f"{section}."


def get_section(section: str, path: Path = DB_PATH) -> dict:
    """Stored fields for `section`, or {} if the DB is unreachable."""
    try:
        with _connect(path) as conn:
            rows = conn.execute(f"SELECT key, value FROM settings WHERE {_IN_SECTION}", _prefix(section)).fetchall()
    except sqlite3.Error as exc:
        logger.warning("settings read failed, using defaults: %s", exc)
        return {}
    return {key.split(".", 1)[1]: json.loads(value) for key, value in rows}


def put_section(section: str, values: dict, path: Path = DB_PATH) -> None:
    """Upserts `values` under `section`. Raises sqlite3.Error on failure."""
    now = datetime.datetime.now().isoformat()
    with _connect(path) as conn:
        conn.executemany(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [(f"{section}.{k}", json.dumps(v), now) for k, v in values.items()],
        )


def clear_section(section: str, path: Path = DB_PATH) -> None:
    """Drops every stored field in `section` (back to code defaults). Raises sqlite3.Error on failure."""
    with _connect(path) as conn:
        conn.execute(f"DELETE FROM settings WHERE {_IN_SECTION}", _prefix(section))


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        assert get_section("general", db) == {}
        put_section("general", {"vad_threshold": 0.7, "verify_threshold": None}, db)
        put_section("general", {"vad_threshold": 0.8}, db)
        put_section("other", {"vad_threshold": 1}, db)
        assert get_section("general", db) == {"vad_threshold": 0.8, "verify_threshold": None}
        put_section("hotXkeys", {"a": 1}, db)
        assert get_section("hot_keys", db) == {}  # "_" is literal, not a wildcard
        clear_section("general", db)
        assert get_section("general", db) == {}
        assert get_section("other", db) == {"vad_threshold": 1}
        assert get_section("general", Path(tmp) / "missing_dir" / "x.db") == {}
    print("db self-check passed")
