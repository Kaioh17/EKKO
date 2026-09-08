# Listener

Always-on listening pipeline: Silero VAD catches when someone's
speaking, openWakeWord listens continuously for "hey jarvis" during
that speech. Identity is checked right there, at the wake word
segment itself, against `voice_auth/reference_embedding.pt`, before
anything else happens. A match plays an audio acknowledgment
immediately (see `--no-feedback`) and arms active listening: it waits
(up to `--active-window` seconds, default 10) for a speech segment that
looks like a command and transcribes that one. A non-match on the wake
word drops straight back to idle. This is the same "go ahead, I'm
listening" cue Alexa/Google Assistant give right after their wake
word, rather than only after your whole command has been said.

**The mic is gated shut while that acknowledgment plays** (plus
`--feedback-tail-ms`, default 300, for room decay). Without it EKKO
hears its own greeting and transcribes it back as your command:
`sounddevice`'s input callback runs on its own thread, so blocking the
main loop during playback didn't stop the mic from filling the queue.
Every wake word between 17:15 and 17:36 in `captures/transcripts.jsonl`
logged "Hi Mubaraq, what are we doing today?" as the command instead of
whatever was actually said. Measured: 3.33s of audio at 0.99 peak
amplitude reaching the queue unguarded, zero with the gate. See
`MicGate` in `vad_listener.py`.

**That gate then had a second-order version of the same bug**, which is
why the two paragraphs below exist. Gating for the duration of the
`speak()` call isn't the same as gating until the sound is gone:
`sd.wait()` returns once PortAudio has handed off its last buffer, but
the device still has more queued — measured at **183ms** on this
machine — and the room rings on after that. The fixed 300ms tail
expired mid-decay. What leaked was no longer the whole greeting, just
its last syllable fading out, which Silero read as a speech segment and
Whisper hallucinated into `'Bye.'` and `'Thank you.'` (both still in
`captures/transcripts.jsonl` at 18:20 and 18:21). Their giveaway is the
envelope: 100ms RMS frames of `0.215, 0.091, 0.014, 0.004, 0.004` — a
transient decaying straight to the noise floor, where real speech stays
up across many frames.

So the tail is no longer guessed at. `feedback.speech.speak()` measures
the output stream's latency and returns the time playback *actually*
ends, and `--feedback-tail-ms` is now only the room decay added on top
of that. The mute window scales with the audio automatically, however
long a future response turns out to be, instead of needing a new
constant per phrase.

**And active listening no longer hands the first thing it hears to the
router.** That was the other half of the bug: any segment ended the
turn, so one stray blip cost you your command and EKKO went back to
idle while you were still drawing breath. Candidate segments are now
screened (`_screen_command_segment`) on three things — long enough
(`--min-command-ms`), from the enrolled speaker
(`--command-verify-threshold`), and something Whisper is confident was
actually said (`--max-no-speech-prob`, `--min-avg-logprob`). A segment
that fails is discarded with its reason logged and active listening
simply continues; only `--active-window` elapsing drops back to idle.

The identity check is the layer carrying the weight here, and it's the
one that generalizes: whatever EKKO says, in whatever response gets
added later, at whatever length, it will never score as you. Over the
18:20 captures it split cleanly — enrolled speaker 0.379–0.714, EKKO's
own acknowledgment tail 0.050 and 0.086 — hence a floor as low as 0.25.
It's much looser than `--verify-threshold` on purpose: it isn't
authorizing anything, the wake word already did, it only has to tell you
apart from a TTS voice or a stranger. (It also closes a hole: until now
anyone in the room could issue the command once *your* wake word had
verified.) The Whisper confidence checks are a genuine backstop for junk
that isn't a voice at all, but they can't stand alone — one echo tail
scores `no_speech_prob` 0.323 against a real command's 0.342, so the
distributions interleave and no threshold separates them. Under
`--no-verify` they're all that's left, and some tails will get through.

(An earlier version of this pipeline verified the *command* segment
instead, on the theory that "hey jarvis" was too short/acoustically
narrow for the speaker embedding to be reliable there. `voice_auth`
now has a short-clip reference bucket and calibrated threshold for
exactly that duration, see `voice_auth/verify.py`'s `SHORT_BUCKET`,
just not one calibrated on "hey jarvis" specifically. Re-tune
`--verify-threshold` with `--skip-wake` below, actually saying the
wake word while you do, don't assume the existing default transfers.)

Once a command segment passes screening, it's transcribed locally
with `faster-whisper` (`small` model by default), appended to
`captures/transcripts.jsonl`, and handed to `routing/` — intent match,
slot extraction, then the matched PowerShell handler runs and EKKO says
what happened ("Done.", "That's already done.", "Sorry, I didn't catch
that."). Nothing gets transcribed or persisted from a segment that was
never preceded by a verified wake word.

**`--no-execute`** keeps all of that except the subprocess call, so you
can watch what EKKO would run before letting it run anything.

**Active listening is a loop, not a single shot.** Whenever EKKO actually
says something about a command's outcome — "Done.", "That's already
done.", "Sorry, I didn't catch that.", "I didn't catch which one you
meant.", or "Something went wrong running that." — it follows up with
"Anything else I can help with?" and re-arms `--active-window` instead of
dropping back to idle. The next accepted segment is checked against
`_is_decline()` (a closed, hand-written set: "no", "nope", "nah", "that's
all", ...) before anything else: a decline gets "Alright, let me know if
you need anything." and ends the turn, anything else is routed as the
next command, same as the first. Only two things end the loop without a
decline: the `--active-window` timeout elapsing in silence (EKKO says
"Never mind, going back to sleep." — `listening_timeout` — so there's an
audible signal it stopped listening, not just a console/log line), or an
outcome where EKKO stayed quiet (an empty transcript, or `--no-execute`
swallowing a would-be response) — nothing was said, so there's nothing to
react to.
`_is_decline` deliberately isn't a `routing/intents.yaml` intent: that
file is the security boundary for things EKKO can *do*, and "no" doesn't
do anything.

This module owns the mic loop and orchestration; it depends on
`voice_auth` for the speaker model and comparison logic (`enroll.py`,
`verify.py`), on `routing/` for what a command means, on `feedback/`
for saying so, and on `ui/` for showing so — none of the four depend
back on this one. Complete a voice_auth enrollment first (see
`voice_auth/auedio.md`) before running this.

**A small on-screen popup mirrors the pipeline's state** — listening,
processing, speaking, or an error like a failed voice match —
alongside a live waveform and the running transcript. It's `ui/`'s
`VoiceUI`, running in its own thread inside this same process (no new
console window per wake word), sitting hidden and near-zero-cost until
a state change shows it, and auto-hiding a beat after returning to
idle. Purely cosmetic — nothing in the pipeline branches on whether
it's there — so `--no-ui` turns it off with no other effect, useful
for a headless run with no interactive desktop session.

**This is where EKKO's permission boundary takes effect.** A segment
only reaches the router if the wake word fired *and* the voice matched;
the router can only return an intent from `routing/intents.yaml`; that
can only name a `.ps1` under `scripts/`. There's no path from speech to
an arbitrary command, by construction rather than by check. Adding an
intent is the only way to widen it.

## Usage

Run from the repo root (or anywhere, paths resolve relative to this
file regardless of cwd):

```powershell
python listener\vad_listener.py
python listener\vad_listener.py --wake-threshold 0.6 --verify-threshold 0.75
python listener\vad_listener.py --no-save         # don't keep permanent captures
python listener\vad_listener.py --no-verify       # wake word only, skip identity check
python listener\vad_listener.py --whisper-model small
python listener\vad_listener.py --no-transcribe   # verify only, skip transcription
python listener\vad_listener.py --skip-wake       # tuning mode, see below
python listener\vad_listener.py --feedback-tail-ms 500   # longer room-decay tail after speaking
python listener\vad_listener.py --no-execute      # route and say the outcome, run nothing
python listener\vad_listener.py --routing-threshold 0.6   # stricter intent matching
python listener\vad_listener.py --command-verify-threshold 0.35  # stricter "is that you" on commands
python listener\vad_listener.py --active-window 15        # longer to get your command out
python listener\vad_listener.py --no-ui           # no on-screen popup, e.g. headless
```

**Tuning the command screen:** the four defaults were derived from the
captures in `captures/`, not picked as round numbers, so re-derive them
the same way rather than nudging by feel — replay real WAVs through
`_screen_command_segment()` and look at where the two populations
actually sit. `--command-verify-threshold` is the one worth watching: if
real commands start getting `ignoring segment: not the enrolled
speaker`, your enrollment is the thing to fix (`voice_auth/diagnose.py`),
not this number. Rejections are cheap by design — EKKO keeps listening,
you just say it again — so prefer erring towards rejecting.

The first run downloads openWakeWord's ONNX model files (a few MB,
cached locally after that), same as the speechbrain download in
`voice_auth/enroll.py`.

**Tuning verification accuracy:** `--skip-wake` skips the wake word
requirement entirely and verifies every speech segment directly, one
after another, as if each one were the wake word segment. Since the
wake word segment is what's actually verified now, use this by
repeating "hey jarvis" itself (not arbitrary test phrases) and watching
similarity scores land while you dial in `--verify-threshold`.

**Transcript log:** `captures/transcripts.jsonl` (default, override
with `--transcript-log`) is an append-only log, one JSON object per
command that followed a verified wake word: timestamp, the WAV path it
came from, the transcript text, and `verify_score`, the *wake word*
segment's similarity score. That's still the wake word's rather than the
command's even though commands are now scored too, because it's the one
that authorized anything; the command's own score is a filter, not a
permission, and goes to the console. `verify_score` is `null` under
`--no-verify`, where nothing was ever scored. Segments rejected by the
command screen are never logged here — nothing was accepted as a
command, so there's no command to record. Under `--no-save` the underlying WAV is a temp file that gets
deleted right after verification, so `wav_path` is logged as `null` in
that case rather than pointing at a file that no longer exists.

## Files

- `vad_listener.py` — always-on VAD → wake word (verify + acknowledge) → command → transcribe pipeline
- `captures/` — saved speech segments (default `--save-dir`; pass `--no-save` to skip)
- `captures/transcripts.jsonl` — append-only transcript log for commands that followed a verified wake word (see above)
