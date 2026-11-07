from listener.segmenter import PreRoll, segment_seconds

BLOCK_MS = 32  # 512 samples at 16 kHz


def blocks(n):
    return [f"b{i}" for i in range(n)]


def test_450ms_is_fourteen_lead_blocks():
    p = PreRoll(450)
    assert p.lead_blocks == 14


def test_take_returns_the_lead_plus_the_start_block():
    p = PreRoll(450)
    for b in blocks(20):  # b19 is the block VAD fires on
        p.add(b)
    taken, lead = p.take()
    assert lead == 14
    assert taken[0] == "b5" and taken[-1] == "b19" and len(taken) == 15


def test_short_history_uses_what_exists():
    p = PreRoll(450)
    for b in blocks(3):
        p.add(b)
    taken, lead = p.take()
    assert taken == ["b0", "b1", "b2"] and lead == 2


def test_take_clears_so_segments_never_share_lead():
    p = PreRoll(450)
    for b in blocks(5):
        p.add(b)
    p.take()
    p.add("next")
    assert p.take() == (["next"], 0)


def test_zero_preroll_is_just_the_start_block():
    p = PreRoll(0)
    for b in blocks(5):
        p.add(b)
    assert p.take() == (["b4"], 0)


def test_gate_closing_clears_the_lead():
    # EKKO spoke between these blocks (the gate closed): audio from before
    # that is not continuous with what follows, and may be the wake phrase.
    p = PreRoll(450)
    for b in blocks(10):
        p.add(b, epoch=1)
    p.add("after_ack", epoch=2)
    assert p.take() == (["after_ack"], 0)


def test_same_epoch_keeps_the_lead():
    p = PreRoll(450)
    p.add("a", epoch=7)
    p.add("b", epoch=7)
    assert p.take() == (["a", "b"], 1)


def test_clear_empties():
    p = PreRoll(450)
    p.add("a")
    p.clear()
    assert p.take() == ([], 0)


def test_segment_seconds_excludes_the_lead():
    # 60 blocks of which 14 are lead: 46 blocks of actual speech and tail.
    assert segment_seconds(60, 14) == 46 * BLOCK_MS / 1000
    assert segment_seconds(1, 0) == BLOCK_MS / 1000


def test_command_silence_default_is_800ms():
    from listener.defaults import DEFAULT_COMMAND_MIN_SILENCE_MS

    assert DEFAULT_COMMAND_MIN_SILENCE_MS == 800
