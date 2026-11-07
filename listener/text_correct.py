"""Fixes the spelling of ekko's own vocabulary ("Ekko", "Chrome", product
names) in a transcript, after either speech engine. Parakeet has no prompt
to bias it the way Whisper's initial_prompt does, so this does the job for
both.

A port of apply_custom_words from Handy (github.com/cjpais/Handy, MIT,
src-tauri/src/audio_toolkit/text.rs, commit 73cc8579): edit distance plus a
phonetic (Soundex) boost, and up to three spoken words joined into one custom
word ("charge b" -> "ChargeBee"). ASCII words only.
"""

_CODES = {c: d for d, letters in {"1": "bfpv", "2": "cgjkqsxz", "3": "dt", "4": "l", "5": "mn", "6": "r"}.items() for c in letters}


def soundex(word: str) -> str:
    letters = [c for c in word.upper() if c.isalpha()]
    if not letters:
        return ""
    out, prev = letters[0], _CODES.get(letters[0].lower(), "")
    for c in letters[1:]:
        code = _CODES.get(c.lower(), "")
        if code and code != prev:
            out += code
        if c not in "HW":  # H and W don't separate two letters of the same code
            prev = code
    return (out + "000")[:4]


def _levenshtein(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _key(word: str) -> str:
    return "".join(c for c in word if c.isalnum()).lower()


def _fuzzy_ok(key: str) -> bool:
    return bool(key) and key.isascii() and key.isalnum()


def _keys(word: str, index: int) -> list[tuple[int, str]]:
    primary = _key(word)
    keys = [(index, primary)] if _fuzzy_ok(primary) else []
    if "&" in word:
        expanded = _key(word.replace("&", " and "))
        if _fuzzy_ok(expanded) and expanded != primary:
            keys.append((index, expanded))
    return keys


def _best_match(candidate: str, words: list[str], keys: list[tuple[int, str]], threshold: float):
    if not _fuzzy_ok(candidate) or len(candidate) > 50:
        return None
    best, best_score = None, float("inf")
    for index, key in keys:
        longest = max(len(candidate), len(key))
        if abs(len(candidate) - len(key)) > max(longest * 0.25, 2.0):
            continue  # too different in length to be the same word
        score = _levenshtein(candidate, key) / longest
        if candidate.isalpha() and key.isalpha() and soundex(candidate) == soundex(key):
            score *= 0.3  # sounds the same: trust it more
        if score < threshold and score < best_score:
            best, best_score = words[index], score
    return None if best is None else (best, best_score)


def _split_punctuation(word: str) -> tuple[str, str]:
    start = next((i for i, c in enumerate(word) if c.isalnum()), len(word))
    end = next((i + 1 for i in range(len(word) - 1, -1, -1) if word[i].isalnum()), 0)
    return word[:start], word[end:] if end > start else ""


def _match_case(original: str, replacement: str) -> str:
    if original and all(c.isupper() for c in original):
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_custom_words(text: str, custom_words: list[str], threshold: float) -> str:
    """`threshold` is the loosest match accepted: 0 changes nothing, 0.18 only
    near-misses, higher gets riskier."""
    if not custom_words or threshold <= 0:
        return text
    keys = [k for i, w in enumerate(custom_words) for k in _keys(w, i)]
    words = text.split()
    out, i = [], 0
    while i < len(words):
        best = None  # (n words used, replacement, score)
        for n in (3, 2, 1):
            if i + n > len(words):
                continue
            group = words[i : i + n]
            # A comma or period inside the group ends the candidate there.
            if any(_split_punctuation(w)[1] for w in group[:-1]):
                continue
            match = _best_match("".join(_key(w) for w in group), custom_words, keys, threshold)
            if match and (best is None or match[1] < best[2]):
                best = (n, match[0], match[1])
        if best:
            n, replacement, _ = best
            prefix, _ = _split_punctuation(words[i])
            _, suffix = _split_punctuation(words[i + n - 1])
            out.append(prefix + _match_case(words[i], replacement) + suffix)
            i += n
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)
