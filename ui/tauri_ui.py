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
from enum import Enum

from listener import control

SESSION_DEFAULT_TIMEOUT_S = 20.0


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

    def show_response(self, text: str, timeout_s: float = SESSION_DEFAULT_TIMEOUT_S):
        self._send("/api/avatar", {"type": "response", "text": text, "timeout_s": timeout_s})

    def clear_response(self):
        self._send("/api/avatar", {"type": "clear_response"})

    # The mic level and explicit show/hide drove the Tk window; the avatar
    # shows itself from state alone.
    def set_audio_level(self, level: float):
        pass

    def show(self):
        pass

    def hide(self):
        pass


if __name__ == "__main__":
    ui = TauriUI()
    assert not ui._enabled  # no EKKO_SUPERVISED here: calls must be harmless
    ui.start(); ui.set_state(VoiceUIState.LISTENING); ui.show_response("hi"); ui.stop()
    assert ui._q.empty()
    print("tauri_ui self-check passed")
