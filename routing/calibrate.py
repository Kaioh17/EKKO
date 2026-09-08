"""Measures where the routing threshold should actually sit.

Mirrors voice_auth/diagnose.py, which does the same job for speaker
verification, and exists for the same reason: this project already shipped
a borrowed generic threshold once (0.75, for the speaker check) and it was
badly miscalibrated for the real setup. The right value only appeared
after measuring genuine-vs-impostor scores directly. matcher.py's
DEFAULT_THRESHOLD is currently that same kind of guess. This script is how
it stops being one.

Scores every phrase in calibration_phrases.yaml and reports:

  1. Each phrase's best score, and for should_match phrases, whether it
     landed on the intent it was supposed to. A wrong intent is NOT a
     threshold problem, no cutoff fixes it, it means the example set needs
     a phrasing it's missing. Those are called out separately.
  2. The two score distributions, should-match versus should-not-match,
     and the gap between them. The threshold belongs in the middle of that
     gap, which is exactly how 0.6 was arrived at for the speaker check.
  3. The top-2 margin: how far the winning intent beat the best example
     from any *other* intent. Reported but not acted on, it's the data an
     ambiguity check would need if one is ever added, and there's no point
     inventing a second uncalibrated number now.

Usage:
    python routing/calibrate.py
    python routing/calibrate.py --phrases other_phrases.yaml
"""

import argparse
import statistics
from dataclasses import dataclass
from pathlib import Path

import yaml

try:
    from .config import DEFAULT_CONFIG_PATH, load_config
    from .matcher import DEFAULT_INDEX_PATH, DEFAULT_THRESHOLD, get_index, load_model, match
    from .slots import extract
except ImportError:
    from config import DEFAULT_CONFIG_PATH, load_config
    from matcher import DEFAULT_INDEX_PATH, DEFAULT_THRESHOLD, get_index, load_model, match
    from slots import extract

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PHRASES_PATH = _PACKAGE_DIR / "calibration_phrases.yaml"


@dataclass
class Scored:
    phrase: str
    expected_intent: str | None  # None for should_not_match phrases
    predicted_intent: str
    score: float
    margin: float  # score minus the best score from any other intent
    slots: dict[str, str]
    missing_slot: str | None

    @property
    def routed_correctly(self) -> bool:
        return self.predicted_intent == self.expected_intent


def score_phrases(phrases_path: str | Path, config_path: str, index_path: str) -> list[Scored]:
    raw = yaml.safe_load(Path(phrases_path).read_text(encoding="utf-8")) or {}
    should_match: dict = raw.get("should_match") or {}
    should_not_match: list = raw.get("should_not_match") or []

    config = load_config(config_path)
    model = load_model()
    index = get_index(config, model, index_path)

    unknown = set(should_match) - {i.key for i in config.intents}
    if unknown:
        raise SystemExit(
            f"[calibrate] {phrases_path} names intent(s) not in the config: {sorted(unknown)}"
        )

    labelled: list[tuple[str, str | None]] = [
        (phrase, intent_key) for intent_key, phrases in should_match.items() for phrase in phrases
    ]
    labelled += [(phrase, None) for phrase in should_not_match]

    results: list[Scored] = []
    for phrase, expected in labelled:
        result = match(index, model, phrase)
        intent = config.get(result.intent)
        slots, missing = extract(intent, phrase) if intent else ({}, None)
        results.append(
            Scored(
                phrase=phrase,
                expected_intent=expected,
                predicted_intent=result.intent,
                score=result.score,
                margin=result.score - result.runner_up_score,
                slots=slots,
                missing_slot=missing,
            )
        )
    return results


def report(results: list[Scored], current_threshold: float) -> None:
    genuine = [r for r in results if r.expected_intent is not None]
    impostor = [r for r in results if r.expected_intent is None]
    misrouted = [r for r in genuine if not r.routed_correctly]
    correct = [r for r in genuine if r.routed_correctly]

    print("\n--- should match ---")
    for r in sorted(genuine, key=lambda r: r.score):
        flag = "" if r.routed_correctly else f"  <-- routed to {r.predicted_intent} instead"
        slot_note = ""
        if r.routed_correctly and r.missing_slot:
            slot_note = f"  <-- slot {r.missing_slot!r} not filled"
        elif r.slots:
            slot_note = f"  slots={r.slots}"
        print(f"  {r.score:.3f}  {r.expected_intent:<20} {r.phrase!r}{flag}{slot_note}")

    print("\n--- should NOT match ---")
    for r in sorted(impostor, key=lambda r: r.score, reverse=True):
        print(f"  {r.score:.3f}  (closest: {r.predicted_intent:<18}) {r.phrase!r}")

    print("\n--- distributions ---")
    _print_distribution("should match (routed correctly)", [r.score for r in correct])
    _print_distribution("should NOT match", [r.score for r in impostor])
    _print_distribution("top-2 margin, correct routes", [r.margin for r in correct])

    if not correct or not impostor:
        print("\nNot enough labelled phrases to recommend a threshold.")
        return

    floor = min(r.score for r in correct)  # lowest a real command scored
    ceiling = max(r.score for r in impostor)  # highest anything else scored
    print("\n--- recommendation ---")
    print(f"  lowest genuine score:   {floor:.3f}  ({_lowest(correct).phrase!r})")
    print(f"  highest impostor score: {ceiling:.3f}  ({_highest(impostor).phrase!r})")
    print(f"  currently in matcher.py: {current_threshold}")

    if floor > ceiling:
        recommended = (floor + ceiling) / 2
        print(f"  clean gap:              {floor - ceiling:.3f}")
        print(f"\n  recommended threshold:  {recommended:.2f}")
        if not (ceiling < current_threshold < floor):
            print(
                "\n  The current threshold is OUTSIDE the measured gap. Update "
                "matcher.DEFAULT_THRESHOLD to the recommended value."
            )
        return

    # The two sets overlap, so no threshold is error-free. Report the two
    # thresholds worth considering rather than refusing to pick: the one
    # that minimises total errors, and the one that eliminates false
    # accepts entirely. For a system that runs scripts, those are not
    # equally bad errors, a false accept executes something unasked for,
    # a false reject just means saying it again, so the second is the one
    # that matters and is printed last.
    print(f"  overlap:                {ceiling - floor:.3f}, no error-free threshold exists")

    balanced, balanced_fa, balanced_fr = _best_threshold(correct, impostor, weight_false_accept=1)
    strict, strict_fa, strict_fr = _best_threshold(correct, impostor, weight_false_accept=1000)
    print(
        f"\n  fewest total errors:    {balanced:.2f}  "
        f"({balanced_fa} false accept(s), {balanced_fr} false reject(s))"
    )
    print(
        f"  no false accepts:       {strict:.2f}  "
        f"({strict_fa} false accept(s), {strict_fr} false reject(s))"
    )

    print("\n  Scoring at or above a real command:")
    for r in sorted([r for r in impostor if r.score >= floor], key=lambda r: r.score, reverse=True):
        print(f"    {r.score:.3f}  {r.phrase!r} (closest to {r.predicted_intent})")
    print(
        "  Check each of these before trusting a threshold picked around "
        "them. An impostor that scores high because it's the OPPOSITE of a "
        "real command ('close X' against 'open X') will not go away by "
        "moving the threshold, sentence embeddings barely encode negation. "
        "One that scores high because it's genuinely similar means an "
        "example phrasing is too loose and should be tightened."
    )

    if misrouted:
        print(f"\n--- {len(misrouted)} phrase(s) routed to the WRONG intent ---")
        for r in misrouted:
            print(f"  {r.phrase!r}: expected {r.expected_intent}, got {r.predicted_intent}")
        print(
            "  No threshold fixes these, they cleared the bar and picked the "
            "wrong intent. Add a phrasing closer to these to the expected "
            "intent's examples in intents.yaml."
        )

    unfilled = [r for r in correct if r.missing_slot]
    if unfilled:
        print(f"\n--- {len(unfilled)} phrase(s) matched but couldn't fill a slot ---")
        for r in unfilled:
            print(f"  {r.phrase!r}: slot {r.missing_slot!r} unfilled")
        print(
            "  Not a threshold problem either. Add the wording these use to "
            "that value's aliases in intents.yaml."
        )

    print(
        "\nA gap of 0.15 or more means the threshold has room to sit "
        "comfortably in the middle and small phrasing changes won't flip a "
        "decision. A narrow gap means the two sets are nearly touching, and "
        "the honest fix is more distinguishing examples, not a threshold "
        "tuned to three decimal places. Re-run this after every intents.yaml "
        "edit, and grow calibration_phrases.yaml from whatever shows up near "
        "the boundary in routing/logs/routing.jsonl."
    )


def _best_threshold(
    correct: list[Scored], impostor: list[Scored], weight_false_accept: int
) -> tuple[float, int, int]:
    """Sweep every threshold the data can distinguish and return the one
    with the lowest weighted error count, plus the errors it makes.

    weight_false_accept scales how much worse an executed-but-unasked-for
    command is than a rejected real one. At 1 the two are equal; at a
    large value the sweep will accept any number of false rejects to get
    false accepts to zero.

    Candidates are the observed scores nudged up by a hair, so a threshold
    always sits just above the impostor it's meant to exclude rather than
    exactly on it (the comparison in route() is `score < threshold`).
    """
    candidates = sorted({round(r.score + 1e-4, 4) for r in correct + impostor})
    best = (candidates[0], len(impostor), 0)
    best_cost = None
    for threshold in candidates:
        false_accepts = sum(1 for r in impostor if r.score >= threshold)
        false_rejects = sum(1 for r in correct if r.score < threshold)
        cost = false_accepts * weight_false_accept + false_rejects
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best = (threshold, false_accepts, false_rejects)
    return best


def _print_distribution(label: str, values: list[float]) -> None:
    if not values:
        print(f"  {label:<32} (none)")
        return
    print(
        f"  {label:<32} n={len(values):<3} "
        f"min={min(values):.3f}  mean={statistics.fmean(values):.3f}  max={max(values):.3f}"
    )


def _lowest(results: list[Scored]) -> Scored:
    return min(results, key=lambda r: r.score)


def _highest(results: list[Scored]) -> Scored:
    return max(results, key=lambda r: r.score)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phrases",
        default=str(DEFAULT_PHRASES_PATH),
        help=f"Labelled test phrases (default: {DEFAULT_PHRASES_PATH}).",
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
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="The threshold to compare the recommendation against "
        f"(default: matcher.DEFAULT_THRESHOLD, {DEFAULT_THRESHOLD}).",
    )
    args = parser.parse_args()

    report(score_phrases(args.phrases, args.config, args.index), args.threshold)
