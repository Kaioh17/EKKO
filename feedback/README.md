# Feedback

Spoken feedback for the listening pipeline, synthesized at runtime
with Piper TTS via `speech.py` — no pre-recorded audio files, no
separate "dynamic response" system to build later. One code path:
a known system event and, once something produces genuinely dynamic
text, arbitrary novel text both go through the same `speak()` call.

Executing the matched intent lives in `routing/`, not here — this
package only says things. That split is deliberate: `feedback/` imports
nothing from the rest of the repo, so the dependency direction stays
one-way (`listener` → `feedback`, never back).

Wired into `listener/vad_listener.py` at two moments:

1. **At the wake word**, once speaker verification succeeds:
   `generic_ack`, the "go ahead, I'm listening" cue Alexa/Google
   Assistant give. Fires before active listening starts waiting for a
   command, rather than after the command has been said.
2. **After the command**, once it's been routed and run: whichever key
   `routing/execute.py` picked for the outcome — `command_confirmed`,
   `already_done`, `not_understood`, `missing_slot`, or `error`.

Both go through `_say()` in `vad_listener.py`, which **gates the mic
shut while EKKO talks**. Without that, the assistant's own voice gets
captured and transcribed as your next command; it used to, see `MicGate`
there. Disable all of it with `--no-feedback`.

## Setup: download a voice model

Piper needs a voice model that isn't shipped with the `piper-tts` pip
package. Download a `.onnx` + `.onnx.json` pair (default here is
`en_GB-alba-medium`; `en_GB-alan-medium` is also in `models/` and was
the previous default — switch with `--model`, or change
`DEFAULT_MODEL_PATH` in `speech.py` to move EKKO's voice everywhere at
once) from
https://huggingface.co/rhasspy/piper-voices/tree/main and place both
files under `feedback/models/`. That's a manual step, not a
code task, same as this project's other pretrained-model downloads
(see `voice_auth/auedio.md`). `load_voice()` raises a clear error
pointing back here if the model isn't found; `vad_listener.py`
catches that at startup and continues without audio feedback rather
than failing the whole pipeline.

## Usage

```powershell
python feedback\speech.py --list                  # show RESPONSES keys and every text variant
python feedback\speech.py --key generic_ack        # speak a random variant of that key
python feedback\speech.py --key command_confirmed --intent open_app --slot app=chrome
                                                    # intent-aware variant, {app} filled in
python feedback\speech.py --text "anything at all" # speak arbitrary text, no mic pipeline needed
```

## Adding a response

Add a key and a *list* of variant texts to `RESPONSES` — just text, no
recording step — and have the caller ask for it by name. Keep the
lookup and rotation logic out of here: `say(voice, key, intent=None,
slots=None)` speaks a key and nothing else, `render()` is what picks
the variant and fills any `{slot}` placeholder. Every key needs at
least one variant; a key with only one entry still works, it's just
never going to sound different call to call.

Want a confirmation that names what happened rather than a generic
"Done."? Add `"<key>:<intent name>"` instead of (or alongside) the
bare key — see `command_confirmed:open_app` and
`command_confirmed:web_search` in `speech.py`. `render()` tries that
combination first and falls back to the bare key when it doesn't
exist, so most keys (and any future intent that doesn't earn its own
phrasing yet) never need one. The intent name is just a string match
against whatever the caller passes as `intent`, not an import from
`routing/`, so this module still doesn't know intents exist.

This used to be a `respond_to_intent(voice, intent=None)` seam, designed
as the place that would inspect a matched intent and pick a key. That job
turned out to belong elsewhere. By the time a key can be chosen you need
both the routing status *and* the handler's exit code, and only
`routing/execute.py` has both, so it does the picking (`response_key()`)
and this module just speaks the answer — `intent`/`slots` ride along
from the caller (`vad_listener.py` passes `bundle.intent`/`bundle.slots`)
purely to pick and fill a variant, not to make a routing decision.
`access_denied` is the one key still unused: nothing speaks on a failed
wake-word verification yet.

## Files

- `speech.py` — `load_voice()`, `speak()`, `render()`, `say()`, `RESPONSES` dict, `--list`/`--key`/`--intent`/`--slot`/`--text` CLI
- `models/` — downloaded Piper voice files (`.onnx` + `.onnx.json`), not committed
