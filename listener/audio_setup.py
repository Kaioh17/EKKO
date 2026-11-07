"""What the listener reads from the app's settings, and the audio devices it
opens. Kept free of torch and Whisper so the backend and the tests can use
it without a 15 second import."""


def settings_defaults() -> dict:
    """argparse defaults from the UI's "general" settings (backend/config.py).
    A key with no matching argparse flag is silently ignored, so every key
    here needs its flag in listener/vad_listener.py's parser."""
    from backend.config import get

    g = get("general")
    return {
        "threshold": g.vad_threshold,
        "wake_threshold": g.wake_threshold,
        "verify_threshold": g.verify_threshold,
        "command_verify_threshold": g.command_verify_threshold,
        "routing_threshold": g.routing_threshold,
        "min_silence_ms": g.min_silence_ms,
        "command_min_silence_ms": g.command_min_silence_ms,
        "active_window": g.active_window_s,
        "feedback_tail_ms": g.feedback_tail_ms,
        "min_command_ms": g.min_command_ms,
        "preroll_ms": g.preroll_ms,
        "max_no_speech_prob": g.max_no_speech_prob,
        "min_avg_logprob": g.min_avg_logprob,
        "input_device": g.input_device,
        "output_device": g.output_device,
        "whisper_model": g.whisper_model,
        "whisper_device": g.whisper_device,
        "whisper_cpu_threads": g.whisper_cpu_threads,
        "stt_engine": g.stt_engine,
        "parakeet_model": g.parakeet_model,
        "custom_words": list(g.custom_words),
        "word_correction_threshold": g.word_correction_threshold,
        "push_to_talk": g.push_to_talk,
        "push_to_talk_hotkey": g.push_to_talk_hotkey,
        "ack_style": g.ack_style,
        "no_save": g.no_save,
        "no_verify": g.no_verify,
        "no_transcribe": g.no_transcribe,
        "no_execute": g.no_execute,
        "no_llm_fallback": g.no_llm_fallback,
        "no_manual_wake": g.no_manual_wake,
        "no_hard_stop": g.no_hard_stop,
    }


def resolve_device(name: str | None, kind: str, devices: list[dict], hostapi: int) -> tuple[int | None, str | None]:
    """(PortAudio index, None) for the saved device `name`; (None, None) when
    nothing is saved (use the system default); (None, reason) when it's
    saved but not plugged in. Only the default host API is searched, like
    backend/audio_control.py's list, so a name means one device. Indexes
    shift when hardware changes, which is why names are what's saved."""
    if not name:
        return None, None
    channels = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(devices):
        if d["name"] == name and d["hostapi"] == hostapi and d[channels] > 0:
            return i, None
    return None, f"{kind} device {name!r} not found, using the system default"


def apply_devices(input_name: str | None, output_name: str | None, sd=None) -> list[str]:
    """Point sounddevice's defaults at the saved devices, so the mic stream
    and every playback in this process (feedback/speech.py) follow them.
    Returns a note per saved device that couldn't be found; those keep the
    system default."""
    if sd is None:
        import sounddevice as sd
    if not input_name and not output_name:
        return []
    devices = list(sd.query_devices())
    hostapi = sd.default.hostapi
    current_in, current_out = sd.default.device[0], sd.default.device[1]
    notes = []
    chosen = []
    for name, kind, current in ((input_name, "input", current_in), (output_name, "output", current_out)):
        idx, reason = resolve_device(name, kind, devices, hostapi)
        if reason:
            notes.append(reason)
        chosen.append(current if idx is None else idx)
    sd.default.device = tuple(chosen)
    return notes
