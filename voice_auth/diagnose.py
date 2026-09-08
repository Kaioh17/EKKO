"""
Diagnoses whether a low verification score is caused by a noisy
reference embedding or by the test clip itself.

Computes:
  1. Pairwise similarity between every enrollment sample and every
     other one. If these are inconsistent with each other, the
     averaged reference is a blurry centroid, not a clean signal.
  2. Similarity of each individual enrollment sample against the
     final averaged reference. Low scores here confirm the average
     is being pulled around by one or two noisy recordings.

Usage:
    python diagnose.py enrollment/ reference_embedding.pt
"""

import argparse
import glob
import os

import torch.nn.functional as F

from enroll import FULL_BUCKET, embed_file, load_model, load_reference


def main(enrollment_dir: str, reference_path: str) -> None:
    wav_files = sorted(glob.glob(os.path.join(enrollment_dir, "*.wav")))
    if not wav_files:
        raise SystemExit(f"No .wav files found in {enrollment_dir}")

    model = load_model()
    names = [os.path.basename(f) for f in wav_files]
    embeddings = [embed_file(model, f) for f in wav_files]
    # Enrollment samples are the full-length (~4s) recordings themselves,
    # so they're only meaningfully compared against the full bucket, not
    # the short duration-matched one, that bucket exists to compare
    # against real short queries instead. See verify.py for that.
    reference = load_reference(reference_path)[FULL_BUCKET]
    # See verify.py: reference_embedding.pt loads onto whichever device it
    # was saved from, which won't match a freshly embedded sample whenever
    # that's changed since enrollment (e.g. CUDA becoming available after
    # the reference was built on CPU).
    if reference.device != embeddings[0].device:
        reference = reference.to(embeddings[0].device)

    print("\n--- Each sample vs the averaged reference ---")
    for name, emb in zip(names, embeddings):
        sim = F.cosine_similarity(emb.unsqueeze(0), reference.unsqueeze(0)).item()
        flag = "  <-- low, pulling the average off" if sim < 0.7 else ""
        print(f"  {name}: {sim:.3f}{flag}")

    print("\n--- Pairwise similarity between enrollment samples ---")
    print("    " + "  ".join(f"{n[:8]:>8}" for n in names))
    for i, emb_i in enumerate(embeddings):
        row = []
        for emb_j in embeddings:
            sim = F.cosine_similarity(
                emb_i.unsqueeze(0), emb_j.unsqueeze(0)
            ).item()
            row.append(f"{sim:.2f}")
        print(f"{names[i][:8]:>8}  " + "  ".join(f"{v:>8}" for v in row))

    print(
        "\nIf most pairwise scores are 0.8+, your enrollment set is "
        "consistent, the reference is trustworthy. If some pairs score "
        "well below others, those specific recordings are the problem, "
        "consider re-recording them rather than adding more."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("enrollment_dir", help="Folder of enrollment WAV files")
    parser.add_argument("reference_path", help="Path to reference_embedding.pt")
    args = parser.parse_args()
    main(args.enrollment_dir, args.reference_path)
