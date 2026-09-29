from conftest import DATA_DIR


def test_writers_resolve_under_data_dir():
    import paths
    from backend import db
    from llm_fallback.brain.logging import LOGS_ROOT
    from memory import short_term, store
    from routing import host, route

    assert paths.DATA_DIR == DATA_DIR
    for p in (db.DB_PATH, LOGS_ROOT, short_term.DEFAULT_SHORT_MEMORY_PATH, short_term.DEFAULT_LOG_PATH,
              store.DEFAULT_MEMORY_PATH, store.DEFAULT_LOG_PATH, route.DEFAULT_LOG_PATH, host.DOTENV_PATH):
        assert DATA_DIR in p.parents, p


def test_listener_defaults_under_data_dir():
    from listener import defaults

    for p in (defaults.DEFAULT_SAVE_DIR, defaults.DEFAULT_TRANSCRIPT_LOG, defaults.DEFAULT_REFERENCE):
        assert str(p).startswith(str(DATA_DIR)), p


def test_listener_defaults_are_light():
    import subprocess
    import sys

    code = "import sys, listener.defaults, backend.schemas.settings; assert 'torch' not in sys.modules and 'faster_whisper' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=str(__import__('paths').ROOT))
