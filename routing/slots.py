"""Closed-vocabulary slot extraction.

Intent matching answers *which command*; this answers *with what*. It runs
only after an intent has already matched, and only for intents that
declare slots, which is what makes it a small closed problem rather than
free-form parsing: by this point the set of legal values is known, so the
job is just finding which one of them the transcript names.

Deliberately not embedding-based. The intent decision tolerates paraphrase
because phrasings vary; slot values don't have that problem, they're a
short fixed list, and a nearest-neighbour match over five app names would
happily return "steam" for "stream". Exact matching over an explicit alias
list is both correct more often and auditable, which matters more here,
this value ends up on a command line.

Three rules, all of them "fail rather than guess":
  - the value returned is always the canonical config value, never a span
    of the transcript, so nothing user-derived reaches the handler
  - nothing found is MISSING_SLOT, never a default
  - two different values found is also MISSING_SLOT, since "open chrome
    and discord" is genuinely ambiguous and picking one would be a guess

Two slot types opt out of the first rule, and it's worth being explicit
about what that costs rather than letting the module docstring keep
claiming something that stopped being true. free_text passes a slice of
the transcript through (see find_free_text), and transcript passes the
whole utterance through verbatim (see find_transcript). Both are safe for
the same narrow reason and no broader one: the handler that receives them
never puts them on a shell line. web_search.ps1 puts its query in a URL
parameter; hands_on.ps1 hands its instruction to a parser whose output is
re-checked by control_center/hands_on/file_gateway/policy.py before anything touches
disk. A new handler taking one of these slot types has to earn that
separately.

Usage:
    python routing/slots.py                            # self-check
    python routing/slots.py open_app "launch vs code"
"""

import argparse
import re
import sys

try:
    from .config import DEFAULT_CONFIG_PATH, IntentSpec, SlotSpec, load_config, normalise
except ImportError:
    from config import DEFAULT_CONFIG_PATH, IntentSpec, SlotSpec, load_config, normalise


def find_value(slot: SlotSpec, transcript: str) -> str | None:
    """closed_vocabulary only. The canonical value this transcript names,
    or None if it names none of them or more than one.

    Phrasings are pre-sorted longest-first by config.py, so "visual studio
    code" is tested before any shorter phrasing contained in it and the
    longer, more specific reading wins.
    """
    normalised = normalise(transcript)
    found: list[str] = []
    for phrasing, canonical in slot.phrasings:
        if canonical in found:
            continue  # already matched by a longer phrasing for the same value
        # Word boundaries, not bare substring: "code" must not match
        # inside "codex", and "steam" must not match inside "steaming".
        if re.search(rf"\b{re.escape(phrasing)}\b", normalised):
            found.append(canonical)
    if len(found) == 1:
        return found[0]
    return None


def find_free_text(slot: SlotSpec, transcript: str, *, trust_intent_match: bool = True) -> str | None:
    """free_text only. Whatever follows the first trigger phrase found in
    the transcript, MISSING_SLOT (None) if a trigger was said but nothing
    followed it, or -- only when `trust_intent_match` is True -- the whole
    transcript if no trigger is present at all.

    This is a deliberate, narrow exception to the rest of this module: the
    return value is a slice of the transcript, not a canonical config
    value, because a search query genuinely can't be a closed vocabulary
    (see the comment on web_search in routing/intents.yaml). It stays safe
    despite that because it only ever reaches a URL query parameter in the
    handler, never a shell string, see scripts/web_search.ps1.

    Triggers are pre-sorted longest-first by config.py, same reasoning as
    phrasings above: "search up for" is tried before "search up" so the
    shorter trigger doesn't leave a stray "for" glued to the query.

    Falling back to the whole transcript when NO trigger is present, rather
    than MISSING_SLOT, is specific to free_text: by the time this runs on
    the deterministic path (routing/route.py -> extract(), the only caller
    that leaves `trust_intent_match` at its default), the intent classifier
    already decided the utterance IS a search with a real embedding score
    behind it, so refusing to fill the slot here would silently overrule a
    decision already earned upstream.

    `trust_intent_match=False` is for a caller whose `intent` came from an
    LLM's own guess rather than a scored match -- llm_fallback/*/fallback.py's
    validate_pick() is the only other caller, and it passes False
    deliberately. Found in manual testing: a weak local model asked to
    re-check "tell me a joke" against the intent list guessed `web_search`
    with a `query` slot that didn't correspond to anything it actually
    read off the transcript. With the trust-worthy-classifier fallback
    active, that slot filled with the whole transcript anyway ("tell me a
    joke") and validate_pick() let a MATCHED web_search bundle through --
    an executable action from a wrong guess, not a rejected one. An LLM's
    intent pick hasn't earned the same trust an embedding score has, so for
    that caller "no trigger phrase found" must mean MISSING_SLOT (fail
    closed), not "assume the whole transcript is the query."

    That fallback must not fire when a trigger WAS said but with nothing
    after it ("search for" and then silence) -- that case is genuinely
    missing, not "search for the words search for", which is why trigger
    presence is tracked separately from a filled remainder below rather
    than falling through the same loop. This holds regardless of
    `trust_intent_match`.
    """
    normalised = normalise(transcript)
    trigger_said = False
    for trigger in slot.triggers:
        match = re.search(rf"\b{re.escape(trigger)}\b", normalised)
        if match is None:
            continue
        trigger_said = True
        remainder = normalised[match.end():].strip()
        if remainder:
            return remainder
    if trigger_said:
        return None
    if trust_intent_match:
        return normalised or None
    return None


def find_transcript(transcript: str) -> str | None:
    """transcript slots only. The whole utterance, raw.

    Raw, not normalise()d, and that is the entire reason this slot type
    exists rather than a free_text slot with no triggers. normalise()
    strips punctuation, which is right for matching phrasings and wrong
    for anything carrying filenames -- it turns "notes.txt" into "notes
    txt". control_center/hands_on/file_gateway/nl.py's clean() documents the same
    tradeoff from the other side and makes the same choice.

    Unlike find_value and find_free_text there is nothing here that can
    fail to match, so the only MISSING_SLOT case is an utterance that was
    blank once stripped -- which route() has already returned an
    EMPTY_TRANSCRIPT bundle for before extract() runs.
    """
    return transcript.strip() or None


def extract(intent: IntentSpec, transcript: str) -> tuple[dict[str, str], str | None]:
    """Returns (filled slots, name of the first slot that couldn't be
    filled). A non-None second element means the caller must produce a
    MISSING_SLOT bundle, not a MATCHED one, and the partial dict in the
    first element is discarded, an incompletely parameterised command is
    not a command.
    """
    filled: dict[str, str] = {}
    for slot in intent.slots:
        if slot.type == "transcript":
            value = find_transcript(transcript)
        elif slot.type == "free_text":
            value = find_free_text(slot, transcript)
        else:
            value = find_value(slot, transcript)
        if value is None:
            return filled, slot.name
        filled[slot.name] = value
    return filled, None


def _self_check() -> None:
    """Run with no arguments. Covers the transcript slot type, whose whole
    reason for existing is that it does NOT normalise -- a regression
    there would silently rewrite filenames on their way to a handler and
    still look like a working extraction.
    """
    ts = IntentSpec(key="t", examples=(), handler="h", slots=(SlotSpec(name="instruction", type="transcript"),))

    filled, missing = extract(ts, "save notes.txt to the Archive folder")
    assert missing is None, missing
    # Verbatim: punctuation and casing both survive, unlike normalise().
    assert filled["instruction"] == "save notes.txt to the Archive folder", filled
    assert normalise("save notes.txt") == "save notes txt", "normalise() changed; the comment above is stale"

    assert extract(ts, "   padded   ")[0]["instruction"] == "padded"
    assert extract(ts, "   ")[1] == "instruction"

    # free_text is untouched by that branch: trigger still wins, and the
    # no-trigger whole-transcript fallback still normalises.
    ft = SlotSpec(name="query", type="free_text", triggers=("search for",))
    assert find_free_text(ft, "search for pasta recipes") == "pasta recipes"
    assert find_free_text(ft, "search for") is None
    assert find_free_text(ft, "pasta recipes!") == "pasta recipes"
    assert find_free_text(ft, "pasta recipes!", trust_intent_match=False) is None

    print("slots: self-check passed.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        _self_check()
        raise SystemExit(0)

    parser = argparse.ArgumentParser()
    parser.add_argument("intent", help="Intent key from the config, e.g. open_app")
    parser.add_argument("transcript", help="Text to extract slot values from")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to the intent config (default: {DEFAULT_CONFIG_PATH}).",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    intent_spec = config.get(args.intent)
    if intent_spec is None:
        raise SystemExit(
            f"[slots] no such intent {args.intent!r}. "
            f"Known: {[i.key for i in config.intents]}"
        )
    if not intent_spec.slots:
        raise SystemExit(f"[slots] {args.intent} declares no slots")

    slots, missing = extract(intent_spec, args.transcript)
    print(f"Transcript: {args.transcript!r}")
    print(f"Normalised: {normalise(args.transcript)!r}")
    if missing:
        print(f"MISSING SLOT: {missing!r}")
        spec = next(s for s in intent_spec.slots if s.name == missing)
        if spec.type == "closed_vocabulary":
            print(f"  expected one of: {list(spec.values)}")
        else:
            print(f"  expected text after one of: {list(spec.triggers)}")
    else:
        print(f"Slots: {slots}")
