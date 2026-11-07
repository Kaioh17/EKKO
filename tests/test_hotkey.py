import pytest

from listener.hotkey import normalize_chord


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("ctrl+alt+space", "ctrl+alt+space"),
        ("Ctrl + Alt + Space", "ctrl+alt+space"),
        ("ALT+CTRL+p", "ctrl+alt+p"),  # modifiers come out in one fixed order
        ("Control+Option+Spacebar", "ctrl+alt+space"),
        ("win+ctrl+f9", "ctrl+windows+f9"),
        ("f9", "f9"),  # a function key is safe alone
        ("ctrl+/", "ctrl+/"),
        ("ctrl+alt+page up", "ctrl+alt+page up"),
        ("ctrl+alt+PageDown", "ctrl+alt+page down"),
    ],
)
def test_normalizes_what_a_person_might_type(typed, expected):
    assert normalize_chord(typed) == expected


@pytest.mark.parametrize(
    "typed, why",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("w", "Ctrl, Alt"),  # a bare letter would fire while typing
        ("shift+a", "Ctrl, Alt"),  # so would a capital
        ("ctrl+alt", "key"),  # modifiers only
        ("ctrl+a+b", "one"),  # two main keys
        ("ctrl+ctrl+a", "twice"),
        ("ctrl+alt+banana", "banana"),
        ("ctrl+alt+w", "Manual wake"),  # already in use
        ("ctrl+alt+q", "Hard stop"),
        ("ctrl+alt+", "key"),
    ],
)
def test_rejects_with_a_reason_a_person_can_act_on(typed, why):
    with pytest.raises(ValueError) as e:
        normalize_chord(typed)
    assert why.lower() in str(e.value).lower()


def test_collision_check_sees_through_spelling():
    with pytest.raises(ValueError):
        normalize_chord("Alt+Control+W")
