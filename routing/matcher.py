"""Embed → compare → threshold, the same shape as voice_auth's speaker check.

enroll.py embeds enrollment WAVs once into a reference and caches it to
disk; verify.py embeds a query and cosine-compares it against that
reference. This does the same thing for text: build_index() embeds every
example phrasing in intents.yaml into one matrix and caches it, match()
embeds a transcript and compares against every row. Both halves live in
one module because together they're smaller than either voice_auth file.

Model is all-MiniLM-L6-v2 (see intent_routing.md for why this and not raw
BERT, and why no FAISS for a few hundred vectors). It's pinned to CPU:
22M params runs in a few milliseconds there, and the GPU is already
carrying Whisper and the speaker embedding model.

Usage:
    python routing/matcher.py "open task manager"     # top 5 scoring examples
    python routing/matcher.py --rebuild
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

import torch
from sentence_transformers import SentenceTransformer

try:
    # Package-relative when imported as routing.matcher; bare when run
    # directly as `python routing/matcher.py`, where there's no parent
    # package. Same two-branch import as voice_auth/verify.py.
    from .config import DEFAULT_CONFIG_PATH, IntentConfig, load_config
except ImportError:
    from config import DEFAULT_CONFIG_PATH, IntentConfig, load_config

_PACKAGE_DIR = Path(__file__).resolve().parent

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_INDEX_PATH = _PACKAGE_DIR / ".index_cache.pt"

# Measured, not borrowed. voice_auth learned that lesson the hard way: a
# generic 0.75 lifted from someone else's benchmark was badly wrong for
# that setup, and the right value only came out of real score gaps (see
# readme.md, "Current status: Stage 1").
#
# From routing/calibrate.py against routing/calibration_phrases.yaml:
#   lowest genuine command scored  0.668  ('open up the process list')
#   highest impostor scored        0.502  (EKKO's own spoken greeting,
#                                          re-transcribed off its own mic)
# 0.58 sits in the middle of that 0.166-wide gap.
#
# One impostor scores above the genuine floor and is deliberately not
# designed around: 'close task manager' hits 0.753 against the example
# 'open task manager'. Sentence embeddings barely encode negation, so no
# threshold separates a command from its own opposite. That's tolerable
# only because every intent here is an open/launch action, so the worst
# case is Task Manager opening when you asked for it to close. Adding a
# destructive or state-reversing intent invalidates that reasoning and
# needs an explicit check, not a threshold nudge.
#
# This is calibrated against 38 hand-written phrases, not months of real
# usage. Re-run calibrate.py after every intents.yaml edit, and grow the
# phrase file from whatever lands near the boundary in
# routing/logs/routing.jsonl.
DEFAULT_THRESHOLD = 0.58


@dataclass(frozen=True)
class IntentIndex:
    """The embedded example phrasings, one matrix row per phrasing."""

    config_hash: str
    model_name: str
    intent_keys: tuple[str, ...]  # row -> which intent the phrasing belongs to
    example_texts: tuple[str, ...]  # row -> the phrasing itself
    matrix: torch.Tensor  # (n_examples, 384), L2-normalised


@dataclass(frozen=True)
class MatchResult:
    intent: str
    score: float
    example: str
    # Best score among examples belonging to a *different* intent. Not used
    # for any decision today; calibrate.py reports the margin between this
    # and score so an ambiguity check can be added later from measured
    # data rather than guessed at now.
    runner_up_intent: str | None
    runner_up_score: float


def load_model(model_name: str = MODEL_NAME) -> SentenceTransformer:
    # First call downloads the model from Hugging Face and caches it
    # locally, same as enroll.py's speechbrain fetch and the
    # openWakeWord download in listener/vad_listener.py.
    return SentenceTransformer(model_name, device="cpu")


def embed(model: SentenceTransformer, texts: list[str]) -> torch.Tensor:
    # Normalised at encode time so similarity is a plain matmul later,
    # no per-query normalisation in the hot path.
    return model.encode(texts, convert_to_tensor=True, normalize_embeddings=True)


def build_index(
    config: IntentConfig, model: SentenceTransformer, model_name: str = MODEL_NAME
) -> IntentIndex:
    # model_name is passed rather than read off the model object: which
    # attribute holds it has moved between sentence-transformers releases,
    # and it's only ever used to invalidate the cache, so taking the
    # caller's word for it is both simpler and more stable.
    pairs = config.example_pairs()
    intent_keys, example_texts = zip(*pairs)
    return IntentIndex(
        config_hash=config.file_hash,
        model_name=model_name,
        intent_keys=tuple(intent_keys),
        example_texts=tuple(example_texts),
        matrix=embed(model, list(example_texts)),
    )


def save_index(index: IntentIndex, path: str | Path = DEFAULT_INDEX_PATH) -> None:
    torch.save(
        {
            "config_hash": index.config_hash,
            "model_name": index.model_name,
            "intent_keys": list(index.intent_keys),
            "example_texts": list(index.example_texts),
            "matrix": index.matrix,
        },
        path,
    )


def load_index(
    path: str | Path, config: IntentConfig, model_name: str = MODEL_NAME
) -> IntentIndex | None:
    """Returns None (rather than raising) whenever the cache can't be
    trusted: missing, unreadable, built from a different intents.yaml, or
    built with a different model. The caller rebuilds. Mirrors
    enroll.load_reference()'s tolerance of an out-of-date file on disk,
    the difference being that this one is derived data, so it can just be
    regenerated instead of degraded gracefully.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        # weights_only=True: this file is only ever tensors and plain
        # containers, and it sits in a directory a config edit regenerates,
        # so there's no reason to allow arbitrary pickle execution.
        raw = torch.load(path, weights_only=True)
    except Exception as exc:
        print(f"[matcher] ignoring unreadable index cache {path}: {exc}")
        return None

    if raw.get("config_hash") != config.file_hash:
        print("[matcher] intents.yaml changed since the index was built, rebuilding")
        return None
    if raw.get("model_name") != model_name:
        print(f"[matcher] index was built with {raw.get('model_name')!r}, rebuilding")
        return None

    return IntentIndex(
        config_hash=raw["config_hash"],
        model_name=raw["model_name"],
        intent_keys=tuple(raw["intent_keys"]),
        example_texts=tuple(raw["example_texts"]),
        matrix=raw["matrix"],
    )


def get_index(
    config: IntentConfig,
    model: SentenceTransformer,
    path: str | Path = DEFAULT_INDEX_PATH,
    rebuild: bool = False,
    model_name: str = MODEL_NAME,
) -> IntentIndex:
    """Load the cached index, or build and cache it. This is what startup
    calls, so that embedding the example set is a first-run cost rather
    than a per-launch one.
    """
    if not rebuild:
        cached = load_index(path, config, model_name)
        if cached is not None:
            return cached
    index = build_index(config, model, model_name)
    save_index(index, path)
    print(f"[matcher] embedded {len(index.example_texts)} example phrasings -> {path}")
    return index


def _scores(index: IntentIndex, model: SentenceTransformer, transcript: str) -> torch.Tensor:
    """Cosine similarity of the transcript against every example row.

    Both sides are already L2-normalised, so this is a plain matmul.
    Clamped because float32 rounding puts an exact match fractionally
    over 1.0 (measured: 1.0000001 for a transcript identical to an
    example), and IntentBundle validates that scores really are cosine
    similarities.
    """
    query = embed(model, [transcript])  # (1, 384)
    return torch.clamp((index.matrix @ query.T).squeeze(1), -1.0, 1.0)  # (n_examples,)


def match(index: IntentIndex, model: SentenceTransformer, transcript: str) -> MatchResult:
    """Highest-scoring example wins. Both sides are already L2-normalised,
    so the matmul is cosine similarity directly.
    """
    scores = _scores(index, model, transcript)

    best = int(torch.argmax(scores))
    best_intent = index.intent_keys[best]

    # Best row belonging to any other intent, for the margin calibrate.py
    # reports. A single-intent config has no runner-up at all.
    other = [i for i, key in enumerate(index.intent_keys) if key != best_intent]
    runner_up_intent, runner_up_score = None, 0.0
    if other:
        runner_up = max(other, key=lambda i: scores[i].item())
        runner_up_intent = index.intent_keys[runner_up]
        runner_up_score = scores[runner_up].item()

    return MatchResult(
        intent=best_intent,
        score=scores[best].item(),
        example=index.example_texts[best],
        runner_up_intent=runner_up_intent,
        runner_up_score=runner_up_score,
    )


def top_matches(
    index: IntentIndex, model: SentenceTransformer, transcript: str, k: int = 5
) -> list[tuple[str, str, float]]:
    """(intent, example, score) for the k highest-scoring rows. Debugging
    aid for "why did that route there", not used by route.py.
    """
    scores = _scores(index, model, transcript)
    k = min(k, len(index.example_texts))
    top = torch.topk(scores, k)
    return [
        (index.intent_keys[i], index.example_texts[i], score)
        for i, score in zip(top.indices.tolist(), top.values.tolist())
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", nargs="?", help="Text to score against the example set")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to the intent config (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--index",
        default=str(DEFAULT_INDEX_PATH),
        help=f"Path to the cached embedding matrix (default: {DEFAULT_INDEX_PATH}).",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Re-embed the example set even if the cache is still valid.",
    )
    parser.add_argument("--top", type=int, default=5, help="How many rows to show (default: 5).")
    args = parser.parse_args()

    loaded_config = load_config(args.config)
    loaded_model = load_model()
    loaded_index = get_index(loaded_config, loaded_model, args.index, rebuild=args.rebuild)

    if not args.transcript:
        print(
            f"Index: {len(loaded_index.example_texts)} phrasings across "
            f"{len(set(loaded_index.intent_keys))} intents, "
            f"model {loaded_index.model_name}"
        )
        raise SystemExit(0)

    print(f"Transcript: {args.transcript!r}\n")
    for intent_key, example, score in top_matches(
        loaded_index, loaded_model, args.transcript, args.top
    ):
        print(f"  {score:.3f}  {intent_key:<20} {example!r}")
    print(f"\nThreshold: {DEFAULT_THRESHOLD} (placeholder, run calibrate.py)")
