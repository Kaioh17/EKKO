from listener.segmenter import HoldToTalk


def test_press_and_release_are_edges_not_levels():
    h = HoldToTalk()
    assert h.update(False, 0.0) is None and not h.active
    assert h.update(True, 1.0) == "press" and h.active
    for t in (1.1, 1.2, 1.3):  # still held: nothing new
        assert h.update(True, t) is None and h.active
    assert h.update(False, 1.4) == "release" and not h.active
    assert h.update(False, 1.5) is None


def test_voiced_is_per_press():
    h = HoldToTalk()
    h.update(True, 0.0)
    assert not h.voiced
    h.heard_speech()
    assert h.voiced
    h.update(False, 1.0)
    h.update(True, 2.0)  # a fresh press starts with nothing heard
    assert not h.voiced


def test_a_stuck_key_is_released_at_the_cap_and_not_re_pressed():
    h = HoldToTalk(max_seconds=30)
    assert h.update(True, 0.0) == "press"
    assert h.update(True, 29.9) is None
    assert h.update(True, 30.1) == "release" and not h.active
    # the key is still down: that's not a new press
    assert h.update(True, 31.0) is None and not h.active
    assert h.update(False, 32.0) is None
    assert h.update(True, 33.0) == "press"


def test_release_without_a_press_is_ignored():
    h = HoldToTalk()
    assert h.update(False, 0.0) is None
    assert h.update(False, 0.1) is None


def test_cancel_ends_the_hold_and_a_still_held_key_is_not_a_new_press():
    h = HoldToTalk()
    h.update(True, 0.0)
    h.cancel()
    assert not h.active
    assert h.update(True, 1.0) is None  # hard stop pressed while still holding
    assert h.update(False, 2.0) is None
    assert h.update(True, 3.0) == "press"


def test_cancel_when_idle_changes_nothing():
    h = HoldToTalk()
    h.cancel()
    assert h.update(True, 0.0) == "press"
