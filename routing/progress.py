"""Live progress steps: "what is ekko doing right now", for any process.

A step is one line the UI (chat today, voice later) shows while a command
runs:  running -> done | warn | failed | skipped, with an optional detail
("yes", "no", "already open") and the milliseconds it took.

    from routing import progress

    with progress.track("app.running", "Is Spotify running?") as s:
        s.done("yes") if running else s.warn("no")      # or just leave: done
    progress.emit("app.open", "Opening Spotify", "skipped", "already open")

Where the events go is not this module's business:
  - in-process: whoever set `sink` (backend/routers/chat.py does, per turn)
  - in a handler subprocess: stdout lines `STEP: {json}` when routing/execute.py
    started it with EKKO_STEPS=1; execute() strips them from stdout and hands
    them to `sink`, so a handler never needs to know who is listening
  - neither: nothing happens, so adding a step costs a handler nothing

Usage:
    python routing/progress.py          # run the self-check
"""

import contextvars
import json
import os
import time
from contextlib import contextmanager
from typing import Callable

STEP_PREFIX = "STEP: "
ENV_FLAG = "EKKO_STEPS"
STATES = ("running", "done", "warn", "failed", "skipped")

# Set by whoever wants the events (a chat turn, the voice listener). Same
# shape as llm_fallback.brain.transport.delta_sink.
sink: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar("progress_sink", default=None)


def emit(id: str, label: str, state: str, detail: str = "", ms: int | None = None) -> None:
    assert state in STATES, state
    event = {"id": id, "label": label, "state": state, "detail": detail, "ms": ms}
    if (fn := sink.get()) is not None:
        fn(event)
    elif os.environ.get(ENV_FLAG):
        print(STEP_PREFIX + json.dumps(event), flush=True)


def parse_line(line: str) -> dict | None:
    """The event in a `STEP: {json}` stdout line, or None for any other line."""
    if not line.startswith(STEP_PREFIX):
        return None
    try:
        event = json.loads(line[len(STEP_PREFIX):])
    except ValueError:
        return None
    return event if isinstance(event, dict) and event.get("state") in STATES and event.get("id") else None


class Step:
    def __init__(self, id: str, label: str) -> None:
        self.id, self.label, self._start, self.finished = id, label, time.perf_counter(), False
        emit(id, label, "running")

    def _end(self, state: str, detail: str) -> None:
        if not self.finished:  # first outcome wins; the context manager's default can't override it
            self.finished = True
            emit(self.id, self.label, state, detail, round((time.perf_counter() - self._start) * 1000))

    def finish(self, ok: bool, detail: str = "") -> None: self._end("done" if ok else "failed", detail)  # when success is a bool
    def done(self, detail: str = "") -> None: self._end("done", detail)
    def warn(self, detail: str = "") -> None: self._end("warn", detail)    # a "no" that isn't an error
    def fail(self, detail: str = "") -> None: self._end("failed", detail)
    def skip(self, detail: str = "") -> None: self._end("skipped", detail)


@contextmanager
def track(id: str, label: str):
    """running on entry; done on a clean exit, failed (and re-raised) on an exception,
    unless the body already picked an outcome."""
    step = Step(id, label)
    try:
        yield step
    except BaseException as exc:
        step.fail(str(exc)[:80])
        raise
    step.done()


if __name__ == "__main__":
    got: list[dict] = []
    token = sink.set(got.append)
    try:
        with track("a", "A") as s:
            s.warn("no")
        with track("b", "B"):
            pass
        try:
            with track("c", "C"):
                raise ValueError("boom")
        except ValueError:
            pass
        emit("d", "D", "skipped", "n/a")
    finally:
        sink.reset(token)
    assert [(e["id"], e["state"]) for e in got] == [
        ("a", "running"), ("a", "warn"), ("b", "running"), ("b", "done"),
        ("c", "running"), ("c", "failed"), ("d", "skipped"),
    ], got
    assert got[5]["detail"] == "boom" and isinstance(got[1]["ms"], int)
    assert parse_line(STEP_PREFIX + json.dumps(got[1])) == got[1]
    assert parse_line("Opened Spotify.") is None and parse_line(STEP_PREFIX + "{bad") is None
    print("progress self-check passed")
