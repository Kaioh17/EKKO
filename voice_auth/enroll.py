"""
Builds your reference voice embedding from a folder of enrollment WAVs.

Record 5-10 samples first with record_sample.py, ideally saying different
phrases at different times of day, so the embedding captures natural
variation in your voice rather than one specific recording.

Usage:
    python enroll.py enrollment/ reference_embedding.pt
"""

import argparse
import glob
import os

import numpy as np
import torch
import torchaudio
from scipy.io.wavfile import read as wav_read
from speechbrain.inference.speaker import EncoderClassifier
from speechbrain.utils.fetching import LocalStrategy

MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
MODEL_CACHE = "pretrained_models/spkrec-ecapa-voxceleb"

# ECAPA-TDNN's stats-pooling layer needs enough frames to get a stable
# read on a speaker, so a cosine similarity between a short query and a
# reference built from long (~4s) enrollment clips is systematically
# suppressed even for the enrolled speaker, an enrollment/test duration
# mismatch, not evidence of a different voice. Padding a short query out
# by repeating it doesn't fix this (verified empirically: repeated frames
# don't add new statistics for the pooling layer to work with), so the
# fix is on the enrollment side instead: build a second reference from
# short crops of the same enrollment clips, and pick whichever reference
# matches the query's own duration at verify time. See voice_auth/auedio.md.
SHORT_BUCKET = "short"
FULL_BUCKET = "full"
SHORT_CROP_SECONDS = 2.0
# Queries at or above this length compare against the uncropped reference;
# below it, they compare against the short, duration-matched one.
SHORT_BUCKET_MAX_SECONDS = 3.5


def load_model():
    # First call downloads the pretrained model from Hugging Face Hub
    # and caches it locally. Later calls just load from cache.
    # run_opts pins the device explicitly, avoids the "cuda" vs "cuda:0"
    # string parsing warning, harmless either way but this silences it.
    #
    # local_strategy=COPY instead of the default SYMLINK: on native Windows,
    # creating real symlinks needs Developer Mode or admin rights, and this
    # cache directory has previously held WSL-native symlinks (WinError 1920)
    # that plain Windows Python can't stat. Copying real files sidesteps both.
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    return EncoderClassifier.from_hparams(
        source=MODEL_SOURCE,
        savedir=MODEL_CACHE,
        run_opts={"device": device},
        local_strategy=LocalStrategy.COPY,
    )


def embed_file(model, wav_path: str, max_seconds: float | None = None) -> torch.Tensor:
    # Reads the WAV directly with scipy instead of torchaudio.load(),
    # sidesteps torchaudio's TorchCodec backend requirement entirely.
    # Safe here since we control the WAV format end to end, it's always
    # the int16 PCM written by record_sample.py.
    sample_rate, audio_int16 = wav_read(wav_path)
    audio_float32 = audio_int16.astype(np.float32) / 32767.0
    signal = torch.from_numpy(audio_float32).unsqueeze(0)  # shape: (1, samples)

    if sample_rate != 16000:
        signal = torchaudio.functional.resample(signal, sample_rate, 16000)

    if max_seconds is not None:
        # Crop, don't pad: this is how build_reference() makes a short,
        # duration-matched reference out of the (longer) enrollment
        # clips. A clip already shorter than max_seconds is left alone.
        max_samples = int(max_seconds * 16000)
        signal = signal[:, :max_samples]

    with torch.no_grad():
        embedding = model.encode_batch(signal)
    return embedding.squeeze(0).squeeze(0)  # shape: (192,)


def clip_duration_seconds(wav_path: str) -> float:
    sample_rate, audio_int16 = wav_read(wav_path)
    return len(audio_int16) / sample_rate


def build_reference(wav_files: list[str], model) -> dict[str, torch.Tensor]:
    """Builds both reference buckets from the same enrollment clips: the
    full-length one (existing behavior) and a short, duration-matched one
    for verifying short queries against. See SHORT_CROP_SECONDS above.
    """
    full, short = [], []
    for wav_path in wav_files:
        print(f"  embedding {wav_path}")
        full.append(embed_file(model, wav_path))
        short.append(embed_file(model, wav_path, max_seconds=SHORT_CROP_SECONDS))
    # Average across samples gives a more stable reference than any
    # single recording, since it smooths out per-recording noise.
    return {
        FULL_BUCKET: torch.stack(full).mean(dim=0),
        SHORT_BUCKET: torch.stack(short).mean(dim=0),
    }


def load_reference(reference_path: str) -> dict[str, torch.Tensor]:
    """Loads a reference_embedding.pt, transparently upgrading the old
    format (a single tensor, pre-dating duration buckets) so files built
    by an older enroll.py keep working: both buckets just point at the
    same tensor, which is exactly today's un-adapted behavior. Re-run
    enroll.py to get a real short-duration reference and the accuracy
    gain that comes with it.
    """
    reference = torch.load(reference_path)
    if isinstance(reference, torch.Tensor):
        print(
            f"  note: {reference_path} is in the old (pre-duration-bucket) "
            "format, short clips won't get the duration-matched reference. "
            "Re-run enroll.py to regenerate it."
        )
        return {FULL_BUCKET: reference, SHORT_BUCKET: reference}
    return reference


def main(enrollment_dir: str, output_path: str) -> None:
    wav_files = sorted(glob.glob(os.path.join(enrollment_dir, "*.wav")))
    if not wav_files:
        raise SystemExit(f"No .wav files found in {enrollment_dir}")

    print(f"Found {len(wav_files)} enrollment samples.")
    model = load_model()

    reference = build_reference(wav_files, model)
    torch.save(reference, output_path)
    print(f"Saved reference embedding ({FULL_BUCKET} + {SHORT_BUCKET} buckets) to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("enrollment_dir", help="Folder of enrollment WAV files")
    parser.add_argument("output_path", help="Where to save the reference embedding")
    args = parser.parse_args()
    main(args.enrollment_dir, args.output_path)
