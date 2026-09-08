"""Prototype: run pyrnnoise over already-captured audio and inspect the effect.

This is deliberately NOT wired into vad_listener.py's pipeline yet. It exists
to answer one question before that wiring happens: does denoising the raw
capture with RNNoise (pyrnnoise) help or hurt faster-whisper's transcription,
on real captures from this room/mic?

What it does:
  1. Reads existing speech_*.wav captures from --in-dir (default
     listener/captures, i.e. vad_listener's own --save-dir), or a single
     file passed via --file.
  2. Runs each one through pyrnnoise's RNNoise and writes the cleaned
     version to --out-dir (default listener/captures_denoised), same
     filename, original left untouched.
  3. With --transcribe, runs faster-whisper on both the original and the
     denoised version of each file and prints them side by side, so the
     effect on transcription is visible directly rather than inferred.

RNNoise only operates at 48kHz (10ms / 480-sample frames internally); the
mic captures at 16kHz (to match openWakeWord/Silero/Whisper). 16k:48k is a
clean 1:3 ratio, so we resample up with scipy.signal.resample_poly before
denoising and back down after -- same tradeoff DeepFilterNet has, there's
no way around the round trip with either library.

We do that resample ourselves rather than relying on RNNoise(sample_rate=
16000)'s own internal resample (denoise_wav()'s in_graph/out_graph, which
goes through audiolab's FFmpeg filter graph): with a 16000 sample_rate that
path is exercised on every frame, and on this pinned/patched av==13.1.0
audiolab install (see note below) that path is where the mumbling traced
back to. Passing RNNoise(sample_rate=48000) with audio we've already
resampled to 48kHz makes that internal graph a no-op passthrough, so the
only resampling that actually runs is scipy's, which is unaffected by any
of that.

Usage:
    python listener/denoise_prototype.py
    python listener/denoise_prototype.py --limit 10
    python listener/denoise_prototype.py --file listener/captures/speech_20260815_111206_767225.wav
    python listener/denoise_prototype.py --file listener/captures/speech_20260815_111206_767225.wav --wet 0.5
    python listener/denoise_prototype.py --transcribe
    python listener/denoise_prototype.py --transcribe --whisper-model medium
    python listener/denoise_prototype.py --in-dir some/other/dir --out-dir some/out/dir

Environment note: pyrnnoise pulls in `audiolab`, which as installed here
assumed an older PyAV API (`av.option`, a subclassable `av.filter.Graph`,
`Codec.canonical_name`) that this venv's av==18.0.0 doesn't have. Fixed by
pinning av==13.1.0 (oldest version with a cp313 Windows wheel that still has
the first two) and patching audiolab's codec.py to fall back to
`Codec.name` where `canonical_name` doesn't exist (cosmetic grouping field
only, not used for anything functional here). faster-whisper was confirmed
to still import and run fine against av==13.1.0.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from pyrnnoise import RNNoise
from scipy.io import wavfile
from scipy.signal import resample_poly

MIC_SAMPLE_RATE = 16000  # matches listener/vad_listener.py's SAMPLE_RATE
RNNOISE_SAMPLE_RATE = 48000  # pyrnnoise.rnnoise.SAMPLE_RATE -- fixed, not configurable
RESAMPLE_UP, RESAMPLE_DOWN = 3, 1  # 16kHz -> 48kHz is a clean 1:3 ratio
DEFAULT_IN_DIR = Path(__file__).resolve().parent / "captures"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "captures_denoised"


def _resample(audio: np.ndarray, up: int, down: int) -> np.ndarray:
    # Filter internally at float precision regardless of input dtype, so an
    # int16 array in doesn't ring/clip the way naive integer resampling can.
    return resample_poly(audio.astype(np.float32), up=up, down=down)


def _to_int16(audio: np.ndarray) -> np.ndarray:
    return np.clip(audio, -32768, 32767).astype(np.int16)


def denoise_file(in_path: Path, out_path: Path, wet: float = 1.0) -> float:
    """Run one 16kHz WAV through RNNoise (which only operates at 48kHz) and
    write the result back out at 16kHz. Returns the mean speech probability
    RNNoise reported across 10ms frames, as a rough signal of how much of
    the clip it thought was actually speech.

    `wet` blends RNNoise's output back with the original signal (1.0 = pure
    RNNoise, 0.0 = pure original). There's no aggressiveness knob in
    pyrnnoise's API -- process_frame() takes no gain/threshold argument, so
    it's full suppression or nothing at the algorithm level -- and full
    suppression is where RNNoise's known "musical noise" artifact (a
    swampy/underwater quality from per-10ms-frame spectral gating) is most
    audible. Backing off with a wet/dry mix is the standard workaround."""
    rate, audio = wavfile.read(in_path)
    if rate != MIC_SAMPLE_RATE:
        raise ValueError(f"{in_path} is {rate}Hz, expected {MIC_SAMPLE_RATE}Hz")
    if audio.ndim > 1:
        audio = audio[:, 0]  # mono only, matches vad_listener's captures

    audio_48k = _to_int16(_resample(audio, RESAMPLE_UP, RESAMPLE_DOWN))

    denoiser = RNNoise(sample_rate=RNNOISE_SAMPLE_RATE)
    chunk = audio_48k.reshape(1, -1)  # [num_channels, num_samples], mono
    denoised_parts, probs = [], []
    for speech_prob, denoised in denoiser.denoise_chunk(chunk, partial=True):
        denoised_parts.append(denoised)
        probs.append(speech_prob)
    denoised_48k = np.concatenate(denoised_parts, axis=-1).flatten()

    denoised_16k = _resample(denoised_48k, RESAMPLE_DOWN, RESAMPLE_UP)
    denoised_16k = denoised_16k[: len(audio)]  # trim resample_poly's rounding

    if wet < 1.0:
        denoised_16k = wet * denoised_16k + (1.0 - wet) * audio.astype(np.float32)
    out_audio = _to_int16(denoised_16k)

    wavfile.write(out_path, MIC_SAMPLE_RATE, out_audio)
    return float(np.mean(probs)) if probs else 0.0


def _load_whisper_model(model_size: str):
    from faster_whisper import WhisperModel

    try:
        gpu_model = WhisperModel(model_size, device="cuda", compute_type="float16")
        warmup = np.zeros(MIC_SAMPLE_RATE, dtype=np.float32)
        segments, _info = gpu_model.transcribe(warmup, language="en")
        list(segments)
        return gpu_model
    except Exception as exc:
        print(f"  faster-whisper: CUDA unavailable ({exc}), falling back to CPU")
        return WhisperModel(model_size, device="cpu", compute_type="int8")


def _transcribe(model, wav_path: Path) -> tuple[str, float, float]:
    segments, _info = model.transcribe(str(wav_path), language="en", vad_filter=True)
    segments = list(segments)
    if not segments:
        return "", 1.0, -1.0
    text = " ".join(s.text.strip() for s in segments).strip()
    no_speech_prob = float(np.mean([s.no_speech_prob for s in segments]))
    avg_logprob = float(np.mean([s.avg_logprob for s in segments]))
    return text, no_speech_prob, avg_logprob


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in-dir", default=str(DEFAULT_IN_DIR), help=f"Source of raw captures (default: {DEFAULT_IN_DIR})")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help=f"Where denoised copies go (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--pattern", default="speech_*.wav", help="Glob pattern for input files (default: speech_*.wav)")
    parser.add_argument("--limit", type=int, default=None, help="Only process the N most recently modified files")
    parser.add_argument("--file", default=None, help="Denoise just this one WAV instead of scanning --in-dir")
    parser.add_argument("--wet", type=float, default=1.0, help="RNNoise/original blend, 1.0=pure RNNoise, 0.0=pure original (default: 1.0). Lower this if the output sounds swampy/underwater.")
    parser.add_argument("--transcribe", action="store_true", help="Also run faster-whisper on original vs. denoised and compare")
    parser.add_argument("--whisper-model", default="small", help="faster-whisper model size for --transcribe (default: small)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.file:
        single = Path(args.file)
        if not single.is_file():
            print(f"No such file: {single}", file=sys.stderr)
            sys.exit(1)
        files = [single]
    else:
        in_dir = Path(args.in_dir)
        if not in_dir.is_dir():
            print(f"No such input directory: {in_dir}", file=sys.stderr)
            sys.exit(1)
        files = sorted(in_dir.glob(args.pattern), key=lambda p: p.stat().st_mtime)
        if args.limit:
            files = files[-args.limit:]
        if not files:
            print(f"No files matching {args.pattern!r} in {in_dir}")
            return

    source = files[0] if args.file else in_dir
    print(f"Denoising {len(files)} file(s) from {source} -> {out_dir}\n")

    whisper_model = None
    if args.transcribe:
        print(f"Loading faster-whisper ({args.whisper_model})...")
        whisper_model = _load_whisper_model(args.whisper_model)
        print()

    for in_path in files:
        out_path = out_dir / in_path.name
        t0 = time.time()
        speech_prob = denoise_file(in_path, out_path, wet=args.wet)
        elapsed = time.time() - t0
        print(f"{in_path.name}  (denoise: {elapsed:.2f}s, mean speech_prob: {speech_prob:.3f})")

        if whisper_model is not None:
            raw_text, raw_nsp, raw_alp = _transcribe(whisper_model, in_path)
            den_text, den_nsp, den_alp = _transcribe(whisper_model, out_path)
            print(f"  raw:      {raw_text!r}  (no_speech_prob={raw_nsp:.3f}, avg_logprob={raw_alp:.3f})")
            print(f"  denoised: {den_text!r}  (no_speech_prob={den_nsp:.3f}, avg_logprob={den_alp:.3f})")
            if raw_text != den_text:
                print("  -> transcript DIFFERS")
        print()

    print(f"Done. Denoised files in {out_dir}, originals in {source} untouched.")


if __name__ == "__main__":
    main()
