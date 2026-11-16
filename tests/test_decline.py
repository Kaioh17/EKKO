import json


from listener import decline
from listener.decline import is_decline
from llm_fallback.brain.parsing import parse_result


def _reply(**kw):
    return json.dumps({"intent": None, "slots": {}, "answer": None, **kw})


def test_bare_decline_is_flagged():
    assert parse_result(_reply(decline=True, reason="closing")).decline is True


def test_decline_defaults_false_and_noise_stays_noise():
    assert parse_result(_reply()).decline is False
    assert parse_result(_reply(decline=False)).decline is False


def test_decline_ignored_when_model_also_answers_picks_or_asks_for_more():
    assert parse_result(_reply(decline=True, answer="Sure.")).decline is False
    assert parse_result(_reply(decline=True, intent="open_app")).decline is False
    assert parse_result(_reply(decline=True, recall=["k"])).decline is False
    assert parse_result(_reply(decline=True, search="news")).decline is False


def test_decline_must_be_a_real_boolean():
    assert parse_result(_reply(decline="true")).decline is False


# Real transcripts from the fallback logs that used to reach the model.
CAUGHT = [
    "No, nothing else. Jesus Christ.", "Nothing else, bro.", "That's it.", "No, it's all good.",
    "That’s all good.", "That is all.", "Nothing else. Thank you.", "Nothing else, bitch.",
    "Oh, nothing else.", "No, that's okay.", "No, no, it's okay.", "Now, you're good.",
    "No, no need.", "Nothing. Nothing.", "Nothing for now.", "Nothin' else.",
    "Nothing else. Nothing.", "No, no, it's okay. Nothing else.", "Nothing else, bro. Jesus Christ.",
    "Bye bye.", "Go back to sleep.", "I'm good, thanks man.", "Nope.", "Thanks, bro.",
]
# Commands, questions and bare fillers that must still route normally.
KEPT = [
    "No, open Chrome.", "Nothing else matters, play that song.", "No, I need to open Robinhood.",
    "Yes.", "Okay.", "Bro.", "So.", "What's the weather?", "That's amazing.",
    "No. For now, I am just curious with the job market in the future.",
    "Nothing else. They are talking to themselves, bro.",
]


def test_wider_decline_map_catches_logged_phrases():
    missed = [t for t in CAUGHT if not is_decline(t)]
    assert not missed, missed


def test_wider_decline_map_leaves_commands_alone():
    wrongly = [t for t in KEPT if is_decline(t)]
    assert not wrongly, wrongly


def test_no_dead_phrases():
    """An entry the other entries (plus filler) already cover is clutter that implies it matters."""
    squashed = [decline.squash(p) for p in decline.PHRASES]
    assert len(set(squashed)) == len(squashed), "duplicate after squash"
    derivable = [p for p, s in zip(decline.PHRASES, squashed) if decline._covered(s, decline._KNOWN - {s})]
    assert not derivable, derivable


# --- the turn: deterministic first, the model only for what it misses ---------

import listener.vad_listener as vl  # noqa: E402
from llm_fallback.brain.outcome import FallbackOutcome  # noqa: E402
from routing.bundle import IntentBundle  # noqa: E402


class _Rig:
    """_handle_command with every side effect recorded instead of performed."""

    def __init__(self, monkeypatch, outcome):
        self.said, self.events, self.turns, self.routed, self.asked = [], [], [], [], []
        rig = self

        class Router:
            def route(self, transcript):
                rig.routed.append(transcript)
                return IntentBundle.no_match(transcript, 0.3)

        class Ctx:
            def as_kwargs(self):
                return {}

        def ask(transcript, **kw):
            rig.asked.append(transcript)
            return outcome

        self.router = Router()
        monkeypatch.setattr(vl, "_say", lambda voice, key, *a, **kw: self.said.append(key))
        monkeypatch.setattr(vl, "record_turn", lambda text, reply, **kw: self.turns.append((text, kw.get("kind"))))
        monkeypatch.setattr(vl.segment_log, "write", self.events.append)
        monkeypatch.setattr(vl, "attempt_with_failover", ask)
        monkeypatch.setattr(vl, "gather_context", lambda: Ctx())
        monkeypatch.setattr(vl, "default_provider", lambda: "fake")

    def run(self, transcript, expecting_reply):
        return vl._handle_command(transcript, self.router, False, None, None, None, None, expecting_reply=expecting_reply)

    def declines(self):
        return [(e["via"], e["text"]) for e in self.events if e["event"] == "decline"]


def _outcome(**kw):
    base = dict(bundle=None, answer=None, urls=[], raw_intent=None, raw_slots={}, reason="closing", provider="p",
                model="m", usage={}, latency_seconds=0.1, error=None)
    return FallbackOutcome(**{**base, **kw})


def test_known_phrase_ends_the_turn_without_routing_or_a_model_call(monkeypatch):
    rig = _Rig(monkeypatch, _outcome())
    key, bundle, *_ = rig.run("Nothing else, bro.", expecting_reply=True)
    assert key == "ok_bye" and vl._follow_up_key(bundle, key) is None
    assert rig.said == ["ok_bye"] and rig.turns == [("Nothing else, bro.", "decline")]
    assert rig.routed == [] and rig.asked == []
    assert rig.declines() == [("deterministic", "Nothing else, bro.")]


def test_missed_phrase_falls_back_to_the_model_which_ends_the_turn(monkeypatch):
    rig = _Rig(monkeypatch, _outcome(decline=True))
    key, bundle, *_ = rig.run("That will be quite enough from me today.", expecting_reply=True)
    assert key == "ok_bye" and vl._follow_up_key(bundle, key) is None
    assert rig.routed and rig.asked  # deterministic check passed it on
    assert rig.said == ["ok_bye"] and rig.turns[0][1] == "decline"
    assert rig.declines()[0][0] == "inference"


def test_known_phrase_outside_a_reply_window_is_not_cut_short(monkeypatch):
    """"no" as the first command after a wake word still routes; the model may still end it."""
    rig = _Rig(monkeypatch, _outcome(decline=True))
    rig.run("Nothing else.", expecting_reply=False)
    assert rig.routed and rig.asked and rig.declines()[0][0] == "inference"


def test_unflagged_noise_still_gets_the_old_not_understood_path(monkeypatch):
    rig = _Rig(monkeypatch, _outcome())
    key, bundle, *_ = rig.run("Blorp.", expecting_reply=True)
    assert key == "not_understood" and rig.declines() == []
    assert vl._follow_up_key(bundle, key) == "anything_else"


def test_command_during_a_reply_window_routes_normally(monkeypatch):
    rig = _Rig(monkeypatch, _outcome())
    rig.run("No, open Chrome.", expecting_reply=True)
    assert rig.routed == ["No, open Chrome."] and rig.declines() == []
