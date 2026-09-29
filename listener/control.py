"""The listener's link to backend/supervisor.py when it runs as the
backend's child (EKKO_SUPERVISED=1):

- A thread long-polls GET /api/listener/next for UI commands
  ({"cmd": "wake"}) and sets the same event the manual-wake hotkey sets,
  so no OS-level hotkey (or root on Linux) is needed. HTTP, never a stdin
  read: on Windows a thread blocked on a stdin pipe deadlocks later native
  DLL loads (torch, onnxruntime) in this process.
- The same thread exits the process if the backend is gone (it was
  killed hard, so it couldn't stop us).
- VoiceUI state changes are mirrored to POST /api/status/push so ekko-ui
  shows live state. Fire-and-forget: a slow or absent backend must never
  stall the audio loop.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request


def supervised() -> bool:
    return os.environ.get("EKKO_SUPERVISED") == "1"


def _request(method: str, path: str, body: dict | None = None, timeout: float = 2.0):
    url, token = os.environ["EKKO_BACKEND_URL"], os.environ["EKKO_API_TOKEN"]
    req = urllib.request.Request(
        f"{url}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Ekko-Token": token},
        method=method,
    )
    return urllib.request.urlopen(req, timeout=timeout)  # loopback URL set by the supervisor


def dispatch(message: dict, events: dict[str, threading.Event]) -> None:
    event = events.get(message.get("cmd"))
    if event is not None:
        event.set()


def _poll_commands(events: dict[str, threading.Event]) -> None:
    from procutil import alive

    parent = os.getppid()
    while True:
        try:
            with _request("GET", "/api/listener/next?timeout=25", timeout=35) as resp:
                if resp.status == 200:
                    dispatch(json.loads(resp.read()), events)
        except (OSError, ValueError):
            if not alive(parent):
                os._exit(0)  # backend is gone; nobody will stop us otherwise
            time.sleep(1)


def start_command_reader(events: dict[str, threading.Event]) -> None:
    threading.Thread(target=_poll_commands, args=(events,), daemon=True, name="ekko-control").start()


def _post_status(state: str) -> None:
    try:
        _request("POST", "/api/status/push", {"state": state}).close()
    except (OSError, KeyError):
        pass


def mirror_status(ui) -> None:
    """Wrap ui.set_state so every state change is also pushed to the backend."""
    original = ui.set_state

    def set_state(state):
        original(state)
        threading.Thread(target=_post_status, args=(state.value,), daemon=True).start()

    ui.set_state = set_state


if __name__ == "__main__":
    wake = threading.Event()
    dispatch({"cmd": "bogus"}, {"wake": wake})
    dispatch({}, {"wake": wake})
    assert not wake.is_set()
    dispatch({"cmd": "wake"}, {"wake": wake})
    assert wake.is_set()
    print("control self-check passed")
