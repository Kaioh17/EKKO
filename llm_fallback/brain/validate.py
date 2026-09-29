"""validate_pick() -- the one safety re-check every provider shares.

An intent name proposed by any model is never
trusted on its own -- it's re-checked against a *fresh* load of
routing/intents.yaml, and every declared slot is independently re-filled
from the real transcript via routing/slots.py, never from the model's own
proposed value. See validate_pick()'s docstring below for the full
reasoning.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from routing.bundle import IntentBundle  # noqa: E402
from routing.config import IntentConfig  # noqa: E402
from routing.slots import find_free_text, find_transcript, find_value  # noqa: E402

# Sentinel score for an LLM-assisted match, distinct from any real cosine
# similarity the embedding matcher could produce (those are always in
# [-1, 1] but this project's real scores cluster in [0.3, 1.0], see
# routing/README.md). 0.0 reads as "no confidence signal was measured here,"
# which is honest -- there is no embedding score for this bundle.
LLM_MATCH_SCORE = 0.0
LLM_MATCH_EXAMPLE = "(llm_fallback)"


def validate_pick(
    transcript: str,
    intent_name: str | None,
    proposed_slots: dict[str, str],
    config: IntentConfig,
) -> IntentBundle | None:
    """The re-validation step. intent_name must be an exact key in a
    freshly loaded config (never trust the prompt echo), and every declared
    slot must independently pass the same exact matcher routing/slots.py
    uses for the deterministic path -- proposed_slots is only ever used to
    know *which* slot the model thought applied, the value that ends up in
    the bundle always comes back out of find_value()/find_free_text() run
    against the real transcript, never out of the model's own text.

    Returns None on any failure: unknown intent, or any declared slot that
    doesn't independently validate. A MATCHED bundle only comes back when
    every check passes, same "fail rather than guess" rule routing/slots.py
    documents for the deterministic path.

    find_free_text(..., trust_intent_match=False) here, not the default:
    that function's "no trigger phrase -> fall back to the whole transcript"
    behavior is only sound when the caller's `intent` came from a real
    embedding score, which is what the deterministic path (routing/route.py
    -> extract()) has and this path does not -- `intent_name` here is a
    model's own guess, exactly the thing being re-validated, not yet-earned
    confidence. Found in manual testing against a local fallback model (see
    llm_fallback/ollama/): a weak model asked to re-check "tell me a joke"
    guessed `web_search` with no real basis, and the trusting fallback
    filled `query` with the whole transcript anyway, producing a MATCHED
    bundle -- an executable search from a wrong guess, not a rejected one.
    `trust_intent_match=False` makes "no trigger phrase found" MISSING_SLOT
    here instead, closing that gap for every caller of this function,
    regardless of which provider proposed the pick.
    """
    if intent_name is None:
        return None
    intent = config.get(intent_name)
    if intent is None:
        return None

    filled: dict[str, str] = {}
    for slot in intent.slots:
        # transcript slots are the whole utterance: nothing for the model to
        # propose or get wrong, and the handler is useless without it.
        if slot.type != "transcript" and slot.name not in proposed_slots:
            continue
        if slot.type == "transcript":
            value = find_transcript(transcript)
        elif slot.type == "free_text":
            value = find_free_text(slot, transcript, trust_intent_match=False)
        else:
            value = find_value(slot, transcript)
        if value is None:
            # The model thought this slot applied but the deterministic
            # extractor, run independently against the real transcript,
            # disagrees. Fail closed rather than trust the model's value.
            return None
        filled[slot.name] = value

    return IntentBundle.matched(
        transcript=transcript,
        intent=intent.key,
        score=LLM_MATCH_SCORE,
        matched_example=LLM_MATCH_EXAMPLE,
        handler=intent.handler,
        slots=filled,
    )
