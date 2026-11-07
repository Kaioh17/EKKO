# Listener

Always-on listening pipeline: Silero VAD catches when someone's
speaking, openWakeWord listens continuously for "hey ekko" during
that speech. Identity is checked right there, at the wake word
segment itself, against `voice_auth/reference_embedding.pt`, before
anything else happens. A match plays an audio acknowledgment
immediately (a spoken line by default; `--ack-style` makes it a short chime or nothing, and `--no-feedback` silences it) and arms active listening: it waits
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
instead, on the theory that the wake phrase was too short/acoustically
narrow for the speaker embedding to be reliable there. `voice_auth`
now has a short-clip reference bucket and calibrated threshold for
exactly that duration, see `voice_auth/verify.py`'s `SHORT_BUCKET`,
just not one calibrated on "hey ekko" specifically. Re-tune
`--verify-threshold` with `--skip-wake` below, actually saying the
wake word while you do, don't assume the existing default transfers.)

Once a command segment passes screening, it's transcribed locally
(see [Speech recognition](#speech-recognition) for the two engines),
appended to
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

## Speech recognition

When you finish a request, ekko turns your voice into text on your own computer. It has two engines for that, and by default it picks one for you.

| | Whisper | Parakeet |
|---|---|---|
| Meant for | A GPU, or a CPU with the `small` model | A CPU. Not yet timed against Whisper on this project; the app's Recent requests shows your own numbers |
| What ekko uses | `faster-whisper`: the `medium` model on a GPU, `small` (int8) on a CPU | NVIDIA's Parakeet TDT 0.6B through `onnx-asr`, int8, on the CPU |
| Download | `faster-whisper` fetches the model the first time it is used | About 660 MB, once, from the Audio page |
| Tells ekko how sure it is | Yes (`--max-no-speech-prob`, `--min-avg-logprob`) | No. ekko only knows whether anything was heard |
| Languages | English | `tdt-0.6b-v2` is English. `tdt-0.6b-v3` covers 25 European languages |

ekko only understands English commands, so `tdt-0.6b-v2` is the default Parakeet model and the one to use.

### How "Automatic" chooses

1. If a GPU is present **and its CUDA libraries load**, ekko uses Whisper on the GPU.
2. Otherwise, if Parakeet has been downloaded, ekko uses Parakeet on the CPU.
3. Otherwise ekko uses Whisper on the CPU, and the Audio page says Parakeet isn't downloaded yet.

A GPU that is present but can't start counts as "not usable" and takes you to step 2, not straight to a slower Whisper. That is the normal situation in the installed app, which doesn't ship NVIDIA's CUDA libraries (they add about 1.4 GB). A development setup gets them from the CUDA build of PyTorch.

If you pick an engine yourself (`stt_engine` set to `whisper` or `parakeet`), ekko respects that. The one exception: if Parakeet is chosen but can't start, ekko uses Whisper so it still works, and says why.

### Using Parakeet

1. Open **General & Voice**, then **Speech recognition**.
2. Set **Engine** to **Parakeet** (or leave it on **Automatic** if you have no usable GPU).
3. Click **Download** next to **Parakeet model**. A progress bar follows it. The files go to `models/stt/<model id>/` in the data folder.
4. When it says **Ready**, click **Restart listening to use it**. Saving other settings on the page also restarts the listener.
5. Check **Running now**. It should read `Parakeet · tdt-0.6b-v2 · CPU`, followed by the thread count.

Parakeet needs the `onnx-asr` package. Installers built from this version include it; from source, `pip install -r requirements.txt` installs it. Parakeet uses the same CPU thread setting as Whisper (`whisper_cpu_threads`, where 0 means half your cores). Calibrate times Whisper, not Parakeet, so treat its answer as a starting point for Parakeet.

If Parakeet can't start, ekko keeps working on Whisper. **Running now** shows the reason, and the listener log has it too.

### Spelling your words

Speech engines often hear "echo" for "Ekko". After transcription, ekko fixes near-misses of the words in `custom_words` (by default `Ekko`, `Chrome`, `Brave` and `Spotify`). It compares spelling and sound, and it can join up to three spoken words into one custom word, so "charge b" can become "ChargeBee". `word_correction_threshold` sets how loose a match may be: 0 turns it off, and the default 0.18 only accepts close misses. A looser setting starts changing words you meant.

This is a port of the matcher in [Handy](https://github.com/cjpais/Handy) (MIT, `src-tauri/src/audio_toolkit/text.rs`). It applies to both engines. Whisper is also still nudged toward ekko's commands by a prompt (`DEFAULT_INITIAL_PROMPT` in `defaults.py`); Parakeet has no equivalent, which is why the correction step exists.

### What gets ignored

Before a transcript counts as a command, ekko checks it. A request is dropped, and listening continues, if it is:

- shorter than `min_command_ms`,
- not your voice (when command verification is on),
- empty,
- exactly something ekko itself says (reason `EKKO's own voice`), which catches it hearing its own replies,
- for Whisper only: unlikely to be speech (`max_no_speech_prob`) or low confidence (`min_avg_logprob`).

Each rejection is logged with its reason. See [Segment log](#segment-log).

### If it isn't working

| What you see | Likely cause | What to check |
|---|---|---|
| Download stops with an error | Network or disk space | The message under **Parakeet model**. Click **Try again** |
| Downloaded, but **Running now** still says Whisper | The listener hasn't restarted, or Parakeet couldn't start | Click **Restart listening to use it**. If it still says Whisper, read the reason beside **Running now** |
| The reason mentions `onnx_asr` | The package isn't installed | `pip install "onnx-asr[cpu,hub]"` for a source install |
| **Running now** says "your graphics card can't be used" | No CUDA libraries | Nothing is broken. See "Why my graphics card isn't used" in Help, or use Parakeet |
| The first word is often missing | The request started before ekko noticed speech | Raise `preroll_ms` |
| Requests get cut in half | You pause longer than `command_min_silence_ms` | Raise it, or use [hold to talk](#hold-to-talk) |
| Real requests are being ignored | A confidence gate is too strict | Look at `reason` in the segment log, then loosen that setting |

## Hold to talk

Saying "Hey ekko", waiting for the greeting, speaking, and then waiting while ekko decides you've finished adds up to several seconds. Hold to talk skips all of it: hold a key, speak, let go. It is optional and off by default.

### Turn it on

1. Open **General & Voice**. The **Hold to talk** card is at the top.
2. Switch **Hold to talk** on.
3. To use other keys, click the key shown and press the combination you want. Press **Esc** to cancel.
4. Save. Saving restarts the listener, which is when the key is registered.
5. Hold the key, say your request, and let go.

The default key is **Ctrl+Alt+Space**. It is separate from **Ctrl+Alt+W** (manual wake), which works as it always did. A tip on Home offers a one-click "Turn it on" until you use it or dismiss it.

### What it does differently

- **No wake word, no spoken acknowledgement, no speaker check.** Holding the key is the proof you're there, as with manual wake.
- **No waiting for a pause.** Letting go ends the request.
- **The start isn't clipped.** The `preroll_ms` of audio before you pressed is kept.
- **An empty press is dropped.** If nothing was said, ekko ignores it (reason `nothing said`).
- **A hold stops at 30 seconds.** That covers a stuck key, and Parakeet can't decode much more than that anyway.
- **Hard stop cancels it.** `Ctrl+Alt+Q` ends a hold and cuts any speech.

### Limits

- The key isn't blocked, so the app you're in also sees it. Pick a combination your apps don't use.
- A chord needs Ctrl, Alt or Windows, or must be a function key on its own (such as `f9`). Otherwise it would fire while you type. It can't be Manual wake or Hard stop. The page and the API both say so when you pick one.
- If you press it while ekko is still speaking, ekko notices when it finishes. Use hard stop to cut the speech.
- The key is saved in the `keyboard` package's spelling, for example `ctrl+alt+space`.

### If nothing happens

1. Check the listener log (`backend/logs/listener.log`) for `Hold-to-talk hotkey: ...` at startup. If it says "not usable", the key isn't one the `keyboard` package can map. Choose another.
2. Check that **Hold to talk** is on and saved.
3. Another program may use the combination. Pick a different one.

### The wake-word acknowledgement

After "Hey ekko", ekko can say hi (`speech`, the default), play a short rising chime (`chime`), or stay quiet (`none`). Set it with `ack_style`, or `--ack-style`. It applies to the wake-word path whether or not hold to talk is on.

The chime is about 0.2 seconds of sound. Measured on the development machine it occupies about 0.7 seconds including the device's own delay, against a median of about 2.4 seconds for a spoken line. With `none`, ekko plays nothing and you can speak immediately. `--no-feedback` silences all of it.

## Settings reference

These live in the app under **General & Voice** and are stored in the settings database. For development, each has a command-line flag, and an explicit flag wins over the saved value. Saving restarts the listener.

| Setting | Default | What it does |
|---|---|---|
| `vad_threshold` | 0.5 | How sure the voice detector must be that it hears speech. Raise it in a noisy room |
| `min_silence_ms` | 300 | Silence that ends a segment while waiting for the wake word |
| `command_min_silence_ms` | 800 | Silence that ends a request after the wake word. 500 split commands at their pauses |
| `preroll_ms` | 450 | Audio kept from before speech is detected, so the first word isn't clipped. 0 turns it off |
| `wake_word` | `hey_ekko` | The wake word model. Locked: changing it needs a code change (see Help) |
| `wake_threshold` | 0.5 | How sure the wake word model must be |
| `verify_threshold` | automatic | How closely the wake phrase must match your voice. Automatic picks by clip length |
| `command_verify_threshold` | 0.25 | How closely a request must match your voice. Far looser than the wake word on purpose |
| `routing_threshold` | 0.58 | How close a request must be to a known command to run it. See `routing/README.md` |
| `active_window_s` | 10 | Seconds to wait for a request after the wake word, and after each reply |
| `feedback_tail_ms` | 300 | Quiet time after ekko speaks, so it doesn't hear its own voice |
| `ack_style` | `speech` | After the wake word: `speech`, `chime` or `none` |
| `min_command_ms` | 300 | Shortest request. Briefer sounds are clicks or bumps |
| `max_no_speech_prob` | 0.45 | Whisper only. Reject a clip it thinks isn't speech above this |
| `min_avg_logprob` | -0.95 | Whisper only. Reject a transcript it is unsure of below this |
| `input_device` | system default | The microphone, by name |
| `output_device` | system default | The speaker, by name |
| `stt_engine` | `auto` | `auto`, `whisper` or `parakeet`. See [Speech recognition](#speech-recognition) |
| `parakeet_model` | `tdt-0.6b-v2` | Which Parakeet model. `tdt-0.6b-v3` adds 25 European languages |
| `whisper_model` | `auto` | `auto`, `tiny`, `base`, `small`, `medium` or `large-v3`. Automatic is medium on a GPU, small on a CPU |
| `whisper_device` | `auto` | `auto`, `gpu` or `cpu`. `cpu` never uses the GPU |
| `whisper_cpu_threads` | 0 | CPU threads for speech recognition. 0 means half your cores |
| `custom_words` | `Ekko`, `Chrome`, `Brave`, `Spotify` | Spellings to restore after transcription |
| `word_correction_threshold` | 0.18 | How loose a custom-word match may be. 0 turns it off |
| `push_to_talk` | off | Turn on [hold to talk](#hold-to-talk) |
| `push_to_talk_hotkey` | `ctrl+alt+space` | The key combination to hold |
| `no_save`, `no_verify`, `no_transcribe`, `no_execute`, `no_llm_fallback`, `no_manual_wake`, `no_hard_stop` | all off | Off switches. The app shows each as a positive toggle: keep audio clips, only respond to my voice, write down what I say, run commands, ask a cloud AI, wake with Ctrl+Alt+W, stop with Ctrl+Alt+Q |

## Segment log

The listener writes one JSON line per event to `listener/logs/segments.jsonl` in the data folder. It exists to measure changes instead of guessing about them. Every line has `ts` and `event`.

| `event` | When | Fields |
|---|---|---|
| `segment` | A stretch of speech ended | `kind` (`wake` or `command`), `duration_s`, `outcome` (`accepted` or `rejected`), `reason`. Wake segments add `verify_score`, `verify_ms`. Command segments add `via` (`voice` or `push_to_talk`), `preroll_ms`, `detail`, `screen_ms`, `stt_ms`, `no_speech_prob`, `avg_logprob`, `text`, `wake_verify_score` |
| `activation` | ekko started listening for a request | `source` (`wake`, `manual` or `push_to_talk`) |
| `timeout` | No request came in time | `window_s` |
| `overflow` | The microphone dropped audio | `gate_open`: whether the mic was live, so the lost audio could have been you |
| `route` | A request was matched to a command | `status`, `intent`, `route_ms` |
| `execute` | A command ran | `intent`, `exec_ms` |
| `engine_loaded` | The listener started a speech engine | `engine`, `model`, `device`, `compute_type`, `threads`, `requested`, `fallback_reason`, `load_ms` |
| `stt_error` | An engine failed on one request | `engine`, `error` |

`reason` is a short code with no numbers in it, such as `too short`, `no speech in it`, `EKKO's own voice` or `nothing said`, so rejections can be counted. `detail` keeps the full text.

Summarize it with:

```powershell
python -m listener.stt_report                     # print the metrics
python -m listener.stt_report --append "what changed"   # also add a row to STT_IMPROVEMENTS.md
```

If the log doesn't exist yet, the report falls back to the older `listener.log` and `transcripts.jsonl`. The app shows the same figures for your last 50 requests under **Speech recognition**, as **Recent requests**.

## Usage

Run from the repo root (or anywhere, paths resolve relative to this
file regardless of cwd):

```powershell
python listener\vad_listener.py
python listener\vad_listener.py --wake-threshold 0.6 --verify-threshold 0.75
python listener\vad_listener.py --no-save         # don't keep permanent captures
python listener\vad_listener.py --no-verify       # wake word only, skip identity check
python listener\vad_listener.py --whisper-model small
python listener\vad_listener.py --stt-engine parakeet     # Parakeet on the CPU (download it from the app first)
python listener\vad_listener.py --push-to-talk            # hold ctrl+alt+space, speak, let go
python listener\vad_listener.py --push-to-talk --push-to-talk-hotkey ctrl+alt+t
python listener\vad_listener.py --ack-style chime        # a short chime after the wake word
python listener\vad_listener.py --preroll-ms 600         # keep more audio from before speech starts
python listener\vad_listener.py --whisper-device cpu --whisper-cpu-threads 6
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
repeating "hey ekko" itself (not arbitrary test phrases) and watching
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
- `defaults.py` — default values (light: the backend imports it)
- `audio_setup.py` — which saved settings reach the listener, and the audio devices it opens
- `stt.py` — chooses and loads the speech engine, transcribes, and judges a transcript
- `stt_models.py` — the Parakeet model catalog and its download
- `text_correct.py` — custom-word spelling correction
- `segmenter.py` — the pre-roll buffer and the hold-to-talk state machine
- `hotkey.py` — what counts as a valid hotkey
- `segment_log.py` — the segment log writer
- `stt_report.py` — summarizes the segment log (`python -m listener.stt_report`)
- `calibrate.py` — finds the fastest CPU thread count for Whisper
- `STT_IMPROVEMENTS.md` — the plan, baseline and progress log for speech recognition work
- `captures/` — saved speech segments (default `--save-dir`; pass `--no-save` to skip)
- `captures/transcripts.jsonl` — append-only transcript log for commands that followed a verified wake word (see above)
