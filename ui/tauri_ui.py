"""The listener's on-screen avatar, drawn by the Tauri app (ekko-ui's
"avatar" window) instead of Tkinter. Same calls as ui/voice_ui.py's
VoiceUI, but nothing is drawn here: state, transcript and responses are
posted to the backend, which broadcasts them on /ws/status.

Only active when the listener runs under the backend (EKKO_SUPERVISED=1,
which provides the backend URL and token); otherwise every call is a
no-op. ui/voice_ui.py is untouched but no longer imported by the pipeline.
"""

import queue
import threading
import time
from enum import Enum

from listener import control

SESSION_DEFAULT_TIMEOUT_S = 20.0
LEVEL_INTERVAL_S = 0.05


class VoiceUIState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


class TauriUI:
    def __init__(self):
        self._q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._enabled = control.supervised()
        self._last_level = 0.0

    def start(self):
        if self._enabled:
            threading.Thread(target=self._pump, daemon=True, name="ekko-avatar").start()

    def stop(self):
        self.set_state(VoiceUIState.IDLE)

    # One worker, so messages reach the avatar in the order they were sent.
    def _pump(self):
        while True:
            path, body = self._q.get()
            try:
                control._request("POST", path, body).close()
            except (OSError, KeyError):
                pass  # backend slow or gone: never stall the audio loop

    def _send(self, path: str, body: dict):
        if self._enabled:
            self._q.put((path, body))

    def set_state(self, state: VoiceUIState):
        self._send("/api/status/push", {"state": state.value})

    def set_transcript(self, text: str):
        self._send("/api/avatar", {"type": "transcript", "text": text})

    def set_meta(self, domain: str | None, model: str | None):
        """The matched domain expert (None = general) and the model in use /
        that answered (None = none did). The avatar formats the label."""
        self._send("/api/avatar", {"type": "meta", "domain": domain or "", "model": model or ""})

    def show_response(self, text: str, timeout_s: float = SESSION_DEFAULT_TIMEOUT_S):
        self._send("/api/avatar", {"type": "response", "text": text, "timeout_s": timeout_s})

    def show_offer(self, query: str, urls: list[str], timeout_s: float):
        """A Yes/No card; Yes is handled by the avatar window itself."""
        self._send("/api/avatar", {"type": "offer", "query": query, "urls": urls, "timeout_s": timeout_s})

    def clear_response(self):
        self._send("/api/avatar", {"type": "clear_response"})

    def set_audio_level(self, level: float):
        # Called per mic block (~30-100/s): cap at ~20/s and drop rather than
        # queue behind slower posts, so the bars never lag the voice.
        now = time.monotonic()
        if now - self._last_level < LEVEL_INTERVAL_S or self._q.qsize() > 2:
            return
        self._last_level = now
        self._send("/api/avatar", {"type": "level", "level": max(0.0, min(1.0, level))})

    def show(self):
        pass

    def hide(self):
        pass


if __name__ == "__main__":
    ui = TauriUI()
    assert not ui._enabled  # no EKKO_SUPERVISED here: calls must be harmless
    ui.start()
    ui.set_state(VoiceUIState.LISTENING)
    ui.show_response("hi")
    ui.stop()
    assert ui._q.empty()

    ui._enabled = True  # queue only: _pump is never started here
    ui.set_audio_level(2.0)  # clamped to 1.0
    ui.set_audio_level(0.5)  # inside LEVEL_INTERVAL_S: dropped
    assert ui._q.qsize() == 1 and ui._q.get() == ("/api/avatar", {"type": "level", "level": 1.0})
    ui._last_level = 0.0
    for _ in range(3):
        ui._q.put(("/x", {}))  # backed up: levels are dropped, not queued behind
    ui.set_audio_level(0.5)
    assert ui._q.qsize() == 3
    while not ui._q.empty():
        ui._q.get()
    ui.set_meta(None, None)
    ui.set_meta("finance", "gemini")
    assert ui._q.get()[1] == {"type": "meta", "domain": "", "model": ""}
    assert ui._q.get()[1] == {"type": "meta", "domain": "finance", "model": "gemini"}
    print("tauri_ui self-check passed")
