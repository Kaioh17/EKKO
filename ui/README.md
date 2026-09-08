# UI

A small terminal-styled popup that mirrors `listener/vad_listener.py`'s
pipeline state while EKKO is active: listening, processing, speaking,
or an error (a failed wake-word voice match, or an outcome other than
a clean match). Purely visual — nothing in the listening pipeline
branches on whether it's there, and `--no-ui` turns it off with no
other effect.

## Why in-process, not a separate program

Spawning a new console/process every time the wake word fires costs a
process creation, a new interpreter, a new conhost — real overhead on
every single interaction. `VoiceUI` instead runs one Tkinter overlay in
a background thread inside the same process as the rest of the
pipeline. The window sits hidden and near-idle (a queue poll every
120ms) until a state change shows it, then redraws at ~30fps only
while visible, and auto-hides itself a beat after returning to idle.
Cross-thread updates go through a plain `queue.Queue` — cheap, no
serialization, no IPC — since Tkinter's own objects may only be
touched from the thread running `mainloop()`.

## Wiring

`listener/vad_listener.py` owns every state transition; this package
just draws whatever it's told:

```python
ui = VoiceUI()
ui.start()                              # once, at listener startup

ui.set_state(VoiceUIState.LISTENING)    # wake word verified, awaiting a command
ui.set_audio_level(rms)                 # from the mic callback, 0.0-1.0
ui.set_transcript(text)                 # once Whisper has a transcript
ui.set_state(VoiceUIState.PROCESSING)   # routing / running the matched handler
ui.set_state(VoiceUIState.SPEAKING)     # set automatically by _say() in vad_listener.py
ui.set_state(VoiceUIState.ERROR)        # access denied, or a non-MATCHED outcome
ui.set_state(VoiceUIState.IDLE)         # back to sleep; popup auto-hides shortly after

ui.show_response(text, timeout_s=20)    # llm_fallback's open-ended answer, or a
                                         # response during an active follow-up session
ui.clear_response()                     # close the response panel before its timeout

ui.stop()                               # at shutdown
```

`set_state()` shows the popup itself for any state other than `IDLE`,
so callers don't need to pair every transition with a `show()`/`hide()`
— see `_say()` in `vad_listener.py`, which sets `SPEAKING` (or an
override, e.g. `ERROR` for "access denied") for the duration of a
response and lets the caller decide what state follows once playback
ends.

`show_response()` grows the popup with a text panel below the normal
content and closes it again after `timeout_s` seconds (a fresh call
resets that clock, which is how `vad_listener.py` keeps a multi-turn
follow-up session landing in the same panel instead of opening a new
one each time — see `_handle_command()`'s `session_until` there). This
module has no idea what an "ambiguous request" or a "session" is,
though — it only knows how to show text for as long as it's told to;
deciding when that applies is entirely the caller's job, same as every
other state here.

All calls are safe from any thread, including the audio callback.

## Preview

```powershell
python -m ui.voice_ui
```

Cycles the popup through every state with simulated audio levels, no
mic or EKKO pipeline required.

## Files

- `voice_ui.py` — `VoiceUI`, `VoiceUIState`, the Tkinter overlay and its `queue.Queue` protocol
