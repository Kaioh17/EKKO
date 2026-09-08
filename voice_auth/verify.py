"""
Compares a new voice recording against your enrolled reference embedding.
This is the check you'd run after a wake word fires, before letting a
command through to your bash scripts.

Usage:
    python verify.py reference_embedding.pt test_clip.wav
    python verify.py reference_embedding.pt test_clip.wav --threshold 0.75
    python verify.py reference_embedding.pt                  # verify every .wav in test/
    python verify.py reference_embedding.pt --test-dir other_dir
"""

import argparse
from pathlib import Path

import torch.nn.functional as F

try:
    # Package-relative: resolves when this module is imported as
    # voice_auth.verify, e.g. by listener/vad_listener.py.
    from .enroll import (
        FULL_BUCKET,
        SHORT_BUCKET,
        SHORT_BUCKET_MAX_SECONDS,
        clip_duration_seconds,
        embed_file,
        load_model,
        load_reference,
    )
except ImportError:
    # Bare: resolves when run directly (`python verify.py ...`), where
    # there's no parent package and Python puts this file's own
    # directory on sys.path instead.
    from enroll import (
        FULL_BUCKET,
        SHORT_BUCKET,
        SHORT_BUCKET_MAX_SECONDS,
        clip_duration_seconds,
        embed_file,
        load_model,
        load_reference,
    )

DEFAULT_TEST_DIR = "test"

# A single cosine-similarity threshold can't serve both duration regimes:
# ECAPA-TDNN embeddings from short queries are noisier and score lower
# against the enrolled speaker's own reference than long ones do, even
# for a genuine match (see enroll.py's SHORT_BUCKET comment). These two
# defaults are separately calibrated per bucket rather than one number,
# so short clips aren't held to a threshold tuned for ~4s+ audio.
# Starting points from this project's own enrollment/test recordings
# (voice_auth/test/, listener/captures/): re-tune with diagnose.py as
# more real command clips accumulate.
DEFAULT_THRESHOLD = 0.70  # FULL_BUCKET (queries >= SHORT_BUCKET_MAX_SECONDS)
DEFAULT_SHORT_THRESHOLD = 0.40  # SHORT_BUCKET (queries < SHORT_BUCKET_MAX_SECONDS)


def verify(
    reference: dict,
    model,
    test_wav: str,
    threshold: float | None = None,
    label: str | None = None,
) -> tuple[bool, float]:
    """threshold=None (the default) picks the duration-appropriate default
    threshold automatically. Pass an explicit value to override both
    buckets with a single flat threshold instead, e.g. for A/B testing
    a new cutoff.

    label prefixes the printed lines below. The listener now calls this
    twice per interaction (once on the wake word, once on the command
    that follows) and its log otherwise shows two identical-looking
    verdicts, plus a bare "NO MATCH" that reads like the router's. None,
    the default, prints exactly as it always did.
    """
    prefix = f"[{label}] " if label else ""
    duration = clip_duration_seconds(test_wav)
    bucket = SHORT_BUCKET if duration < SHORT_BUCKET_MAX_SECONDS else FULL_BUCKET
    resolved_threshold = threshold
    if resolved_threshold is None:
        resolved_threshold = (
            DEFAULT_SHORT_THRESHOLD if bucket == SHORT_BUCKET else DEFAULT_THRESHOLD
        )

    test_embedding = embed_file(model, test_wav)
    # reference_embedding.pt is loaded onto whatever device it was saved
    # from (torch.load default), which won't match the model's device
    # whenever that's changed since enrollment -- e.g. enrolling before
    # pvenv had a working CUDA build, then verifying after. Move the
    # query to the reference's device rather than the other way round,
    # so this works regardless of which one is CPU vs CUDA.
    reference_embedding = reference[bucket]
    if test_embedding.device != reference_embedding.device:
        test_embedding = test_embedding.to(reference_embedding.device)
    similarity = F.cosine_similarity(
        reference_embedding.unsqueeze(0), test_embedding.unsqueeze(0)
    ).item()

    is_match = similarity >= resolved_threshold
    print(f"{prefix}File: {test_wav}")
    print(f"{prefix}Duration: {duration:.2f}s  (bucket: {bucket})")
    print(f"{prefix}Similarity: {similarity:.3f}  (threshold: {resolved_threshold})")
    print(f"{prefix}MATCH" if is_match else f"{prefix}NO MATCH")
    return is_match, similarity


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_path", help="Path to reference_embedding.pt")
    parser.add_argument(
        "test_wav", nargs="?", help="Path to the WAV file to verify"
    )
    parser.add_argument(
        "--test-dir",
        default=DEFAULT_TEST_DIR,
        help=f"Directory to scan for .wav files when test_wav is omitted (default: {DEFAULT_TEST_DIR})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Cosine similarity threshold, raise for stricter matching. "
        f"Default: auto-picks {DEFAULT_SHORT_THRESHOLD} for clips shorter than "
        f"{SHORT_BUCKET_MAX_SECONDS}s, {DEFAULT_THRESHOLD} otherwise; "
        "passing a value here overrides both.",
    )
    args = parser.parse_args()

    reference = load_reference(args.reference_path)
    model = load_model()

    if args.test_wav:
        wav_files = [args.test_wav]
    else:
        wav_files = sorted(str(p) for p in Path(args.test_dir).glob("*.wav"))
        if not wav_files:
            parser.error(f"No .wav files found in {args.test_dir!r}")

    for wav_file in wav_files:
        verify(reference, model, wav_file, args.threshold)
        print()
