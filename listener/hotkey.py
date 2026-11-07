"""Hotkey chords as the `keyboard` package spells them ("ctrl+alt+space").

One place decides what a valid, safe chord is, so the settings API rejects a
bad one with a message the Audio page can show, instead of the listener
discovering it at startup. Light on purpose: the backend imports it."""

from listener.defaults import DEFAULT_HARD_STOP_HOTKEY, DEFAULT_MANUAL_WAKE_HOTKEY

MODIFIERS = ("ctrl", "alt", "shift", "windows")  # the order a chord is written in
_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "option": "alt", "opt": "alt",
    "win": "windows", "cmd": "windows", "command": "windows", "super": "windows", "meta": "windows",
    "return": "enter", "escape": "esc", "spacebar": "space", "del": "delete", "ins": "insert",
    "pageup": "page up", "pgup": "page up", "pagedown": "page down", "pgdn": "page down",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
}
_NAMED = {"space", "enter", "tab", "esc", "backspace", "delete", "insert", "home", "end",
          "page up", "page down", "up", "down", "left", "right"} | {f"f{i}" for i in range(1, 25)}
_PUNCTUATION = set(",./;'[]\\-=`")


def _is_key(token: str) -> bool:
    if token in _NAMED:
        return True
    return len(token) == 1 and token.isascii() and (token.isalnum() or token in _PUNCTUATION)


def _parse(text: str) -> tuple[list[str], str]:
    """(modifiers in writing order, main key). ValueError says what to change."""
    if not text or not text.strip():
        raise ValueError("Pick a key combination: it's empty.")
    tokens = [_ALIASES.get(t.strip().lower(), t.strip().lower()) for t in text.split("+")]
    if any(not t for t in tokens):
        raise ValueError("Missing the main key (a letter, a number or Space) after the last +.")
    mods = [t for t in tokens if t in MODIFIERS]
    for m in set(mods):
        if mods.count(m) > 1:
            raise ValueError(f"{m} is used twice.")
    keys = [t for t in tokens if t not in MODIFIERS]
    if not keys:
        raise ValueError("Add a main key (a letter, a number or Space) as well as the modifiers.")
    if len(keys) > 1:
        raise ValueError(f"Use only one main key besides Ctrl, Alt, Shift and Windows (you have {' and '.join(keys)}).")
    if not _is_key(keys[0]):
        raise ValueError(f"Unknown key {keys[0]!r}. Use a letter, a number, Space, an arrow or an F-key.")
    return [m for m in MODIFIERS if m in mods], keys[0]


def _spell(mods: list[str], key: str) -> str:
    return "+".join([*mods, key])


def normalize_chord(text: str) -> str:
    """The chord in `keyboard`'s spelling, or ValueError. A held key that is
    also typed text (a bare letter, Shift+letter) would fire while you type,
    so it needs Ctrl, Alt or Windows; a function key is fine on its own."""
    mods, key = _parse(text)
    is_function_key = key.startswith("f") and key[1:].isdigit()
    if not is_function_key and not {"ctrl", "alt", "windows"} & set(mods):
        raise ValueError("Add Ctrl, Alt or Windows, otherwise it would trigger while you type.")
    chord = _spell(mods, key)
    for name, taken in (("Manual wake", DEFAULT_MANUAL_WAKE_HOTKEY), ("Hard stop", DEFAULT_HARD_STOP_HOTKEY)):
        if chord == _spell(*_parse(taken)):
            raise ValueError(f"{chord} is already used for {name}. Pick another combination.")
    return chord
