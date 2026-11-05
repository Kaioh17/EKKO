import datetime

from memory import archive, day_log

T0 = datetime.datetime(2026, 10, 4, 12, 0)


def _rec(d, user, assistant, when, **kw):
    day_log.record_turn(user, assistant, now=when, directory=d, **kw)


def test_recent_turns_window_and_order(tmp_path):
    _rec(tmp_path, "old", "x", T0 - datetime.timedelta(hours=30))
    _rec(tmp_path, "yesterday", "y", T0 - datetime.timedelta(hours=20))
    _rec(tmp_path, "now", "z", T0)
    turns = day_log.recent_turns(T0, directory=tmp_path)
    assert [t["user"] for t in turns] == ["yesterday", "now"]


def test_empty_user_is_not_recorded(tmp_path):
    _rec(tmp_path, "  ", "x", T0)
    assert day_log.recent_turns(T0, directory=tmp_path) == []


def test_budget_drops_oldest_turns_only_from_the_block(tmp_path):
    for i in range(10):
        _rec(tmp_path, f"question {i}", "a" * 400, T0 - datetime.timedelta(minutes=10 - i))
    turns = day_log.recent_turns(T0, directory=tmp_path)
    block, stats = day_log.render_block(turns, token_budget=300)
    assert stats["truncated_turns"] > 0 and stats["day_tokens_est"] <= 300
    assert "question 9" in block and "question 0" not in block
    assert len(turns) == 10  # disk untouched


def test_long_answer_is_capped(tmp_path):
    _rec(tmp_path, "q", "a" * 5000, T0)
    block, _ = day_log.render_block(day_log.recent_turns(T0, directory=tmp_path))
    assert len(block) < day_log.ANSWER_CHAR_CAP + 200


def test_past_days_are_ready_at_once_but_expire_after_24h(tmp_path):
    _rec(tmp_path, "a", "x", T0 - datetime.timedelta(hours=48))
    _rec(tmp_path, "b", "x", T0 - datetime.timedelta(hours=13))  # yesterday 23:00
    _rec(tmp_path, "c", "x", T0 - datetime.timedelta(hours=1))  # today
    days = [(d, expired) for d, _, _, expired in day_log.past_days(T0, directory=tmp_path)]
    assert days == [("2026-10-02", True), ("2026-10-03", False)]


def test_window_keeps_whole_answers_newest_first_by_budget(tmp_path):
    for i in range(10):
        _rec(tmp_path, f"question {i}", f"start {i} " + "a" * 2000 + f" end {i}", T0 - datetime.timedelta(minutes=10 - i))
    turns = day_log.recent_turns(T0, directory=tmp_path)
    kept, block, stats = day_log.window(turns, token_budget=1600)
    assert [t["user"] for t in kept] == ["question 7", "question 8", "question 9"]
    assert "end 9" in block and "end 7" in block  # not capped at ANSWER_CHAR_CAP
    assert stats == {"window_turns": 3, "window_tokens_est": stats["window_tokens_est"], "evicted_turns": 7}
    assert stats["window_tokens_est"] <= 1600


def test_window_always_keeps_the_newest_turn(tmp_path):
    _rec(tmp_path, "q", "a" * 50000, T0)
    kept, block, _ = day_log.window(day_log.recent_turns(T0, directory=tmp_path), token_budget=100)
    assert len(kept) == 1 and block.endswith("a")


def test_window_spans_midnight(tmp_path):
    _rec(tmp_path, "late last night", "x", datetime.datetime(2026, 10, 3, 23, 58))
    _rec(tmp_path, "just after midnight", "y", datetime.datetime(2026, 10, 4, 0, 1))
    kept, _, _ = day_log.window(day_log.recent_turns(datetime.datetime(2026, 10, 4, 0, 2), directory=tmp_path))
    assert [t["user"] for t in kept] == ["late last night", "just after midnight"]


def test_archive_fetch_matches_by_date_prefix_and_caps(tmp_path):
    p = tmp_path / "archive.json"
    archive.write_entry("2026-10-03 | installer build", {"summary": "s", "details": "d" * 20000}, p)
    assert archive.index_keys(path=p) == ["2026-10-03 | installer build"]
    text, stats = archive.fetch(["2026-10-03 | something slightly off"], token_cap=100, path=p)
    assert stats["recall_hits"] == 1 and len(text) <= 400
    assert archive.fetch(["1999-01-01 | nope"], path=p)[1]["recall_hits"] == 0
