"""Transcript in, IntentBundle out. The entry point for the whole layer.

Standalone by design: this takes a plain string, so it can be developed
and tested without a mic, without Whisper, and without the execution
layer existing. listener/vad_listener.py will eventually call Router.route()
on the transcript it already produces (see its TODO(stage 3)), and that
wiring is a small change precisely because nothing here depends on audio.

There is no LLM anywhere in this path, and that's an architectural rule
for this project rather than a preference: the closed intent set in
intents.yaml is what structurally guarantees only pre-approved actions
can ever be named, and a prompted model would enforce that by instruction
only. See intent_routing.md.

Known limitation, deliberate: one utterance routes to at most one intent.
"Open task manager and ghelper" will score against both and return
whichever wins, not both. Multi-intent splitting is out of scope for this
build, it doubles the failure surface and phrases like "turn it up and
down" are genuinely ambiguous.

Usage:
    python routing/route.py "open task manager"
    python routing/route.py --threshold 0.6 "pull up chrome"
    python routing/route.py --explain "fire up steam"
    python routing/route.py --batch listener/captures/transcripts.jsonl
    python routing/route.py --rebuild-index
"""

import argparse
import datetime
import json
import os
import re
from pathlib import Path

from sentence_transformers import SentenceTransformer

try:
    from .bundle import IntentBundle, RoutingStatus
    from .config import DEFAULT_CONFIG_PATH, IntentConfig, IntentSpec, load_config, normalise
    from .matcher import (
        DEFAULT_INDEX_PATH,
        DEFAULT_THRESHOLD,
        IntentIndex,
        get_index,
        load_model,
        match,
        top_matches,
    )
    from .slots import extract
except ImportError:
    from bundle import IntentBundle, RoutingStatus
    from config import DEFAULT_CONFIG_PATH, IntentConfig, IntentSpec, load_config, normalise
    from matcher import (
        DEFAULT_INDEX_PATH,
        DEFAULT_THRESHOLD,
        IntentIndex,
        get_index,
        load_model,
        match,
        top_matches,
    )
    from slots import extract

_PACKAGE_DIR = Path(__file__).resolve().parent
# PRIVACY: this file accumulates the text of everything spoken at EKKO
# after a verified wake word, matched or not. That's deliberate, it's what
# makes threshold calibration and the Tier 2 classifier upgrade possible
# (see intent_routing.md), but it is not a side effect anyone should
# discover by accident. It's called out in routing/README.md and readme.md,
# and --no-log turns it off.
DEFAULT_LOG_PATH = _PACKAGE_DIR / "logs" / "routing.jsonl"


def _trigger_match(config: IntentConfig, transcript: str) -> IntentSpec | None:
    """The intent named by a literal free_text trigger phrase in the
    transcript, or None if no trigger appears.

    Real usage exposed why this needs to exist: "search for how to walk in
    PS1" contains the trigger "search for" verbatim and still scored 0.322
    against web_search's examples, well under threshold, because MiniLM
    embeds the whole sentence and "how to walk in PS1" is far enough from
    every example to drag the average down. That's not a threshold or
    example-set problem, it's a ceiling on what whole-sentence similarity
    can promise for content the trigger word deliberately doesn't
    constrain.

    A free_text slot's trigger phrase, unlike an intent's example
    phrasings, isn't a similarity anchor, it's a hand-authored, exact
    string the config author chose specifically because saying it means
    the intent, same certainty class as a closed_vocabulary alias (see
    slots.find_value). So when one is literally present, this bypasses
    match()/threshold entirely rather than asking an approximate method to
    re-derive a decision that's already deterministic. Only fires on
    intents whose slot design already promises that certainty; intents
    that classify purely on phrasing (open_task_manager, open_app, ...)
    are untouched, still decided by embedding similarity same as before.

    Checked before the embedding matcher runs, not after: with one
    free_text intent today this can't disagree with the embedding result,
    but it's ordered this way on purpose so it never could, if a second
    free_text intent is added later, the first trigger found in config
    order wins rather than whichever the embedding matcher happened to
    prefer.
    """
    normalised = normalise(transcript)
    for intent in config.intents:
        for slot in intent.slots:
            if slot.type != "free_text":
                continue
            if any(re.search(rf"\b{re.escape(trigger)}\b", normalised) for trigger in slot.triggers):
                return intent
    return None


def route(
    transcript: str,
    index: IntentIndex,
    model: SentenceTransformer,
    config: IntentConfig,
    threshold: float = DEFAULT_THRESHOLD,
    source: str = "voice",
) -> IntentBundle:
    """The routing decision, as a pure function of its inputs: the same
    transcript against the same config always produces the same bundle.

    `source` is purely a log tag ("voice" or "chat", see
    routing/bundle.py) -- it never affects the routing decision itself.
    """
    if not transcript or not transcript.strip():
        return IntentBundle.empty_transcript(transcript or "", source=source)

    triggered = _trigger_match(config, transcript)
    if triggered is not None:
        # A trigger phrase is as certain as routing gets, see
        # _trigger_match's docstring, so this skips match()/threshold
        # rather than asking embedding similarity to confirm a decision
        # that's already made. score=1.0 reflects that certainty in the
        # log/bundle rather than implying a coincidentally perfect
        # embedding match.
        slots, missing = extract(triggered, transcript)
        if missing is not None:
            return IntentBundle.slot_missing(
                transcript,
                triggered.key,
                1.0,
                "(trigger phrase, no embedding match run)",
                missing,
                source=source,
            )
        return IntentBundle.matched(
            transcript=transcript,
            intent=triggered.key,
            score=1.0,
            matched_example="(trigger phrase, no embedding match run)",
            handler=triggered.handler,
            slots=slots,
            source=source,
        )

    result = match(index, model, transcript)
    if result.score < threshold:
        # An explicit no-match, not the best of a bad set. Nothing
        # downstream ever sees a low-confidence guess.
        return IntentBundle.no_match(transcript, result.score, result.example, source=source)

    intent = config.get(result.intent)
    if intent is None:
        # Only reachable if the index and config went out of sync, which
        # the hash check exists to prevent. Fail closed rather than route
        # to an intent that no longer exists.
        raise RuntimeError(
            f"index names intent {result.intent!r}, which is not in {config.path}. "
            "Rebuild the index (--rebuild-index)."
        )

    slots, missing = extract(intent, transcript)
    if missing is not None:
        return IntentBundle.slot_missing(
            transcript, intent.key, result.score, result.example, missing, source=source
        )

    return IntentBundle.matched(
        transcript=transcript,
        intent=intent.key,
        score=result.score,
        matched_example=result.example,
        handler=intent.handler,
        slots=slots,
        source=source,
    )


def log_decision(bundle: IntentBundle, log_path: str | Path = DEFAULT_LOG_PATH) -> None:
    """Append one JSON object per routing decision. Same append-only JSONL
    shape as listener/captures/transcripts.jsonl, for the same two reasons
    given in intent_routing.md: it shows which real phrasings land near the
    threshold, and it is the labelled dataset a Tier 2 classifier would
    train on later, for free.
    """
    entry = {"timestamp": datetime.datetime.now().isoformat(), **bundle.as_log_entry()}
    log_path = Path(log_path)
    os.makedirs(log_path.parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


class Router:
    """Config, model and index loaded once, held for the process lifetime.

    Loading the embedding model is the expensive part, so this mirrors how
    the rest of the pipeline handles its models (speaker embedding,
    Whisper, Piper): load at startup, not per call. This is the object
    listener/vad_listener.py will hold.
    """

    def __init__(
        self,
        config: IntentConfig,
        model: SentenceTransformer,
        index: IntentIndex,
        threshold: float = DEFAULT_THRESHOLD,
        log_path: str | Path | None = DEFAULT_LOG_PATH,
    ) -> None:
        self.config = config
        self.model = model
        self.index = index
        self.threshold = threshold
        self.log_path = log_path

    @classmethod
    def load(
        cls,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        index_path: str | Path = DEFAULT_INDEX_PATH,
        threshold: float = DEFAULT_THRESHOLD,
        log_path: str | Path | None = DEFAULT_LOG_PATH,
        rebuild_index: bool = False,
    ) -> "Router":
        config = load_config(config_path)
        model = load_model()
        index = get_index(config, model, index_path, rebuild=rebuild_index)
        return cls(config, model, index, threshold, log_path)

    def route(self, transcript: str, source: str = "voice") -> IntentBundle:
        bundle = route(transcript, self.index, self.model, self.config, self.threshold, source=source)
        if self.log_path is not None:
            log_decision(bundle, self.log_path)
        return bundle


def _read_transcripts(path: str | Path) -> list[str]:
    """Reads either a JSONL log (uses each line's "transcript" field, so
    listener/captures/transcripts.jsonl works directly) or a plain text
    file of one phrase per line. Blank lines are skipped.
    """
    transcripts: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            transcripts.append(line)
            continue
        if isinstance(parsed, dict) and "transcript" in parsed:
            transcripts.append(parsed["transcript"])
        else:
            transcripts.append(line)
    return transcripts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", nargs="?", help="The transcribed command to route")
    parser.add_argument(
        "--batch",
        metavar="PATH",
        help="Route every transcript in a file instead of a single argument. "
        "Accepts a JSONL log (reads each line's 'transcript' field, so "
        "listener/captures/transcripts.jsonl works as-is) or one phrase per line.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Cosine similarity below which a transcript is a no-match "
        f"(default: {DEFAULT_THRESHOLD}). This default is an UNCALIBRATED "
        "placeholder, run routing/calibrate.py and replace it.",
    )
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
        "--rebuild-index",
        action="store_true",
        help="Re-embed the example set even if the cache is still valid.",
    )
    parser.add_argument(
        "--log",
        default=str(DEFAULT_LOG_PATH),
        help=f"JSONL file to append routing decisions to (default: {DEFAULT_LOG_PATH}).",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Don't record this decision. --batch implies this, replaying a "
        "file would otherwise fill the log with duplicates of phrases "
        "that were already logged when they were first said.",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Also print the top scoring example phrasings, for working out "
        "why something routed where it did.",
    )
    args = parser.parse_args()

    if not args.transcript and not args.batch:
        parser.error("give a transcript to route, or --batch a file of them")

    router = Router.load(
        config_path=args.config,
        index_path=args.index,
        threshold=args.threshold,
        log_path=None if (args.no_log or args.batch) else args.log,
        rebuild_index=args.rebuild_index,
    )

    if args.batch:
        transcripts = _read_transcripts(args.batch)
        print(f"Routing {len(transcripts)} transcript(s) from {args.batch}")
        print(f"Threshold: {args.threshold}  (decisions not logged in batch mode)\n")
        counts: dict[str, int] = {}
        for text in transcripts:
            result_bundle = router.route(text)
            counts[str(result_bundle.status)] = counts.get(str(result_bundle.status), 0) + 1
            print(f"  {text!r}")
            print(f"    {result_bundle.describe()}\n")
        print("--- summary ---")
        for status in RoutingStatus:
            print(f"  {str(status):<18} {counts.get(str(status), 0)}")
        raise SystemExit(0)

    bundle = router.route(args.transcript)
    print(f"Transcript: {args.transcript!r}")
    print(f"Threshold:  {args.threshold}")
    if bundle.matched_example:
        print(f"Closest:    {bundle.matched_example!r}")
    print(bundle.describe())
    if bundle.handler:
        print(f"Handler:    {bundle.handler}")

    if args.explain:
        print("\n--- top scoring examples ---")
        for intent_key, example, score in top_matches(router.index, router.model, args.transcript):
            print(f"  {score:.3f}  {intent_key:<20} {example!r}")
