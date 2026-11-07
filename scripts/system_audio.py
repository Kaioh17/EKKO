"""Cross-platform speaker volume, microphone gain and wake-word ducking.

Windows: pycaw (Core Audio).  Linux: pulsectl.  macOS: osascript.
Every OS import is lazy and optional: on an unsupported OS or a missing
library each function logs once and returns None/False, it never raises.

Latency: the OS handle (COM endpoint / Pulse client) is built once per
thread and reused, so call warm() at startup and the hot path is a single
native call. Pulse and COM handles aren't thread-safe, hence per-thread.

Ducking is unconditional on purpose: no "is media playing" query. One
guard only: a second duck() while already ducked doesn't overwrite the
saved level (otherwise a double wake-word would "restore" to 10%).
restore() also runs at interpreter exit so a crash path can't leave the
machine at 10%.

NOTE for callers: duck/restore are in-process state. A routed volume
command runs in a separate process (scripts/<os>/volume_control.*) and
sees the *ducked* level, so restore() before executing a routed intent.

Logs: logger "ekko.audio", one line per op with elapsed ms; failures log
the traceback. The CLI below turns logging on.

Usage:
    python scripts/system_audio.py up|down|mute|unmute|<0-100>|duck|restore|mic
"""

import atexit
import logging
import subprocess
import sys
import threading
import time

log = logging.getLogger("ekko.audio")

STEP = 10  # percent per "up"/"down"
DUCK_LEVEL = 0.10


# ---- backends: float scalars 0..1, kind is "out" (speakers) or "in" (mic) ----

class _Win:
    def __init__(self):
        self._tl = threading.local()

    def _ep(self, kind):
        eps = getattr(self._tl, "eps", None)
        if eps is None:
            import comtypes

            try:
                comtypes.CoInitialize()  # no-op on a thread that's already initialised
            except OSError:
                pass
            eps = self._tl.eps = {}
        if kind not in eps:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            dev = AudioUtilities.GetSpeakers() if kind == "out" else AudioUtilities.GetMicrophone()
            dev = getattr(dev, "_dev", dev)  # newer pycaw wraps the raw COM device
            eps[kind] = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
        return eps[kind]

    def get(self, kind):
        return self._ep(kind).GetMasterVolumeLevelScalar()

    def set(self, kind, v):
        self._ep(kind).SetMasterVolumeLevelScalar(v, None)

    def mute(self, kind, muted):
        self._ep(kind).SetMute(int(muted), None)

    def reset(self):  # default device changed / handle went stale
        self._tl.eps = {}


class _Pulse:
    def __init__(self):
        self._tl = threading.local()

    def _p(self):
        if getattr(self._tl, "p", None) is None:
            import pulsectl

            self._tl.p = pulsectl.Pulse("ekko-audio")
        return self._tl.p

    def _dev(self, kind):
        p = self._p()
        info = p.server_info()
        if kind == "out":
            return p, p.get_sink_by_name(info.default_sink_name)
        return p, p.get_source_by_name(info.default_source_name)

    def get(self, kind):
        p, d = self._dev(kind)
        return p.volume_get_all_chans(d)

    def set(self, kind, v):
        p, d = self._dev(kind)
        p.volume_set_all_chans(d, v)

    def mute(self, kind, muted):
        p, d = self._dev(kind)
        p.mute(d, muted)

    def reset(self):
        p, self._tl.p = getattr(self._tl, "p", None), None
        if p is not None:
            try:
                p.close()
            except Exception:  # noqa: BLE001, S110 - already broken, nothing to salvage
                pass


class _Mac:
    # ponytail: ~50-100 ms per osascript spawn; PyObjC/CoreAudio would be
    # faster but is a heavy dependency. Many mics expose no software input
    # volume on macOS, in which case set("in", ...) fails and is logged.
    @staticmethod
    def _osa(script):
        return subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=3, check=True
        ).stdout.strip()

    def get(self, kind):
        return int(self._osa(f"{'output' if kind == 'out' else 'input'} volume of (get volume settings)")) / 100

    def set(self, kind, v):
        self._osa(f"set volume {'output' if kind == 'out' else 'input'} volume {round(v * 100)}")

    def mute(self, kind, muted):
        if kind == "out":
            self._osa(f"set volume output muted {'true' if muted else 'false'}")

    def reset(self):
        pass


_BACKEND = None
_BACKEND_TRIED = False
_lock = threading.Lock()
_saved: float | None = None  # None = not ducked


def _backend():
    global _BACKEND, _BACKEND_TRIED
    if _BACKEND_TRIED:
        return _BACKEND
    _BACKEND_TRIED = True
    cls = {"win32": _Win, "linux": _Pulse, "darwin": _Mac}.get(sys.platform)
    if cls is None:
        log.warning("audio: unsupported platform %r, all audio ops are no-ops", sys.platform)
        return None
    _BACKEND = cls()
    return _BACKEND


def _run(op, fn):
    """fn(backend) -> value, timed, retried once on a fresh handle. Returns
    None on any failure (logged), never raises."""
    be = _backend()
    if be is None:
        return None
    t0 = time.perf_counter()
    for attempt in (1, 2):
        try:
            out = fn(be)
            log.info("audio %s ok %.1fms%s", op, (time.perf_counter() - t0) * 1000, " (retry)" if attempt == 2 else "")
            return out if out is not None else True
        except Exception:  # noqa: BLE001 - missing lib, no device, stale handle: all "unavailable"
            be.reset()
            if attempt == 2:
                log.exception("audio %s FAILED after %.1fms", op, (time.perf_counter() - t0) * 1000)
    return None


def _clamp(x):
    return max(0.0, min(1.0, x))


# ---- public API (percent in, percent out; None/False = failed) ----

def warm() -> bool:
    """Build the speaker + mic handles now so the first real call is fast.
    Call once per thread that will use this module."""
    return _run("warm", lambda b: (b.get("out"), b.get("in"))) is not None


def get_volume() -> int | None:
    v = _run("get", lambda b: b.get("out"))
    return None if v is None else round(v * 100)


def set_volume(level: int) -> int | None:
    v = _clamp(level / 100)
    return round(v * 100) if _run(f"set {level}", lambda b: b.set("out", v)) else None


def change_volume(delta: int) -> int | None:
    def go(b):
        v = _clamp(b.get("out") + delta / 100)
        b.set("out", v)
        return v

    v = _run(f"step {delta:+d}", go)
    return None if v is None else round(v * 100)


def set_mute(muted: bool) -> bool:
    return _run("mute" if muted else "unmute", lambda b: b.mute("out", muted)) is not None


def mic_to_max() -> bool:
    """Default microphone input volume -> 100%. Call at wake-word time."""
    return _run("mic 100", lambda b: b.set("in", 1.0)) is not None


def duck(level: float = DUCK_LEVEL) -> bool:
    """Save the current speaker level, then force it to `level` (0-1)."""
    global _saved
    with _lock:
        def go(b):
            global _saved
            if _saved is None:  # guard: never overwrite the real level with a ducked one
                _saved = b.get("out")
            b.set("out", level)

        ok = _run("duck", go) is not None
        if ok:
            atexit.register(restore)  # idempotent: restore() is a no-op when not ducked
        return ok


def restore() -> bool:
    """Return the speaker level to what duck() saved. No-op if not ducked."""
    global _saved
    with _lock:
        if _saved is None:
            return True
        saved = _saved
        if _run("restore", lambda b: b.set("out", saved)) is None:
            return False  # keep _saved so a later restore() can retry
        _saved = None
        return True


# ---- CLI: this is the handler scripts/<os>/volume_control.* call ----

def _cli(action: str) -> int:
    """Exit codes follow scripts/README.md: 0 done, 3 already there, 1 failed."""
    before = get_volume()
    if action in ("up", "down"):
        after = change_volume(STEP if action == "up" else -STEP)
    elif action in ("mute", "unmute"):
        return 0 if set_mute(action == "mute") else 1
    elif action == "duck":
        return 0 if duck() else 1
    elif action == "restore":
        return 0 if restore() else 1
    elif action == "mic":
        return 0 if mic_to_max() else 1
    elif action.isdigit():
        after = set_volume(int(action))
    else:
        print(f"unknown action {action!r}", file=sys.stderr)
        return 1
    if after is None:
        print("volume control unavailable", file=sys.stderr)
        return 1
    print(f"Volume {after} percent.")
    print(f"SPEAK: Volume {after} percent.")
    return 3 if before == after else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", stream=sys.stderr)
    raise SystemExit(_cli(sys.argv[1].lower()))
