# STT improvements

Plan and checklist for making speech-to-text faster and more accurate, plus
the baseline we measured before changing anything. Re-measure after each
change and add a row to [Check-backs](#check-backs).

## Where this stands

The work was planned in seven phases (0 to 6). All seven are built. This file
is the working record: the plan, the baseline and what happened. How things
work now is in [README.md](README.md) (engines, hold to talk, settings
reference, segment log).

**Still open**

- **Benchmark Parakeet against Whisper `small` on a CPU.** Every speed figure
  for Parakeet in [Projections](#projections) is an estimate. Nothing in the
  app or the docs claims it is faster, and `tests/test_docs.py` fails if one
  slips in. Remove that test once the benchmark is in.
- **Build an installer and check Parakeet inside it.** The spec collects
  `onnx_asr`, but no installer has been built from this version.
- **Make Calibrate time Parakeet.** It times Whisper only.
- **Find what causes the input overflows** (341 in the baseline logs). The
  segment log now records whether the mic was live for each.
- **Decide about `initial_prompt` and the log-probability gate**, using the
  segment log once there's enough of it.
- **GPU speech recognition in the installed app** needs the CUDA libraries
  (about 1.4 GB), deliberately not bundled.

## Reference repo

[cjpais/Handy](https://github.com/cjpais/Handy) (MIT), studied at `73cc8579`
(2026-11-06, v0.10.0), local clone `../Handy`. It's a local, hotkey-driven
dictation app with a very responsive feel. What we're borrowing, and where it
lives in Handy:

| Idea | Handy source |
|---|---|
| 450 ms pre-roll, 60 ms onset, 450 ms hangover around Silero | `src-tauri/src/audio_toolkit/vad/mod.rs`, `vad/smoothed.rs` |
| Hold-to-talk: the key release ends the utterance, no silence wait | `src-tauri/src/actions.rs` (`TranscribeAction`) |
| Custom-word correction after the transcript (Levenshtein + Soundex) instead of a prompt | `src-tauri/src/audio_toolkit/text.rs` (`apply_custom_words`) |
| Pick one engine, keep it warm | `src-tauri/src/managers/transcription.rs`, `catalog/catalog.json` |
| Parakeet models as GGUF Q8_0 | `catalog/catalog.json`: `parakeet-tdt-0.6b-v2`, `parakeet-unified-en-0.6b`, `parakeet-tdt-0.6b-v3`, `parakeet-tdt_ctc-110m` |

Handy uses `transcribe-cpp` (Rust/C++). We're in Python, so for Parakeet we'd
use an ONNX build (sherpa-onnx or onnx-asr). The benchmark task below has to
confirm this.

## Current pipeline (before changes)

1. **Capture.** `sd.InputStream` reads 16 kHz mono float32 in 512-sample
   (32 ms) blocks with `latency="high"`. The callback drops blocks while
   `MicGate` is closed and queues the rest.
2. **Main loop.** Each block is scored by openWakeWord, appended to
   `speech_buffer` while `in_speech`, and fed to Silero's `VADIterator`.
   `speech_buffer` starts at the block that triggered `start`, so there's no
   pre-roll.
3. **Segment end.** The segment closes after `min_silence_ms` of silence
   (300 ms idle) or `command_min_silence_ms` (code default 1500 ms; the dev
   DB has **500 ms**).
4. **Wake segment.** It's written to WAV and goes only to speaker
   verification, then EKKO speaks the ack.
5. **Command segment.** It's written to WAV, then
   `_screen_command_segment` checks duration (300 ms floor), then speaker
   (0.25), then `_transcribe` (faster-whisper, `vad_filter`,
   `initial_prompt`), then `no_speech_prob` (0.45) and `avg_logprob` (-0.95).
6. **Engine.** `pick_whisper()`: `medium`/float16 on CUDA, `small`/int8 on
   CPU, threads from `whisper_cpu_threads()` or `listener/calibrate.py`.

## Baseline (2026-11-06)

Sources:
- dev `backend/logs/listener.log` (84 sessions)
- `listener/captures/transcripts.jsonl` (1015 commands)
- `routing/logs/routing.jsonl`
- the 24 surviving captures of 1 s or longer

Most dev sessions ran Whisper medium on CUDA.

| Metric | Baseline |
|---|---|
| Activations with a command accepted on the first try | 46/101 (+3 after a rejection) |
| Activations that timed out with no VAD segment at all | 35/101 |
| Activations with an `input overflow` before the command resolved | 45/101 (341 overflows total) |
| Accepted command length | p50 2.08 s, p90 4.45 s |
| Rejected: no speech / low logprob / not speech | 15 / 6 / 5 (p50 length 0.93 / 1.41 / 0.86 s) |
| End of segment → transcript logged (excludes the silence wait) | p50 1.09 s, p90 4.05 s |
| EKKO's own phrases accepted as commands ("Access denied." ×5, "Command confirmed." ×1) | 6 |
| Captures already at speech level in the first 32 ms (no lead-in) | 18/24 |
| Voice routing: matched / no_match / missing_slot | 115 / 316 / 3 |
| Installed app launches that fell back to CPU (missing `cublas64_12.dll`) | 9/11 |

Limits of this baseline:
- `listener.log` lines carry only `HH:MM:SS`, so sessions with different
  settings are mixed together.
- Overflows have no timestamp, so we can't tell whether they land inside the
  gated window, where they're harmless, or after it, where they cost audio.
- There's no CPU latency baseline yet.

## Projections

These are estimates to check against, not results. **(m)** means measured
from the baseline logs. **R** is routing plus command execution: it was never
logged, so it's left out of every total. Piper's time to first audio is
166 ms p50 **(m)**.

### From when you stop talking to when EKKO starts answering

| Step | Today (dev settings) | After the plan, wake-word turn | After the plan, hold-to-talk |
|---|---|---|---|
| Silence wait before the segment closes | 500 ms (code default 1500 ms) | 800 ms | 0 ms (key release) |
| STT plus gates | 1090 ms p50 **(m)**, Whisper medium on GPU | ~200–500 ms, Parakeet on CPU (estimate) | ~200–500 ms |
| Disk write | a few ms | 0 (in memory) | 0 |
| Reply starts playing | 166 ms **(m)** | 166 ms | 166 ms |
| **Total** | **~1.75 s + R** (~2.75 s at the default) | **~1.2–1.5 s + R** | **~0.4–0.7 s + R** |

The Parakeet figure is unproven until the benchmark runs. The reason to
expect a gain: Whisper always encodes a 30 s window, while Parakeet only
processes the audio you actually said. Pre-roll adds no latency, only 450 ms
of audio at the start of the segment.

### A full session, today (wake word)

| Time | What happens |
|---|---|
| 0.0 s | You say "Hey Ekko" |
| ~0.9 s | Wake phrase ends (p50 0.90 s **(m)**) |
| +0.3 s | Idle silence wait closes the segment |
| +~0.1 s | Speaker check (~100 ms; off in dev runs) |
| +0.15 s | Ack is synthesized **(m)** |
| +~1.8 s | Ack plays |
| +0.3 s | Mic gate tail |
| **~3.5 s** | **Earliest point you can start the command.** Anything said earlier is dropped. |
| +2.1 s | You speak the command (p50 2.08 s **(m)**) |
| +0.5 s | Command silence wait |
| +1.1 s | STT plus gates **(m)** |
| +R | Route and execute |
| +0.17 s | Reply starts **(m)** |
| **~7.4 s + R** | **You hear the answer** |
| then | "Anything else?" (~2 s), then a 10 s window, or a decline |

### The same session after the plan, using hold-to-talk

| Time | What happens |
|---|---|
| 0.0 s | Press the key. Recording starts with no wake word, speaker check, or ack. |
| 0.0–2.1 s | Speak the command. The pre-roll buffer catches the first syllable. |
| 2.1 s | Release |
| +~0.3 s | Parakeet STT plus gates (estimate) |
| +R | Route and execute |
| +0.17 s | Reply starts |
| **~2.6 s + R** | **You hear the answer**, about 4.8 s sooner than today |

The wake-word path after the plan comes to about **5.9–6.4 s + R**. The
~2.25 s ack is the largest remaining cost there.

## Checklist

### Measurement
- [x] Move the baseline script into the repo: `listener/stt_report.py`.
- [x] Write one JSONL record per segment (`listener/segment_log.py`; format
      in [Segment log](#segment-log)).
- [x] Timestamp `input overflow` and record whether the gate was open.
- [x] Log routing and execution ms, so check-backs can track the whole
      response time and not just STT (fills in **R** in
      [Projections](#projections)).

### Capture and VAD
- [x] Pre-roll ring buffer: `PreRoll` in `listener/segmenter.py` keeps the
      last 14 blocks (450 ms, setting `preroll_ms`) and prepends them on
      `start`. Expected: first words stop getting clipped. Not yet
      confirmed by comparing clips.
- [x] Raise the `command_min_silence_ms` default from 1500 to 800 ms. The
      dev DB's saved value was 500, which is below it. Expected: fewer split
      commands and fewer sub-1 s "no speech" rejections.
- [ ] Find the cause of the input overflows that follow an activation.
- [ ] Mic gate: keep the 300 ms tail. Echoes still leak through, so revisit
      only once the per-segment log shows they've stopped.

### Endpointing (the hotkey lesson)
- [x] Hold-to-talk: press opens a command segment and skips the ack; release
      closes it. Optional (`push_to_talk`, off by default), with its own
      editable key (`push_to_talk_hotkey`, default Ctrl+Alt+Space). Expected:
      no silence wait (500–1500 ms) on that path. Confirmed working live
      on the dev machine (2026-11-07).
- [x] Shorter wake ack: `ack_style` is `speech` (default), `chime` or `none`.

### Transcription
- [x] Pass the in-memory array to the transcriber (`listener/stt.py`).
      `verify()` still needs a path, so the WAV write stays for now.
- [~] Custom-word correction after the transcript
      (`listener/text_correct.py`, a port of Handy's) is in and applies to
      both engines. `initial_prompt` is still on for Whisper: decide whether
      to drop it from the segment log once it shows how often the prompt's
      phrases come back as transcripts.
- [ ] Re-evaluate the logprob gate on segments under 1.5 s once structured
      logs exist. Don't loosen it before then.
- [x] Fix `WHISPER_BEAM_SIZE`: `listener/calibrate.py` imported it, but
      nothing defined it. It's now in `listener/defaults.py` and used by
      live decoding too.

### Parakeet (an engine switch, not an assistant)
- [ ] Benchmark Parakeet against Whisper `small` int8 on CPU with the
      segment log (WER by eye on real commands, `stt_ms`, behavior on
      silence). Run after the first live session with the model installed.
- [x] STT engine setting: `auto` (Whisper on a GPU, Parakeet on CPU),
      `whisper` or `parakeet`, with a fallback to Whisper that records why.
- [x] Parakeet gating: the runtime reports no confidence, so only the empty
      text check applies, plus the new self-echo check for both engines.

### Packaging
- [x] Installed app on a machine with a GPU: `auto` now goes to Parakeet
      when the GPU can't start, instead of a slow CPU Whisper. The CUDA
      libraries are deliberately **not** bundled (see the Phase 5 note).
- [x] onnx-asr and huggingface_hub added to the PyInstaller spec; `locks/`
      regenerated.
- [ ] Rebuild the installer and check Parakeet inside it (needs a build).

## Segment log

The format is documented in [README.md](README.md#segment-log), and
`tests/test_docs.py` fails if the code writes an event the README doesn't
list. Summarize it with `python -m listener.stt_report`, which also adds a
row to Check-backs with `--append "what changed"`.

Two definitions differ from the hand-made baseline row:
- **Overflow activations** counts overflows up to the next activation (50),
  not only until the command resolved (45).
- **Echo accepts** counts EKKO's current `RESPONSES` lines plus the retired
  lines still in `initial_prompt` (`PROMPT_ONLY_PHRASES`), which gives 7.

## Progress log

One line per finished phase.

- **2026-11-07, Phase 0 (measurement).**
  - Added `segment_log.py` and `stt_report.py`.
  - The listener now logs segments, activations, timeouts, overflows with
    the gate state, routing and execution time, and engine load.
  - Fixed the `WHISPER_BEAM_SIZE` import.
  - New tests: `test_segment_log.py`, `test_stt_report.py`,
    `test_calibrate.py`. All failed first, then passed; full suite green.

- **2026-11-07, Phase 1 (settings reach the listener, STT controls in the UI).**
  - **Bug fixed:** `input_device`, `output_device`, `whisper_device` and
    `whisper_cpu_threads` were saved but never read, so the Microphone and
    Speaker pickers did nothing. `listener/audio_setup.py` now carries every
    setting to the listener and applies the saved devices through
    sounddevice's defaults (a missing device logs a note and keeps the
    system default).
  - **New settings:** `min_command_ms`, `max_no_speech_prob`,
    `min_avg_logprob`.
  - **UI:** a Speech recognition section (model, processor, threads with a
    Calibrate button, hardware line), an exact reply-pause note, and a
    collapsed Advanced listening section.
  - **Moved to later phases:** `stt_engine`, `parakeet_model` and
    `custom_words` (Phase 3), `preroll_ms` (Phase 2), `push_to_talk` and
    `ack_style` (Phase 4). A setting that nothing reads would repeat the
    bug above.
  - **Tests:** `test_listener_settings.py` plus new cases in
    `test_whisper_device.py`. `tsc --noEmit` is clean. No Check-backs row:
    nothing here changes measured behavior until it's run live.
  - **To check live:** change the mic in the UI and confirm the listener log
    prints `Audio devices:`. The picker lists names from PyAudio and the
    listener matches them in sounddevice, so a name mismatch would show up
    there as a "not found" note.

- **2026-11-07, Phase 2 (capture fixes).**
  - **Pre-roll:** `PreRoll` keeps the last 450 ms and hands it to each new
    segment. It's cleared whenever the mic gate closes (EKKO spoke) and on
    hard stop, so EKKO's own voice and the wake phrase are never prepended
    to a command. The duration gates (`min_command_ms`) judge the segment
    without its lead, so a 100 ms click doesn't become a 550 ms "command".
  - **Silence default:** 1500 → 800 ms. The UI presets are now
    Quick 500 / Normal 800 / Patient 1500.
  - **In-memory audio:** the command segment goes to faster-whisper as an
    array. `Transcription` and `transcribe()` moved to `listener/stt.py`
    (Phase 3 builds the engine switch on it).
  - **New setting:** `preroll_ms` (0 turns it off), in Advanced listening.
  - **Logging:** command segments record `preroll_ms`.
  - **Tests:** `test_segmenter.py`, `test_stt_transcribe.py`, and
    `preroll_ms` cases in `test_listener_settings.py`. Full suite and
    `tsc --noEmit` are clean.
  - **Not covered by tests:** the main loop itself (it needs a mic). A
    scratch run of real Silero over a saved clip showed the start firing one
    block after onset with 14 lead blocks, but its end event didn't fire on
    my synthetic noise tail.
  - **Mic gate:** left as is (300 ms tail), pending data from the new log.
  - **Check-backs row:** to be added after a live session, since the
    segment log is still empty.

- **2026-11-07, Phase 3 (engine switch, Parakeet, custom words).**
  - **Engines:** `listener/stt.py` now picks and loads the engine
    (`pick_engine`, `load_engine`). `auto` is Whisper on a usable GPU and
    Parakeet otherwise. If Parakeet isn't downloaded or fails to start, the
    listener falls back to Whisper and says why (log, runtime file, UI).
  - **Parakeet runtime:** onnx-asr, int8 ONNX, CPU, with the thread count
    from settings. Models are `tdt-0.6b-v2` (English, default) and
    `tdt-0.6b-v3` (25 European languages). Handy's Unified-EN model is a GGUF
    for its own runtime and isn't available here.
  - **Downloads:** `listener/stt_models.py` fetches the int8 files (about
    660 MB, not the 2.4 GB fp32 weights) into the data dir's `models/stt`;
    `GET /api/stt/models` and `POST /api/stt/models/{id}/download` drive the
    Audio page's Download button and progress bar.
  - **Self-echo check:** a transcript equal to something EKKO says (or one of
    the retired prompt lines) is rejected as "EKKO's own voice".
  - **Custom words:** `custom_words` and `word_correction_threshold` in
    settings, default `Ekko, Chrome, Brave, Spotify` at Handy's 0.18.
  - **Status:** the listener writes `listener/stt_runtime.json`;
    `GET /api/stt/status` serves it; the Audio page shows "Running now".
  - **Bug found:** `backend/main.py` never registered the `stt` router, so
    `/api/stt/hardware` and `/api/stt/calibrate` returned 404 (since they
    were added). Fixed, with a test. This also means the Phase 1 Calibrate
    button couldn't have worked before now.
  - **Tests:** `test_text_correct.py`, `test_stt_engine.py`,
    `test_stt_models.py`, more cases in `test_listener_settings.py`.
    `tsc --noEmit` is clean.
  - **Not run:** nothing has loaded Parakeet. onnx-asr isn't installed in
    `pvenv` and no model was downloaded; the loader is written from the
    onnx-asr docs and is covered only through fakes. First real use is the
    UI download plus a restart.
  - **Known gaps:**
    - `python -m listener.calibrate` and the Calibrate button still time
      Whisper, not Parakeet.
    - The packaged app needs onnx-asr added to its build (Phase 5).
    - `locks/` hasn't been regenerated for the new dependency.

- **2026-11-07, fix: listener crashed at startup (Phase 1 bug).**
  - `apply_devices` read `sd.default_hostapi`, which doesn't exist (it's
    `sd.default.hostapi`), so the listener died on its first line of setup
    and the supervisor restarted it in a loop with growing backoff.
  - **Why tests missed it:** the test double invented the same wrong
    attribute. It now has the real shape, and
    `test_apply_devices_against_the_real_sounddevice` runs the code on the
    real module (skipped where there's no audio).
  - **Checked with the real libraries:** every device name the UI lists
    resolves in sounddevice; the Whisper loader works on CPU and GPU
    (tiny model); asking for Parakeet without the model falls back to
    Whisper with the reason.
  - **Lesson for the remaining phases:** the main loop and hotkey code can't
    be unit-tested, so each phase also gets a real-library check before it's
    called done.

- **2026-11-07, Phase 4 (hold-to-talk, chime).**
  - **Hold-to-talk:** optional, off by default. Hold the key, speak, let go.
    Press starts a segment with the 450 ms pre-roll and no ack; release sends
    it to transcription with no silence wait. An empty press is dropped
    ("nothing said"), a key stuck down is cut at 30 s, and a hard stop cancels
    a hold. The key is polled with `keyboard.is_pressed()` each audio block
    instead of hooked, so a lost key-up can't strand a recording.
  - **Its own key, not the manual-wake chord:** tapping Ctrl+Alt+W works as
    before. The key is editable (click it, press the combination); the same
    rules apply in the UI and in `listener/hotkey.py`: it needs Ctrl, Alt or
    Windows (or be an F-key), and can't be Manual wake or Hard stop.
  - **`ack_style`:** `speech` (say hi), `chime` (about 0.2 s rising two-note
    sound) or `none`. With `none` the pre-roll is cleared by hand, since
    nothing played to do it. `--no-feedback` still means no sound at all.
  - **Finding it:** a "Hold to talk" card at the top of the Audio page, a
    one-click tip on Home (until it's on or dismissed), and Hold to talk
    listed with its real key and on/off state on the Hotkeys page and in the
    overview.
  - **Measured chime cost:** about 0.53 s blocked plus a 0.18 s device tail
    (~0.7 s), against ~2.4 s median for a spoken line. `latency="high"` is
    most of the gap to the sound's own 0.18 s; lowering it risks clipping the
    first note.
  - **Tests:** `test_hotkey.py`, `test_hold_to_talk.py`, `test_chime.py`,
    `test_overview_hotkeys.py`, new `test_listener_units.py` (the ack styles
    and command screening, which had no tests), settings cases, and
    `hooks/chord.test.ts` (8 JS tests). All green, `tsc` clean.
  - **Checked with real libraries:** all 13 chord spellings the page can
    produce parse in `keyboard`; `is_pressed` follows press and release (fed
    through the library's own event path, no keys sent); the chime plays on
    the real output device; an isolated listener (temp data dir, silent) with
    hold-to-talk on starts, registers the key and reaches listening.
  - **Not verified:** a real hold with a real voice, and the pages in the
    running app. Press the key and speak to check; if it misbehaves the
    segment log's `via` field says which path a segment took.
  - **Known limits:** the key is not suppressed, so the active app also sees
    it; pressing it while ekko is speaking waits until it finishes (use Hard
    stop to cut it).

- **2026-11-07, Phase 5 (what's running, recent speed, packaging).**
  - **Hold-to-talk confirmed working live** after the restart (reported).
  - **Recent requests:** `GET /api/stt/status` now also returns `recent`, a
    summary of the last 50 commands in the segment log
    (`stt_report.recent`), shown on the Audio page next to the engine as
    one sentence: how many were understood or ignored (and why), and the
    typical and slowest-tenth decode time.
  - **Bug fixed (auto on a GPU that can't run):** a packaged app on a PC with
    an NVIDIA card saw "a GPU exists", picked Whisper-on-GPU, failed for lack
    of CUDA libraries and landed on slow CPU Whisper, never on Parakeet. Now
    `auto` asks Whisper for the GPU only, and if it won't start uses Parakeet
    (when downloaded). Choosing Whisper explicitly still gets CPU Whisper.
  - **Plain-language GPU message:** "Running now" explains a dead GPU instead
    of showing the raw CUDA error.
  - **Packaging:** the spec now collects `onnx_asr` and `huggingface_hub`
    (both imported inside functions, so PyInstaller couldn't see them), and
    `locks/windows.txt` and `linux.txt` include `onnx-asr`. No existing pin
    changed. They also gained `mcp`, `keyring` and their dependencies, which
    `requirements.txt` already listed but the lock was missing.
  - **CUDA is not bundled, on purpose:** cuBLAS, cuBLASLt and cuDNN measured
    about 1.4 GB uncompressed (113 + 674 + ~600 MB), against an installer
    built on CPU torch to stay small. The dev machine only works on GPU
    because its venv has the CUDA build of torch. Bundling would mean adding
    the nvidia pip wheels to the build and pointing the DLL search path at
    them at startup; worth it only if GPU users of the installed app matter.
  - **First live Check-backs row** (8 activations, so a small sample, and
    Whisper medium on GPU, not Parakeet). Its STT figure is the pure decode
    time (`stt_ms`, 547 / 640 ms); the baseline's was save-to-logged
    (1.09 / 4.05 s), which also includes screening, so the two columns
    aren't comparable. Echo accepts 0 (the self-echo check is on).
  - **Tests:** engine fallback cases, `recent` (window, grouping, empty
    log), the status route, and `test_packaging.py` (checks the spec; the
    spec itself can't be run here). Suite and `tsc` clean.
  - **Not done:** an installer build, so Parakeet inside the packaged app is
    untested.

- **2026-11-07, Phase 6 (documentation).**
  - **Written:** `listener/README.md` gained Speech recognition (the two
    engines, how Automatic chooses, using Parakeet, custom words, what gets
    ignored, a troubleshooting table), Hold to talk (turning it on, limits,
    what to check), the wake-word acknowledgement, a settings reference that
    covers every `general` setting, and the segment log reference. The usage
    examples and file list are current.
  - **Corrected:** `FEATURES.md` (it said `base.en` on a CPU, and that the
    Voice page picks the processor), `readme.md` (it said speech recognition
    uses the GPU without CUDA libraries, which is only true of a source
    install that has the CUDA build of torch), `docs/DESIGN.md` and
    `docs/ARCHITECTURE.md` (the engine, the new routes, the new files in the
    data folder), and a new `CHANGELOG.md` entry.
  - **In the app:** four Help guides (choosing an engine, why a graphics
    card isn't used, what Calibrate does, hold to talk), linked from the
    places a person would get stuck on the Audio page. The Help intro no
    longer says every guide needs a code change.
  - **Unproven speed claims removed.** I had written that Parakeet was
    "faster on a CPU" in the Audio page and in drafts of these docs. It
    hasn't been measured against Whisper `small`, a 0.24B model against its
    0.6B. The wording is now "built for the processor", with Recent
    requests to compare.
  - **Guards, so the docs stay true:** `tests/test_docs.py` fails if a
    `general` setting isn't in the README's settings reference, if the code
    writes a segment-log event the README doesn't list, if the README names
    a command-line flag that doesn't exist, if the stale claims come back,
    if a Help guide has no link from the Audio page, or if an unmeasured
    Parakeet speed claim appears. It found a real gap: `stt_error` was
    missing from the log table.

## Check-backs

One row per re-measurement. Same metrics as the baseline.

| Date | Change | First-try accept | Silent timeouts | Overflow activations | STT p50 / p90 | Echo accepts | Notes |
|---|---|---|---|---|---|---|---|
| 2026-11-06 | baseline | 46/101 | 35/101 | 45/101 | 1.09 / 4.05 s | 6 | medium on CUDA, mixed settings |
| 2026-11-07 | Phase 0: stt_report over the legacy logs | 46/101 | 35/101 | 50/101 | 1093 / 4053 ms | 7 | source: legacy |
| 2026-11-07 | Phases 2-4 live: pre-roll, 800 ms pause, hold-to-talk (Whisper medium GPU) | 4/8 | 1/8 | 0/8 | 547 / 640 ms | 0 | source: segments |
