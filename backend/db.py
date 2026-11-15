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

from paths import data

logger = logging.getLogger(__name__)

DB_PATH = data("backend", "ekko.db")

_SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"


# The chat transcript the UI shows. Not the model's memory (that's memory/day_log).
_CHAT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
    "day TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, model TEXT, stats TEXT)",
    "CREATE INDEX IF NOT EXISTS chat_messages_day ON chat_messages (day, id)",
)


@contextmanager
def _connect(path: Path):
    """One transaction on a fresh connection; closes it after (sqlite3's own
    context manager only commits)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        with conn:
            for statement in (_SCHEMA, *_CHAT_SCHEMA):
                conn.execute(statement)
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
            rows = conn.execute(f"SELECT key, value FROM settings WHERE {_IN_SECTION}", _prefix(section)).fetchall()  # noqa: S608 -- constant clause, values bound
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
        conn.execute(f"DELETE FROM settings WHERE {_IN_SECTION}", _prefix(section))  # noqa: S608 -- constant clause, values bound


def add_chat_turn(user: str, reply: str, model: str | None, stats: dict | None, path: Path = DB_PATH) -> None:
    """Saves one exchange as two rows (user, then assistant). Raises sqlite3.Error on failure."""
    now = datetime.datetime.now()
    ts, day = now.isoformat(), now.date().isoformat()
    with _connect(path) as conn:
        conn.executemany(
            "INSERT INTO chat_messages (ts, day, role, text, model, stats) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (ts, day, "user", user, None, None),
                (ts, day, "assistant", reply, model, json.dumps(stats) if stats else None),
            ],
        )


def chat_days(path: Path = DB_PATH) -> list[dict]:
    """[{day, count}], newest day first; [] if the DB is unreachable."""
    try:
        with _connect(path) as conn:
            rows = conn.execute("SELECT day, COUNT(*) FROM chat_messages GROUP BY day ORDER BY day DESC").fetchall()
    except sqlite3.Error as exc:
        logger.warning("chat days read failed: %s", exc)
        return []
    return [{"day": day, "count": count} for day, count in rows]


def chat_day(day: str, path: Path = DB_PATH) -> list[dict]:
    """Every message on `day`, oldest first; [] if the DB is unreachable."""
    try:
        with _connect(path) as conn:
            rows = conn.execute(
                "SELECT id, ts, role, text, model, stats FROM chat_messages WHERE day = ? ORDER BY id", (day,)
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("chat history read failed: %s", exc)
        return []
    return [
        {"id": i, "ts": ts, "role": role, "text": text, "model": model, "stats": json.loads(stats) if stats else None}
        for i, ts, role, text, model, stats in rows
    ]


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

        assert chat_days(db) == [] and chat_day("2000-01-01", db) == []
        add_chat_turn("hi", "hello", "m", {"total_s": 1.0}, db)
        add_chat_turn("again", "yes", None, None, db)
        today = datetime.date.today().isoformat()
        assert chat_days(db) == [{"day": today, "count": 4}]
        rows = chat_day(today, db)
        assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant"]
        assert rows[1]["stats"] == {"total_s": 1.0} and rows[3]["stats"] is None
        assert chat_day("2000-01-01", db) == []
    print("db self-check passed")
