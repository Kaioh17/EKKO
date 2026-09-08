"""
Trims listener/captures/ down to the N most recent speech segments.

vad_listener.py writes a WAV per detected speech segment, one per
"someone spoke near the mic" event, whether or not it ever passes wake
word / verification. Left running, that directory grows without bound.
This just keeps the newest --keep files and deletes the rest.

"Newest" is determined by filename, not filesystem mtime: vad_listener.py
names every capture speech_{%Y%m%d_%H%M%S_%f}.wav (see _save_segment in
vad_listener.py), so a plain lexicographic sort is already chronological
order and doesn't depend on mtimes surviving a copy/backup/git checkout.
Files that don't match that naming pattern are left alone.

transcripts.jsonl (the verified-command log) is never touched by this
script, only the WAV segments themselves.

Usage (run from anywhere, --dir resolves relative to this file, not cwd):
    python listener/prune_captures.py                 # keep 10 newest, delete the rest
    python listener/prune_captures.py --keep 25
    python listener/prune_captures.py --dry-run        # show what would be deleted, don't touch disk
    python listener/prune_captures.py --dir some/other/dir
"""

import argparse
from pathlib import Path

DEFAULT_KEEP = 10
# Anchored to this file, same reasoning as vad_listener.py's DEFAULT_SAVE_DIR:
# usage examples run this as `python listener/prune_captures.py` from the
# repo root, where a bare relative "captures" would land at the repo root
# instead of listener/captures.
DEFAULT_DIR = str(Path(__file__).resolve().parent / "captures")
CAPTURE_GLOB = "speech_*.wav"


def prune(save_dir: str, keep: int, dry_run: bool = False) -> tuple[list[Path], list[Path]]:
    """Returns (kept, deleted), newest first. Deletes on disk unless dry_run."""
    captures = sorted(Path(save_dir).glob(CAPTURE_GLOB), reverse=True)
    kept, stale = captures[:keep], captures[keep:]
    if not dry_run:
        for path in stale:
            path.unlink()
    return kept, stale


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default=DEFAULT_DIR, help=f"captures directory (default: {DEFAULT_DIR})")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"number of newest segments to keep (default: {DEFAULT_KEEP})")
    parser.add_argument("--dry-run", action="store_true", help="print what would be deleted without touching disk")
    args = parser.parse_args()

    if args.keep < 0:
        parser.error("--keep must be >= 0")

    kept, stale = prune(args.dir, args.keep, dry_run=args.dry_run)

    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"{args.dir}: {len(kept)} kept, {len(stale)} {'stale' if args.dry_run else 'removed'}")
    for path in stale:
        print(f"  {verb}: {path.name}")


if __name__ == "__main__":
    main()
