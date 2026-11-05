"""Audio devices (PyAudio) and default microphone input volume (pycaw, Windows only).

PyAudio can list and open devices but cannot change the system volume, so
volume goes through the Windows Core Audio endpoint. Both imports are lazy
and optional: a missing library or no audio hardware reports
"unavailable" instead of breaking the backend."""

import os


def _pyaudio():
    try:
        import pyaudio

        return pyaudio
    except ImportError:
        return None


def list_devices() -> dict:
    """{"available", "input": [...], "output": [...], "default_input", "default_output"}.
    Devices are {"name", "index"}; names are what gets saved (indexes shift when
    hardware is plugged in). Only the default host API is listed so the same
    physical device doesn't appear once per Windows host API."""
    pa_mod = _pyaudio()
    out = {"available": False, "input": [], "output": [], "default_input": None, "default_output": None}
    if pa_mod is None:
        return out
    pa = pa_mod.PyAudio()
    try:
        host = pa.get_default_host_api_info()["index"]
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d["hostApi"] != host:
                continue
            for kind, ch in (("input", "maxInputChannels"), ("output", "maxOutputChannels")):
                if d[ch] > 0:
                    out[kind].append({"name": d["name"], "index": i})
        for kind, getter in (("input", pa.get_default_input_device_info), ("output", pa.get_default_output_device_info)):
            try:
                out[f"default_{kind}"] = getter()["name"]
            except OSError:  # no default device of this kind
                pass
        out["available"] = True
    finally:
        pa.terminate()
    return out


def find_device(name: str, kind: str) -> int | None:
    """PortAudio index of the saved device `name`, or None if it's unplugged."""
    return next((d["index"] for d in list_devices()[kind] if d["name"] == name), None)


def _endpoint():
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    mic = AudioUtilities.GetMicrophone()
    # newer pycaw may wrap the device; older versions return the raw COM device.
    dev = getattr(mic, "_dev", mic)
    return dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)


def get_volume() -> dict | None:
    """{"level": 0-100, "muted": bool} of the default microphone, None if unsupported."""
    if os.name != "nt":
        return None
    try:
        ep = _endpoint()
        return {"level": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}
    except Exception:
        return None


def set_volume(level: int | None = None, muted: bool | None = None) -> dict | None:
    if os.name != "nt":
        return None
    try:
        ep = _endpoint()
        if level is not None:
            ep.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100, None)
        if muted is not None:
            ep.SetMute(int(muted), None)
    except Exception:
        return None
    return get_volume()
