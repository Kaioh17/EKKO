import json
import os

from backend import runtime
from version import VERSION


def test_write_read_remove(tmp_path):
    path = tmp_path / "runtime.json"
    runtime.write(8123, "abc", path)
    expected = {"port": 8123, "token": "abc", "pid": os.getpid(), "version": VERSION}
    assert json.loads(path.read_text()) == expected
    assert runtime.read(path) == expected
    runtime.remove(path)
    assert not path.exists()


def test_stale_pid_ignored(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"port": 1, "token": "x", "pid": 2**22 + 7}))
    assert runtime.read(path) is None
