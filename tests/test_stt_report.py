import json

import pytest

from listener import stt_report

ECHO = frozenset({"access denied", "command confirmed"})


def _seg(outcome, stt_ms, text="", reason=None, kind="command"):
    return {"event": "segment", "kind": kind, "outcome": outcome, "reason": reason, "stt_ms": stt_ms, "text": text}


RECORDS = [
    {"event": "activation", "source": "wake"},
    {"event": "overflow", "gate_open": False},
    _seg("accepted", 300, "Open Chrome."),
    {"event": "activation", "source": "manual"},
    _seg("rejected", 100, reason="no speech in it"),
    {"event": "timeout"},
    {"event": "activation", "source": "manual"},
    {"event": "overflow", "gate_open": True},
    {"event": "timeout"},
    {"event": "activation", "source": "wake"},
    _seg("accepted", 500, "Access denied."),
    _seg("rejected", 50, reason="only 0.10s long", kind="idle"),  # idle segments aren't commands
]


def test_summarize_structured_records():
    s = stt_report.summarize(RECORDS, echo_phrases=ECHO)
    assert s["activations"] == 4
    assert s["first_try_accepts"] == 2  # 1st and 4th activation
    assert s["silent_timeouts"] == 1  # 3rd: timed out with no segment at all
    assert s["overflows"] == 2
    assert s["overflows_gate_open"] == 1
    assert s["overflow_activations"] == 2
    assert s["accepted"] == 2
    assert s["rejected"] == {"no speech in it": 1}
    assert s["stt_p50_ms"] == pytest.approx(300)
    assert s["stt_p90_ms"] == pytest.approx(460)
    assert s["echo_accepts"] == 1


def test_summarize_empty_is_well_formed():
    s = stt_report.summarize([], echo_phrases=ECHO)
    assert s["activations"] == 0 and s["accepted"] == 0 and s["rejected"] == {}
    assert s["stt_p50_ms"] is None and s["stt_p90_ms"] is None


# Real line shapes from backend/logs/listener.log, including the em dash that
# reads back as U+FFFD from the Windows console encoding.
LEGACY_LOG = [
    "[19:56:58] manual wake triggered \ufffd active listening, say your command",
    "[stream warning] input overflow",
    "[19:57:00] speech started",
    "[19:57:02] speech ended (2.10s)",
    "  transcript: 'Open Chrome.'",
    "[20:00:28] wake word detected (verification disabled with --no-verify) \ufffd active listening",
    "[20:00:29] speech ended (0.90s)",
    "  ignoring segment: no speech in it \ufffd still listening (8.0s left)",
    "[20:00:38] no command heard within 10s, back to idle",
    "[20:01:35] manual wake triggered \ufffd active listening, say your command",
    "[20:01:45] no command heard within 10s, back to idle",
]
LEGACY_TRANSCRIPTS = [
    json.dumps({"timestamp": "2026-11-06T22:32:23.805052",
                "wav_path": "C:\\captures\\speech_20261106_223222_502341.wav", "transcript": "Search for images."}),
    json.dumps({"timestamp": "2026-11-06T22:32:14.636619", "wav_path": None, "transcript": "[wake] hey_ekko"}),
    "",
    json.dumps({"timestamp": "2026-11-06T22:32:40.083181",
                "wav_path": "C:\\captures\\speech_20261106_223239_083181.wav", "transcript": "Access denied."}),
]


def test_summarize_legacy_logs():
    s = stt_report.summarize_legacy(LEGACY_LOG, LEGACY_TRANSCRIPTS, echo_phrases=ECHO)
    assert s["activations"] == 3
    assert s["first_try_accepts"] == 1
    assert s["silent_timeouts"] == 1
    assert s["overflows"] == 1
    assert s["overflow_activations"] == 1
    assert s["accepted"] == 1
    assert s["rejected"] == {"no speech in it": 1}
    # 1.302711 s and 1.000000 s from save time to transcript logged
    assert s["stt_p50_ms"] == pytest.approx(1151.4, abs=0.1)
    assert s["echo_accepts"] == 1


def test_report_prefers_segments_log_and_falls_back_to_legacy(tmp_path):
    seg = tmp_path / "segments.jsonl"
    log = tmp_path / "listener.log"
    tr = tmp_path / "transcripts.jsonl"
    log.write_text("\n".join(LEGACY_LOG), encoding="utf-8")
    tr.write_text("\n".join(LEGACY_TRANSCRIPTS), encoding="utf-8")

    assert stt_report.report(seg, log, tr, echo_phrases=ECHO)["source"] == "legacy"

    seg.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n\nnot json\n", encoding="utf-8")
    s = stt_report.report(seg, log, tr, echo_phrases=ECHO)
    assert s["source"] == "segments" and s["activations"] == 4


def test_ekko_phrases_cover_responses_and_prompt_only_lines():
    p = stt_report.ekko_phrases()
    assert "anything else" in p  # a current RESPONSES line
    # Retired lines still in DEFAULT_INITIAL_PROMPT: Whisper emits them on
    # near-silence, so they are never the user.
    assert {"access denied", "command confirmed", "already done"} <= p
    assert "open chrome" not in p  # a real command in the same prompt


CHECKBACKS = """# STT improvements

## Check-backs

One row per re-measurement.

| Date | Change |
|---|---|
| 2026-11-06 | baseline |

## After

untouched text
"""


def test_append_checkback_adds_row_and_leaves_rest_identical(tmp_path):
    md = tmp_path / "STT_IMPROVEMENTS.md"
    md.write_text(CHECKBACKS, encoding="utf-8")
    stt_report.append_checkback(md, ["2026-11-07", "pre-roll"])
    assert md.read_text(encoding="utf-8") == CHECKBACKS.replace(
        "| 2026-11-06 | baseline |\n", "| 2026-11-06 | baseline |\n| 2026-11-07 | pre-roll |\n"
    )


def test_append_checkback_table_at_end_of_file(tmp_path):
    md = tmp_path / "STT_IMPROVEMENTS.md"
    md.write_text("## Check-backs\n\n| Date | Change |\n|---|---|\n| 2026-11-06 | baseline |", encoding="utf-8")
    stt_report.append_checkback(md, ["2026-11-07", "x"])
    assert md.read_text(encoding="utf-8").endswith("| 2026-11-06 | baseline |\n| 2026-11-07 | x |\n")


def test_append_checkback_without_section_raises(tmp_path):
    md = tmp_path / "x.md"
    md.write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        stt_report.append_checkback(md, ["a", "b"])


def _cmd(i, ms, outcome="accepted", reason=None):
    return {"event": "segment", "kind": "command", "outcome": outcome, "reason": reason, "stt_ms": ms, "text": f"t{i}"}


def test_recent_counts_only_the_last_n_command_segments(tmp_path):
    seg = tmp_path / "segments.jsonl"
    records = [{"event": "activation"}] + [_cmd(i, 1000) for i in range(30)]  # old and slow
    records += [{"event": "overflow", "gate_open": False}]  # older than the window: not counted
    records += [_cmd(i, 200) for i in range(10)] + [{"event": "overflow", "gate_open": True}]
    records += [_cmd(i, 200) for i in range(40)]  # the last 50 commands: recent and fast
    seg.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    r = stt_report.recent(seg, n=50)
    assert r["n_segments"] == 50 and r["accepted"] == 50
    assert r["stt_p50_ms"] == pytest.approx(200) and r["stt_p90_ms"] == pytest.approx(200)
    assert r["overflows"] == 1  # the one inside the window


def test_recent_groups_rejections_by_reason(tmp_path):
    seg = tmp_path / "segments.jsonl"
    records = [_cmd(0, 300), _cmd(1, 90, "rejected", "no speech in it"), _cmd(2, 80, "rejected", "no speech in it"),
               _cmd(3, 50, "rejected", "EKKO's own voice")]
    seg.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    r = stt_report.recent(seg)
    assert r["accepted"] == 1 and r["rejected"] == {"no speech in it": 2, "EKKO's own voice": 1}
    assert r["n_segments"] == 4


def test_recent_with_no_log_is_empty_but_well_formed(tmp_path):
    r = stt_report.recent(tmp_path / "missing.jsonl")
    assert r["n_segments"] == 0 and r["accepted"] == 0 and r["rejected"] == {}
    assert r["stt_p50_ms"] is None and r["stt_p90_ms"] is None
