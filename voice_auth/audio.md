# Voice Auth Starter Pack

Speaker verification library for the smart work lab assistant: confirms
it's actually you speaking, given a WAV clip. Runs natively on Windows,
no WSL audio bridging.

This package only covers enrollment and verification. The always-on
listening pipeline that feeds it real command clips (VAD, wake word)
lives one level up in `listener/`, see `listener/README.md` — that
module imports `enroll.py` and `verify.py` from here rather than the
other way around, voice_auth has no dependency on it.

## Setup

Open PowerShell in the repo root and run:

```powershell
.\voice_auth\setup.ps1
```

If PowerShell blocks the script from running, you likely need to allow
local scripts for this session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

The script creates a venv, installs a CUDA-enabled PyTorch build, then
installs everything else from the repo-root `requirements.txt` (shared
across voice_auth and listener, no reason to maintain two overlapping
dependency lists). It ends by printing whether your GPU is visible to
torch, confirm that before moving on.

## Workflow

All commands below assume your shell's cwd is this folder (`voice_auth/`).

**1. Record 5-10 enrollment samples.** Vary the phrase and vary when
you record them (different times of day, different energy levels),
so the reference embedding captures natural variation rather than
one specific recording.

```powershell
python record_sample.py enrollment\sample_01.wav
python record_sample.py enrollment\sample_02.wav
# ... repeat to 5-10 samples
```

**2. Build the reference embedding.** First run downloads the
pretrained ECAPA-TDNN model from Hugging Face Hub, a few hundred MB,
cached locally after that.

```powershell
python enroll.py enrollment\ reference_embedding.pt
```

**3. Test it.** Record a fresh clip and check the similarity score.

```powershell
python record_sample.py test.wav --seconds 3
python verify.py reference_embedding.pt test.wav
```

Expected scores depend on clip length, ECAPA-TDNN's embeddings are
noisier from less audio, so `verify.py` picks a different reference and
threshold depending on how long the clip is (see its `SHORT_BUCKET`
comment): clips at or above ~3.5s compare against the full-length
reference and should score roughly 0.85-0.95 for your own voice, well
above the 0.70 default threshold; shorter clips (the common case for
real spoken commands) compare against a duration-matched short reference
instead and land lower, expect somewhere in the 0.4-0.7 range rather
than 0.85+, above the 0.40 default threshold for that bucket. Either
way, have someone else record a test clip too and confirm their score
sits noticeably below yours at a comparable duration. If the gap isn't
clear, record more enrollment samples before touching the threshold.

**4. Diagnose a shaky reference.** If verification scores look
inconsistent, `diagnose.py` checks whether the enrollment set itself
is the problem (an outlier sample dragging the average off) rather
than the test clip.

```powershell
python diagnose.py enrollment\ reference_embedding.pt
```

## Files

- `setup.ps1` — one-time environment setup (installs the repo-root `requirements.txt`)
- `record_sample.py` — records a WAV from the mic
- `enroll.py` — builds the reference embedding from enrollment samples
- `verify.py` — compares a new clip against the reference
- `diagnose.py` — flags outlier enrollment samples skewing the reference
