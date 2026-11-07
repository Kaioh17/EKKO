"""Cases ported from Handy's src-tauri/src/audio_toolkit/text.rs tests, plus
ekko's own vocabulary."""

from listener.defaults import DEFAULT_CUSTOM_WORDS, DEFAULT_WORD_CORRECTION_THRESHOLD
from listener.text_correct import apply_custom_words, soundex


def test_soundex_codes():
    assert soundex("Robert") == soundex("Rupert") == "R163"
    assert soundex("echo") == soundex("ekko") == "E200"
    assert soundex("moot") == soundex("mute") == "M300"
    assert soundex("grave") != soundex("brave")


def test_exact_match_takes_the_custom_spelling():
    assert apply_custom_words("hello world", ["Hello", "World"], 0.5) == "Hello World"


def test_fuzzy_match():
    assert apply_custom_words("helo wrold", ["hello", "world"], 0.5) == "hello world"


def test_empty_custom_words_changes_nothing():
    assert apply_custom_words("hello world", [], 0.5) == "hello world"


def test_threshold_decides_how_loose_a_match_may_be():
    # "moot" is a Whisper mishearing of "mute": similar, but not close enough
    # for Handy's default 0.18.
    assert apply_custom_words("moot", ["mute"], 0.5) == "mute"
    assert apply_custom_words("moot", ["mute"], DEFAULT_WORD_CORRECTION_THRESHOLD) == "moot"
    assert apply_custom_words("moot", ["mute"], 0) == "moot"


def test_split_words_join_into_one_custom_word():
    # Handy's own example: speech artifacts that split a name in two.
    assert apply_custom_words("use charge b today", ["ChargeBee"], DEFAULT_WORD_CORRECTION_THRESHOLD) == "use ChargeBee today"


def test_punctuation_and_case_are_kept():
    assert apply_custom_words("Open chrom, please", ["Chrome"], DEFAULT_WORD_CORRECTION_THRESHOLD) == "Open Chrome, please"
    assert apply_custom_words("OPEN CHROM", ["Chrome"], DEFAULT_WORD_CORRECTION_THRESHOLD) == "OPEN CHROME"
    assert apply_custom_words("!chrom?", ["Chrome"], DEFAULT_WORD_CORRECTION_THRESHOLD) == "!Chrome?"


def test_a_match_ends_at_a_comma_and_does_not_swallow_the_next_word():
    assert apply_custom_words("charge b, che", ["ChargeBee"], DEFAULT_WORD_CORRECTION_THRESHOLD) == "ChargeBee, che"


def test_default_words_fix_ekko_and_leave_ordinary_speech_alone():
    t = DEFAULT_WORD_CORRECTION_THRESHOLD
    assert apply_custom_words("hey echo open the music", DEFAULT_CUSTOM_WORDS, t) == "hey Ekko open the music"
    for plain in ("what is the weather today", "play the next track", "lock the screen", "be brave"):
        assert apply_custom_words(plain, DEFAULT_CUSTOM_WORDS, t) == plain.replace("brave", "Brave")
    # a different word that merely looks similar must not become Brave
    assert apply_custom_words("grave", DEFAULT_CUSTOM_WORDS, t) == "grave"


def test_words_with_no_ascii_key_are_skipped_without_error():
    assert apply_custom_words("hello", ["", "你好", "Hello"], 0.5) == "Hello"
