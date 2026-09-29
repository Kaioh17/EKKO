"""Runs the voice listener as a child of the backend and keeps it alive.

Commands for the listener (the UI's Wake button) are queued here and
fetched by the listener's long-poll of GET /api/listener/next (see
listener/control.py). Not stdin: on Windows a thread blocked reading a
stdin pipe deadlocks every later native DLL load in that process (torch,
numpy, onnxruntime), so std handles are never used as a channel.

A crash restarts the listener with capped exponential backoff. This
replaces the old Windows-only restart.ps1 and synthetic-hotkey paths.

Usage:
    sup = Supervisor([sys.executable, "-m", "listener.vad_listener"])
    sup.start(); sup.send({"cmd": "wake"}); sup.stop()
"""

import logging
import queue
import subprocess
import threading
import time
from pathlib import Path

from paths import data

LOG_PATH = data("backend", "logs", "listener.log")
MAX_LOG_BYTES = 10 * 1024 * 1024

logger = logging.getLogger(__name__)


def _open_log(path: Path = LOG_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if path.exists() and path.stat().st_size > MAX_LOG_BYTES else "ab"  # ponytail: truncate, no rotation
    return open(path, mode)


class Supervisor:
    def __init__(self, argv: list[str], cwd: str | None = None, backoff_s: float = 1.0, max_backoff_s: float = 60.0):
        self.argv, self.cwd = argv, cwd
        self.backoff_s, self.max_backoff_s = backoff_s, max_backoff_s
        self.restarts = 0
        self.commands: queue.Queue[dict] = queue.Queue(maxsize=16)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def pid(self) -> int | None:
        proc = self._proc
        return proc.pid if proc and proc.poll() is None else None

    @property
    def running(self) -> bool:
        return self.pid is not None

    def start(self) -> None:
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ekko-supervisor")
        self._thread.start()

    def _run(self) -> None:
        delay = self.backoff_s
        while not self._stopping.is_set():
            started = time.monotonic()
            with self._lock, _open_log() as log:
                # Children inherit os.environ as it is now, so API key edits
                # reach the next listener. Output goes to a file, never our
                # own std handles: under the GUI app those can be invalid.
                self._proc = subprocess.Popen(
                    self.argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=self.cwd
                )
            logger.info("listener started (pid %s), log: %s", self._proc.pid, LOG_PATH)
            code = self._proc.wait()
            if self._stopping.is_set():
                break
            # A run that lasted a while was healthy: reset the backoff.
            delay = self.backoff_s if time.monotonic() - started > 30 else min(delay * 2, self.max_backoff_s)
            self.restarts += 1
            logger.warning("listener exited with %s, restarting in %.1fs", code, delay)
            self._stopping.wait(delay)

    def send(self, message: dict) -> bool:
        """Queue a command for the listener. False if it isn't running or is backed up."""
        if not self.running:
            return False
        try:
            self.commands.put_nowait(message)
        except queue.Full:
            return False
        return True

    def restart(self) -> None:
        """Kill the child; the run loop starts a fresh one (picks up new settings)."""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._thread:
            self._thread.join(timeout)


# The backend's one listener, set by backend/main.py's lifespan when it
# runs as the real app (None under tests or with --no-listener).
listener: Supervisor | None = None


def restart_listener() -> None:
    """Restart the listener so it re-reads settings and keys; a no-op when none runs."""
    if listener is not None:
        listener.restart()
