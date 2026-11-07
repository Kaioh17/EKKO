import json
from types import SimpleNamespace

from listener import segment_log


def test_write_appends_one_line_per_call_with_timestamp(tmp_path):
    path = tmp_path / "logs" / "segments.jsonl"  # parent doesn't exist yet
    segment_log.write({"event": "segment", "kind": "command", "stt_ms": 312}, path)
    segment_log.write({"event": "timeout"}, path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "segment" and first["kind"] == "command" and first["stt_ms"] == 312
    assert "ts" in first
    assert json.loads(lines[1])["event"] == "timeout"


def test_write_never_raises_on_an_unwritable_path(tmp_path):
    # A directory can't be opened for append: logging must not take the
    # listener down with it.
    segment_log.write({"event": "segment"}, tmp_path)


def test_reason_code_is_stable_across_numbers():
    # _screen_command_segment's reasons carry per-clip numbers; the log
    # groups by a short code so rejects can be counted.
    rc = segment_log.reason_code
    assert rc("only 0.20s long (floor 0.30s)") == "too short"
    assert rc("only 0.05s long (floor 0.30s)") == "too short"
    assert rc("not the enrolled speaker (similarity 0.100 < 0.25)") == "not the enrolled speaker"
    assert rc("no speech in it") == "no speech in it"
    assert rc("probably not speech (no_speech_prob 0.54 > 0.45, heard 'Play.')") == "probably not speech"
    assert rc("low-confidence transcript (avg_logprob -1.09 < -0.95, heard 'x')") == "low-confidence transcript"
    assert rc(None) is None


def test_overflow_event_only_for_input_overflow():
    assert segment_log.overflow_event(SimpleNamespace(input_overflow=True), gate_open=True) == {
        "event": "overflow",
        "gate_open": True,
    }
    assert segment_log.overflow_event(SimpleNamespace(input_overflow=False), gate_open=True) is None
    assert segment_log.overflow_event(None, gate_open=False) is None
