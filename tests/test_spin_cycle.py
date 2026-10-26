import datetime
import json

from memory import archive, day_log, spin_cycle

T0 = datetime.datetime(2026, 10, 4, 12, 0)


def _setup(tmp_path):
    day_log.record_turn("plan the build", "ok", now=T0 - datetime.timedelta(hours=40), directory=tmp_path / "days")
    day_log.record_turn("fresh", "ok", now=T0, directory=tmp_path / "days")
    return dict(
        now=T0,
        day_dir=tmp_path / "days",
        archive_path=tmp_path / "archive.json",
        log_path=tmp_path / "spin.jsonl",
    )


def _fake(date, block):
    assert "plan the build" in block
    return {"key_summary": "installer build", "summary": "s", "topics": ["build"], "details": "d"}, {"provider": "fake"}


def test_compacts_old_day_and_keeps_today(tmp_path):
    kw = _setup(tmp_path)
    (res,) = spin_cycle.run(summarize=_fake, **kw)
    assert res["ok"] and res["key"] == "2026-10-02 | installer build"
    assert list(archive.read_archive(kw["archive_path"])) == [res["key"]]
    assert [p.name for p in kw["day_dir"].glob("*.jsonl")] == ["2026-10-04.jsonl"]
    assert json.loads(kw["log_path"].read_text().splitlines()[0])["ok"] is True


def test_failure_keeps_day_file_and_retry_succeeds(tmp_path):
    kw = _setup(tmp_path)

    def boom(date, block):
        raise RuntimeError("provider down")

    (res,) = spin_cycle.run(summarize=boom, **kw)
    assert not res["ok"] and "provider down" in res["error"]
    assert (kw["day_dir"] / "2026-10-02.jsonl").exists() and not kw["archive_path"].exists()
    (res,) = spin_cycle.run(summarize=_fake, **kw)
    assert res["ok"]


def test_rerun_never_summarizes_a_date_twice(tmp_path):
    kw = _setup(tmp_path)
    spin_cycle.run(summarize=_fake, **kw)
    day_log.record_turn("plan the build", "ok", now=T0 - datetime.timedelta(hours=40), directory=kw["day_dir"])  # straggler
    calls = []
    spin_cycle.run(summarize=lambda d, b: calls.append(d), **kw)
    assert calls == [] and len(archive.read_archive(kw["archive_path"])) == 1


def test_low_worth_day_is_dropped_without_summary(tmp_path):
    kw = _setup(tmp_path)
    (kw["day_dir"] / "2026-10-02.jsonl").unlink()
    old = T0 - datetime.timedelta(hours=40)
    for _ in range(5):
        day_log.record_turn("volume up", "ok", kind="command", now=old, directory=kw["day_dir"])
    (res,) = spin_cycle.run(summarize=lambda d, b: 1 / 0, **kw)
    assert res["skipped"] == "low worth" and not kw["archive_path"].exists()
    assert [p.name for p in kw["day_dir"].glob("*.jsonl")] == ["2026-10-04.jsonl"]
