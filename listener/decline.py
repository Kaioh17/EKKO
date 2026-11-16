"""Closing lines: the replies that end a conversation ("no", "nothing else,
bro", "that's all good"), recognized without a model call.

A closed, hand-written set rather than a routing/intents.yaml intent -- that
file is the security boundary for things EKKO can *do*, and "no" doesn't do
anything. This is the first of two checks: a phrase it misses still reaches
the model, which can flag it `decline` (llm_fallback/BASE_CONTRACT.md case 3).
Every such miss is logged (`via: inference`); add its text to PHRASES.

PHRASES holds the contracted spelling only. is_decline() contracts what STT
writes, merges stutters, then accepts an utterance made up *entirely* of
phrases and filler words (at least one real phrase), so "That is all.",
"no, no, it's okay. Nothing else." and "Nothing else, bro. Jesus Christ." need
no entries of their own, while "no, open chrome" is not a decline.
tests/test_decline.py fails on an entry the other entries already cover.
"""

import re

PHRASES = (
    "no", "nope", "nah", "nothing", "nothing else", "nothing more", "nothing further",
    "nothing at all", "nothin' else", "nothing else needed", "nothing more needed",
    "that's all", "that'll be all", "that'd be all", "that's it", "that'll be it",
    "that'll do it", "that's all good", "that's everything", "that's all i need",
    "that's all i got", "that's enough", "enough", "that's okay", "that's fine",
    "i'm good", "i'm all good", "i'm fine", "i'm done", "i'm all set", "i'm set",
    "i'm okay", "i'm ok", "i think that's it", "i think that's all", "i think i'm good",
    "all good", "all set", "it's all good", "it's okay", "it's fine",
    "we're good", "we're done", "we're all set", "you're good",
    "no need", "no more", "not now", "not right now",
    "never mind", "nevermind", "forget it", "scrap that",
    "thanks", "thank you", "thanks a lot", "thank you so much",
    "bye", "goodbye", "see you", "see you later", "see you next time", "good night",
    "go to sleep", "go back to sleep", "you can go back to sleep",
)

# "that is" -> "thats" etc., so one contracted spelling covers what STT writes.
_SPOKEN_FORMS = tuple(
    (re.compile(rf"\b{a}\b"), b)
    for a, b in (("that is", "thats"), ("it is", "its"), ("i am", "im"), ("we are", "were"),
                 ("you are", "youre"), ("that will", "thatll"), ("that would", "thatd"))
)
_REPEATS = re.compile(r"\b(\w+(?: \w+)?)(?: \1\b)+")  # "nothing nothing", "no no no"
# Words around a closing line that never change its meaning. Never a decline
# on their own ("ok", "bro"); "no"/"thanks" are phrases, so they aren't here.
_FILLER = frozenset({
    "oh", "um", "uh", "well", "so", "now", "okay", "ok", "alright", "yeah", "hey", "cool",
    "bro", "man", "dude", "mate", "buddy", "bitch", "please", "jesus christ", "jesus", "christ",
    "oh my god", "my god", "omg", "damn it", "dammit", "damn", "ekko", "jarvis", "though",
    "really", "honestly", "for now", "right now",
})


def squash(text: str) -> str:
    """Lowercase, no punctuation or apostrophes (so "that's"/"thats"/"that’s"
    agree), one space between words, spoken forms contracted, stutters merged."""
    t = text.lower().replace("’", "'").replace("'", "")
    t = " ".join(re.sub(r"[^a-z0-9\s]", " ", t).split())
    for pattern, contracted in _SPOKEN_FORMS:
        t = pattern.sub(contracted, t)
    return _REPEATS.sub(r"\1", t)


def _covered(squashed: str, known: frozenset[str]) -> bool:
    """Greedy longest-first walk: every word belongs to a known phrase or a
    filler, and at least one to a phrase."""
    words, i, hit = squashed.split(), 0, False
    while i < len(words):
        for n in range(min(_LONGEST, len(words) - i), 0, -1):
            chunk = " ".join(words[i:i + n])
            if chunk in known or chunk in _FILLER:
                hit = hit or chunk in known
                i += n
                break
        else:
            return False
    return hit


_KNOWN = frozenset(squash(p) for p in PHRASES)
_LONGEST = max(len(p.split()) for p in _KNOWN | _FILLER)


def is_decline(text: str) -> bool:
    """True if a follow-up reply ("anything else?") was a plain negative:
    only closing phrases and filler, nothing else said."""
    return _covered(squash(text), _KNOWN)
