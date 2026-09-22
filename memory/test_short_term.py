"""Self-check for short_term.py's soft-delete behavior.

Usage:
    python memory/test_short_term.py
"""

import json
import tempfile
from pathlib import Path

try:
    from . import short_term
except ImportError:
    import short_term


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "short_term.json"
        log_path = Path(tmp) / "short_memory.jsonl"

        turn = short_term.write_short_memory(
            prior_question="what's the weather",
            prior_answer="sunny",
            follow_up="want the hourly breakdown?",
            path=path,
        )
        assert turn.status == "active"
        assert short_term.read_active_short_memory(path) is not None

        # Unanswered follow_up, session ends (decline/timeout/hard stop) --
        # soft delete, not a hard delete.
        short_term.clear_short_memory(path, log_path)
        assert path.exists(), "soft delete must not remove the file"
        stale = short_term.read_short_memory(path)
        assert stale.status == "stale"
        assert stale.follow_up_answer is None  # the failure point itself

        # The LLM-facing reader must no longer surface it.
        assert short_term.read_active_short_memory(path) is None

        # The failure is preserved in the log.
        entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 1
        assert entries[0]["follow_up_answer"] is None

        # Clearing an already-stale turn is a no-op, not a duplicate log entry.
        short_term.clear_short_memory(path, log_path)
        entries = log_path.read_text(encoding="utf-8").splitlines()
        assert len(entries) == 1

        # A fresh turn always overwrites and starts active again.
        short_term.write_short_memory(
            prior_question="second question",
            prior_answer="second answer",
            follow_up="second follow-up?",
            path=path,
        )
        assert short_term.read_active_short_memory(path) is not None
        assert short_term.read_active_short_memory(path).history == (), "a stale session must not leak in"

        # Same session: the earlier turn moves into history, with its reply.
        short_term.write_short_memory(follow_up_answer="the reply", path=path)
        short_term.write_short_memory(prior_question="q3", prior_answer="a3", follow_up="f3?", path=path)
        turn = short_term.read_active_short_memory(path)
        assert [h["prior_question"] for h in turn.history] == ["second question"]
        assert turn.history[0]["follow_up_answer"] == "the reply"

        # Ending the session drops the chain.
        short_term.clear_short_memory(path, log_path)
        short_term.write_short_memory(prior_question="q4", prior_answer="a4", follow_up="f4?", path=path)
        assert short_term.read_active_short_memory(path).history == ()

    print("ok")


if __name__ == "__main__":
    main()
