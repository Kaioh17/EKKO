"""Domain detection for the conversational (Gemini-fallback) layer only.

This is a sibling to matcher.py's embed/compare/threshold shape, not an
extension of it -- kept as a separate module and a separate cache file so
it's structurally obvious a domain match can never reach execute.py or a
PowerShell handler. A domain match only ever picks which system-prompt
bundle llm_fallback/gemini/fallback_gemini.py hands to Gemini for one
open-ended call; routing/route.py and routing/intents.yaml (the actual
command security boundary) are untouched by anything in this file.

Reuses matcher.py's already-loaded MiniLM model and its cosine-similarity
matmul directly: DomainIndex carries the same shape IntentIndex does (a
.matrix attribute of L2-normalised embeddings), so matcher._scores() works
against it unmodified -- no change needed to matcher.py at all.

Usage:
    python routing/domains.py "what account should I use"
    python routing/domains.py --rebuild "should I start investing"
"""

import argparse
import datetime
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import torch
import yaml
from sentence_transformers import SentenceTransformer

try:
    from . import matcher
except ImportError:
    import matcher

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
DEFAULT_REGISTRY_PATH = _PROJECT_ROOT / "domains" / "registry.yaml"

# Same directory routing/route.py's routing.jsonl already lives in -- a
# sibling log for the same "domain expert" decision, one JSON object per
# match() call regardless of outcome (see log_match()/DomainRouter.match()),
# not just the ones that happened to fire. This is what answers "is domain
# attribution actually happening" from real usage instead of console prints
# that scroll away.
DEFAULT_LOG_PATH = _PACKAGE_DIR / "logs" / "domains.jsonl"


class DomainConfigError(ValueError):
    """Raised with every validation problem found, not just the first --
    same reasoning routing/config.py's ConfigError gives: this file is
    hand-edited and should fail loudly at startup, not silently later.
    """


@dataclass(frozen=True)
class DomainSpec:
    key: str
    anchors_path: Path
    guardrails_path: Path
    persona_path: Path
    knowledge_path: Path
    threshold: float
    continuity_boost: float


@dataclass(frozen=True)
class DomainIndex:
    """Same shape as matcher.IntentIndex on purpose -- matcher._scores()
    only ever reads .matrix, so this is a drop-in for that function
    without touching matcher.py.
    """

    domain_key: str
    anchors_hash: str
    model_name: str
    example_texts: tuple[str, ...]
    matrix: torch.Tensor  # (n_examples, 384), L2-normalised


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> dict[str, DomainSpec]:
    path = Path(path)
    if not path.exists():
        raise DomainConfigError(f"Domain registry not found at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise DomainConfigError(f"{path} must be a non-empty mapping of domain key -> definition")

    problems: list[str] = []
    specs: dict[str, DomainSpec] = {}
    for key, body in raw.items():
        spec = _parse_domain(str(key), body, problems)
        if spec is not None:
            specs[str(key)] = spec

    if problems:
        raise DomainConfigError(f"{path} has {len(problems)} problem(s):\n  - " + "\n  - ".join(problems))
    return specs


def _parse_domain(key: str, body: object, problems: list[str]) -> DomainSpec | None:
    if not isinstance(body, dict):
        problems.append(f"{key}: must be a mapping, got {type(body).__name__}")
        return None

    required = {"anchors", "guardrails", "persona", "knowledge", "threshold", "continuity_boost"}
    missing = required - set(body)
    if missing:
        problems.append(f"{key}: missing key(s) {sorted(missing)}")
        return None

    paths = {}
    for field in ("anchors", "guardrails", "persona", "knowledge"):
        resolved = (_PROJECT_ROOT / str(body[field])).resolve()
        if not resolved.is_file():
            problems.append(f"{key}.{field}: no such file, {resolved}")
        paths[field] = resolved

    threshold = body["threshold"]
    boost = body["continuity_boost"]
    if not isinstance(threshold, (int, float)):
        problems.append(f"{key}.threshold: must be a number, got {threshold!r}")
        threshold = 1.0  # unreachable placeholder so the domain never fires if this slipped past validation
    if not isinstance(boost, (int, float)):
        problems.append(f"{key}.continuity_boost: must be a number, got {boost!r}")
        boost = 0.0

    if problems:
        return None
    return DomainSpec(
        key=key,
        anchors_path=paths["anchors"],
        guardrails_path=paths["guardrails"],
        persona_path=paths["persona"],
        knowledge_path=paths["knowledge"],
        threshold=float(threshold),
        continuity_boost=float(boost),
    )


def _load_anchor_examples(domain: DomainSpec) -> list[str]:
    raw = yaml.safe_load(domain.anchors_path.read_text(encoding="utf-8"))
    examples = (raw or {}).get("examples")
    if not isinstance(examples, list) or not examples:
        raise DomainConfigError(f"{domain.anchors_path}: 'examples' must be a non-empty list")
    return [str(e) for e in examples]


def build_domain_index(
    domain: DomainSpec, model: SentenceTransformer, model_name: str = matcher.MODEL_NAME
) -> DomainIndex:
    examples = _load_anchor_examples(domain)
    return DomainIndex(
        domain_key=domain.key,
        anchors_hash=_file_hash(domain.anchors_path),
        model_name=model_name,
        example_texts=tuple(examples),
        matrix=matcher.embed(model, examples),
    )


def _cache_path(domain: DomainSpec) -> Path:
    return domain.anchors_path.parent / ".anchor_cache.pt"


def save_domain_index(index: DomainIndex, path: Path) -> None:
    torch.save(
        {
            "domain_key": index.domain_key,
            "anchors_hash": index.anchors_hash,
            "model_name": index.model_name,
            "example_texts": list(index.example_texts),
            "matrix": index.matrix,
        },
        path,
    )


def load_domain_index(path: Path, domain: DomainSpec, model_name: str) -> DomainIndex | None:
    """Same tolerant-cache reasoning as matcher.load_index(): missing,
    unreadable, or built from a different anchors.yaml/model is treated
    as "rebuild", never as an error.
    """
    if not path.exists():
        return None
    try:
        raw = torch.load(path, weights_only=True)
    except Exception as exc:
        print(f"[domains] ignoring unreadable anchor cache {path}: {exc}")
        return None

    if raw.get("anchors_hash") != _file_hash(domain.anchors_path):
        return None
    if raw.get("model_name") != model_name:
        return None
    return DomainIndex(
        domain_key=raw["domain_key"],
        anchors_hash=raw["anchors_hash"],
        model_name=raw["model_name"],
        example_texts=tuple(raw["example_texts"]),
        matrix=raw["matrix"],
    )


def get_domain_index(
    domain: DomainSpec,
    model: SentenceTransformer,
    rebuild: bool = False,
    model_name: str = matcher.MODEL_NAME,
) -> DomainIndex:
    path = _cache_path(domain)
    if not rebuild:
        cached = load_domain_index(path, domain, model_name)
        if cached is not None:
            return cached
    index = build_domain_index(domain, model, model_name)
    save_domain_index(index, path)
    print(f"[domains] embedded {len(index.example_texts)} anchor phrasings for {domain.key!r} -> {path}")
    return index


def score_domain(index: DomainIndex, model: SentenceTransformer, transcript: str) -> float:
    """Max cosine similarity against this domain's anchor phrasings.
    Reuses matcher._scores(), which only reads .matrix -- DomainIndex has
    the same shape as IntentIndex, so no change to matcher.py is needed.
    """
    scores = matcher._scores(index, model, transcript)
    return float(torch.max(scores))


@dataclass(frozen=True)
class DomainScore:
    raw: float
    boost: float
    boosted: float
    threshold: float
    cleared: bool


def score_all_domains(
    transcript: str,
    domains: dict[str, DomainSpec],
    model: SentenceTransformer,
    continuity: "ContinuityBufferProtocol | None" = None,
    indexes: dict[str, DomainIndex] | None = None,
) -> tuple[str | None, dict[str, DomainScore]]:
    """Every domain's raw/boosted score against one transcript, plus which
    (if any) wins -- the shared computation behind both match_domain() (the
    live decision) and log_match() (the audit trail), so logging never has
    to re-embed the transcript against every domain a second time.

    Same tie-break as match_domain() docstring: among domains that already
    clear their own boosted threshold, the highest raw score wins --
    continuity can push a recently-active domain over the line, but never
    decides between two domains that would both already qualify without it.
    """
    if indexes is None:
        indexes = {key: get_domain_index(spec, model) for key, spec in domains.items()}

    scores: dict[str, DomainScore] = {}
    best_key: str | None = None
    best_raw = -1.0
    for key, spec in domains.items():
        index = indexes[key]
        raw = score_domain(index, model, transcript)
        boost = continuity.boost_for(key) if continuity is not None else 0.0
        boost = min(boost, spec.continuity_boost)
        boosted = raw + boost
        cleared = boosted >= spec.threshold
        scores[key] = DomainScore(raw=raw, boost=boost, boosted=boosted, threshold=spec.threshold, cleared=cleared)
        if cleared and (best_key is None or raw > best_raw):
            best_key, best_raw = key, raw

    return best_key, scores


def match_domain(
    transcript: str,
    domains: dict[str, DomainSpec],
    model: SentenceTransformer,
    continuity: "ContinuityBufferProtocol | None" = None,
    indexes: dict[str, DomainIndex] | None = None,
) -> tuple[str, float] | None:
    """Best-scoring domain that clears its own (continuity-boosted)
    threshold, or None. Thin wrapper over score_all_domains() -- kept
    around as the simple two-value return callers/CLI usage already expect.
    """
    best_key, scores = score_all_domains(transcript, domains, model, continuity=continuity, indexes=indexes)
    if best_key is None:
        return None
    return best_key, scores[best_key].boosted


def log_match(
    transcript: str,
    matched: str | None,
    scores: dict[str, "DomainScore"],
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> None:
    """Append one JSON object per domain-match attempt, matched or not --
    same append-only JSONL shape and same "log every call, not just the
    interesting ones" reasoning as routing/route.py's log_decision(). This
    is what makes "is domain attribution actually happening" answerable
    from a file instead of scrollback that a restarted listener loses.
    """
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "transcript": transcript,
        "matched": matched,
        "scores": {
            key: {
                "raw": round(s.raw, 3),
                "boost": round(s.boost, 3),
                "boosted": round(s.boosted, 3),
                "threshold": s.threshold,
                "cleared": s.cleared,
            }
            for key, s in scores.items()
        },
    }
    log_path = Path(log_path)
    os.makedirs(log_path.parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


class DomainRouter:
    """Registry, shared model, per-domain indexes, and the continuity
    buffer, loaded/constructed once and held for the process lifetime --
    same "load at startup, not per call" lifecycle Router (routing/route.py)
    already uses, and this holds the *same* model instance rather than
    loading a second copy of MiniLM into memory (listener/vad_listener.py
    passes Router.model in at construction).
    """

    def __init__(
        self,
        domains: dict[str, DomainSpec],
        model: SentenceTransformer,
        continuity: "ContinuityBufferLike",
        indexes: dict[str, DomainIndex] | None = None,
        log_path: str | Path | None = DEFAULT_LOG_PATH,
    ) -> None:
        self.domains = domains
        self.model = model
        self.continuity = continuity
        self.indexes = indexes or {key: get_domain_index(spec, model) for key, spec in domains.items()}
        self.log_path = log_path

    @classmethod
    def load(
        cls,
        model: SentenceTransformer,
        registry_path: str | Path = DEFAULT_REGISTRY_PATH,
        continuity: "ContinuityBufferLike | None" = None,
        rebuild: bool = False,
        log_path: str | Path | None = DEFAULT_LOG_PATH,
    ) -> "DomainRouter":
        if continuity is None:
            # Imported lazily to avoid a hard dependency from routing/ (a
            # command-routing package) on listener/ (an application-layer
            # package) at module load time -- only needed if the caller
            # didn't already construct its own buffer.
            try:
                from listener.continuity import ContinuityBuffer
            except ImportError:
                from continuity import ContinuityBuffer
            continuity = ContinuityBuffer()
        domains = load_registry(registry_path)
        indexes = {key: get_domain_index(spec, model, rebuild=rebuild) for key, spec in domains.items()}
        return cls(domains, model, continuity, indexes, log_path=log_path)

    def match(self, transcript: str) -> tuple[str, float] | None:
        """Same "log every call, not just the ones that matched" posture
        Router.route() already applies to command routing -- score_all_domains()
        does the one embedding pass this and log_match() both need, so
        logging never costs a second model call.
        """
        best_key, scores = score_all_domains(transcript, self.domains, self.model, continuity=self.continuity, indexes=self.indexes)
        if self.log_path is not None:
            log_match(transcript, best_key, scores, self.log_path)
        if best_key is None:
            return None
        return best_key, scores[best_key].boosted

    def record(self, transcript: str, domain: str | None) -> None:
        self.continuity.record(transcript, domain)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", nargs="?", help="Text to score against every domain's anchors")
    parser.add_argument(
        "--registry",
        default=str(DEFAULT_REGISTRY_PATH),
        help=f"Path to the domain registry (default: {DEFAULT_REGISTRY_PATH}).",
    )
    parser.add_argument("--rebuild", action="store_true", help="Re-embed every domain's anchors.")
    args = parser.parse_args()

    loaded_domains = load_registry(args.registry)
    loaded_model = matcher.load_model()
    loaded_indexes = {
        key: get_domain_index(spec, loaded_model, rebuild=args.rebuild) for key, spec in loaded_domains.items()
    }

    if not args.transcript:
        for key, spec in loaded_domains.items():
            print(f"{key}: {len(loaded_indexes[key].example_texts)} anchors, threshold={spec.threshold}")
        raise SystemExit(0)

    print(f"Transcript: {args.transcript!r}\n")
    for key, spec in loaded_domains.items():
        raw = score_domain(loaded_indexes[key], loaded_model, args.transcript)
        flag = " <-- MATCH" if raw >= spec.threshold else ""
        print(f"  {raw:.3f}  {key:<10} (threshold {spec.threshold}){flag}")

    result = match_domain(args.transcript, loaded_domains, loaded_model, indexes=loaded_indexes)
    print(f"\nResult: {result if result else 'no domain match (falls through to flat prompt)'}")
