import datetime

import pytest

from memory import day_log, today_summary

T0 = datetime.datetime(2026, 10, 4, 15, 0)


def _setup(tmp_path, n=8, size=3000, start=T0):
    days = tmp_path / "days"
    for i in range(n):
        day_log.record_turn(f"question {i}", f"answer {i} " + "x" * size, now=start + datetime.timedelta(minutes=i), directory=days)
    return dict(path=tmp_path / "today.json", directory=days)


def _now(n=8):
    return T0 + datetime.timedelta(minutes=n)


def test_folds_only_turns_that_left_the_window(tmp_path):
    kw = _setup(tmp_path)
    seen = []

    def fake(previous, block):
        seen.append((previous, block))
        return {"summary": "talked about questions", "topics": ["q"]}, {}

    entry = today_summary.refresh(now=_now(), summarize=fake, **kw)
    window, _, _ = day_log.window(day_log.recent_turns(_now(), directory=kw["directory"]))
    evicted = 8 - len(window)
    assert evicted > 0
    assert f"question {evicted - 1}" in seen[0][1] and f"question {evicted}" not in seen[0][1]
    assert entry["covered_until"].startswith("2026-10-04T15:0") and entry["date"] == "2026-10-04"
    assert today_summary.read_today(_now(), kw["path"])["summary"] == "talked about questions"

    # Nothing new evicted -> no call; a new turn evicts one more -> folded on top.
    assert today_summary.refresh(now=_now(), force=True, summarize=fake, **kw) is None
    day_log.record_turn("question 8", "answer 8 " + "x" * 3000, now=_now(), directory=kw["directory"])
    today_summary.refresh(now=_now(9), force=True, summarize=fake, **kw)
    assert seen[1][0] == "talked about questions" and f"question {evicted}" in seen[1][1]


def test_small_backlog_waits_unless_forced(tmp_path):
    kw = _setup(tmp_path, n=3, size=8000)  # each turn alone fills most of the window
    calls = []
    fake = lambda prev, block: (calls.append(1) or {"summary": "s"}, {})  # noqa: E731
    monkey_min = today_summary.MIN_FOLD_TOKENS
    try:
        today_summary.MIN_FOLD_TOKENS = 10**9
        assert today_summary.refresh(now=_now(3), summarize=fake, **kw) is None and calls == []
        assert today_summary.refresh(now=_now(3), force=True, summarize=fake, **kw) is not None
    finally:
        today_summary.MIN_FOLD_TOKENS = monkey_min


def test_failure_writes_nothing(tmp_path):
    kw = _setup(tmp_path)

    def boom(previous, block):
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        today_summary.refresh(now=_now(), force=True, summarize=boom, **kw)
    assert not kw["path"].exists()


def test_yesterdays_summary_is_not_today_context(tmp_path):
    kw = _setup(tmp_path)
    today_summary.refresh(now=_now(), force=True, summarize=lambda p, b: ({"summary": "s"}, {}), **kw)
    tomorrow = _now() + datetime.timedelta(days=1)
    assert today_summary.read_today(tomorrow, kw["path"]) is None
    # Yesterday's turns are never folded into today's summary.
    assert today_summary.pending_turns(tomorrow, kw["path"], kw["directory"]) == []
