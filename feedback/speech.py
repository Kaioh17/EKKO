"""
Spoken feedback via Piper TTS, synthesized at runtime, every time.
There is no pre-recorded-WAV tier anymore (an earlier version of this
module played back fixed recordings via sounddevice + scipy; see git
history / readme.md if that design is ever worth revisiting). One
code path instead of two: a known system event and, once stage 3
produces genuinely dynamic/LLM text, arbitrary novel text both go
through the same speak() call, synthesized fresh and never written to
disk. That also means dynamic responses need no separate system
later, this module already handles any string, not just the ones in
RESPONSES.

Piper (https://github.com/OHF-Voice/piper1-gpl, `pip install
piper-tts`) is local, free, ONNX-based, CPU-fast, fits the project's
zero-budget/local-first constraints (see readme.md's "Audio feedback"
stack row). It needs a downloaded voice model, not shipped with the
pip package: a `.onnx` file plus its matching `.onnx.json` config,
fetched once from
https://huggingface.co/rhasspy/piper-voices/tree/main and placed
under `feedback/models/`. That's a manual step, not a code task, same
reasoning as this project's other pretrained-model/asset downloads
(see voice_auth/auedio.md). DEFAULT_MODEL_PATH below names the
default voice, en_GB-alba-medium; pass --model to use a different one,
e.g. en_GB-alan-medium, which is also in models/ and was the default
before. Changing the constant is enough to switch EKKO's voice
everywhere: listener/vad_listener.py takes its --feedback-model default
from it rather than naming a file of its own.

Loading the voice (ONNX model init) is the expensive part, so this
mirrors how the rest of the pipeline handles its other models
(speaker embedding model, Whisper): load once via load_voice() at
listener startup, not per call. speak()/say() both take an
already-loaded PiperVoice rather than loading one themselves.

Usage:
    python feedback/speech.py --list
    python feedback/speech.py --key generic_ack
    python feedback/speech.py --text "anything at all"
"""

import argparse
import contextlib
import random
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from piper import PiperVoice

# Anchored to this file rather than cwd, same reasoning as the other
# packages' DEFAULT_* paths: usage examples run this as `python
# feedback/speech.py` from the repo root, where a bare relative
# "models" would only resolve correctly from that one cwd.
_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_PATH = _PACKAGE_DIR / "models" / "en_GB-alba-medium.onnx"

# Deterministic *set* of texts for known system events, not a single fixed
# string per event: still a fixed dict lookup, same "why" as the design
# this replaced (no ambiguity about which events map to which key, no
# ambiguity about the source of the words), but hearing the exact same
# sentence every single time a command runs is what made EKKO sound like a
# script rather than something to talk to. render() below picks one
# variant per call, so the event->possible-text mapping is still closed
# and hand-written, only which member of that set gets spoken varies.
#
# Some keys are also intent-aware: "command_confirmed:open_app" etc. are
# looked up in preference to the bare "command_confirmed" list whenever
# the caller knows which intent matched, so a confirmation can name what
# actually happened ("Opened Chrome.") instead of only ever the generic
# "Done." Slot placeholders (`{app}`, `{query}`) are filled from
# bundle.slots by render(); a variant with no placeholder just ignores
# whatever slots are passed. This keeps speech.py itself free of any
# import from routing/ (the intent names below are plain strings, matched
# by coincidence with routing/intents.yaml's keys, not a dependency on
# it), so the listener -> feedback direction stays one-way: a key that
# doesn't have an intent-specific entry silently falls back to the
# generic list instead of erroring.
#
# "anything_else:<domain>" is the same mechanism, one level up: keyed by
# domains/registry.yaml's key (plain string, same "coincidental match, no
# import from domains/" independence as the intent-aware keys above), not
# by intent. This exists so a domain expert's own voice (see
# domains/finance/persona.md's "calm, direct" voice) doesn't get undercut
# by the flat "Anything else I can help with?" the moment
# llm_fallback/gemini's `follow_up` field happens to come back null for
# one turn -- see vad_listener.py's _handle_command()/render()'s `domain`
# param. Still a closed, hand-written set, same as everything else here;
# adding a second domain means adding its own "anything_else:<domain>"
# entry, same as adding an intent means adding its own
# "command_confirmed:<intent>" entry.
RESPONSES: dict[str, list[str]] = {
    "generic_ack": [
        "Hi Mubaraq, what are we doing today?",
        "Hey, what do you need?",
        "I'm listening, what's up?",
    ],
    # Spoken when active listening's --active-window elapses with no
    # command heard (see vad_listener.py's awaiting_command timeout), so
    # there's an audible signal that EKKO stopped listening rather than
    # leaving that to only show up in the console/log.
    "listening_timeout": [
        "Never mind, going back to sleep.",
        "No worries, I'll be here.",
        "Going quiet for now.",
    ],
    # --- stage-3 outcomes ---
    # routing/execute.py maps every routing and execution outcome to one
    # of these keys (see its RESPONSE_KEYS and response_key()).
    #
    # Generic fallback: spoken when the matched intent has no
    # "command_confirmed:<intent>" entry below, which is also what any
    # future intent gets for free until it earns a more specific one.
    "command_confirmed": ["Done.", "Completed.", "All set.", "Got it, done."],
    # Intent-specific confirmations. Keys are "command_confirmed:<intent
    # name from routing/intents.yaml>". open_app and web_search carry a
    # slot worth naming out loud; open_task_manager, open_ghelper and
    # open_apple_music don't have one, but still get their own phrasing
    # rather than the generic list, since "Done." undersells "opened the
    # thing you asked for" when EKKO can just say what it did.
    "command_confirmed:open_app": [
        "Opened {app}.",
        "{app}'s open.",
        "There you go, {app}.",
        "Done, {app} is up.",
    ],
    "command_confirmed:web_search": [
        "Searching for {query}.",
        "Here's your search for {query}.",
        "Looking that up: {query}.",
        "Pulling up results for {query}.",
    ],
    "command_confirmed:open_apple_music": [
        "Apple Music's up.",
        "Opened Apple Music.",
        "There you go, enjoy the music.",
    ],
    "command_confirmed:open_task_manager": [
        "Task Manager's open.",
        "There you go.",
        "Opened Task Manager, take a look.",
    ],
    "command_confirmed:open_ghelper": [
        "G-Helper's open.",
        "There you go.",
        "Opened G-Helper.",
    ],
    # Bug-tolerant fallback only. system_diagnosis.ps1's actual
    # confirmation is dynamic text pulled from its own stdout (see
    # routing/execute.py's spoken_override() and vad_listener.py's _say(),
    # text_override), not this list -- these variants are only reached if
    # that SPEAK: line is ever missing or malformed, so a wiring bug still
    # says something sensible instead of silently falling through further
    # to the bare "command_confirmed" list.
    "command_confirmed:system_diagnosis": [
        "Ran a system check.",
        "Diagnostics done.",
    ],
    # Bug-tolerant fallback only, same reasoning as
    # "command_confirmed:system_diagnosis" above: llm_fallback's Task 2
    # (see llm_fallback/README.md) always speaks its actual generated
    # answer via vad_listener.py's _say() text_override, this list is only
    # ever reached if that text somehow came back empty, so a wiring bug
    # still says something sensible instead of silently falling through.
    "ambiguous_answer": [
        "Sorry, I don't have an answer for that.",
        "I'm not sure about that one.",
    ],
    # Spoken right after "ambiguous_answer", only when
    # llm_fallback/claude_code/fallback.py's open_research_tabs() actually
    # launched Brave (see vad_listener.py's _handle_command() -- a missing
    # Brave install or a script error skips this key entirely rather than
    # claiming a tab was opened that wasn't). Not shown in the response
    # panel (see _say()'s show_in_panel doc): this is a spoken aside about
    # what EKKO just did, not the answer itself.
    "research_tabs_opened": [
        "I'm also pulling up some helpful sites for you.",
        "I've opened a few helpful pages in Brave too.",
        "Also grabbing a couple of useful sites for you now.",
        "Pulling up some helpful sites in Brave as well.",
    ],
    "already_done": [
        "That's already done.",
        "Already taken care of.",
        "That's already open.",
    ],
    # Spoken instead of the generic "anything_else" specifically right
    # after open_app matches with app=brave (see vad_listener.py's
    # _follow_up_key), since opening the browser is the one outcome with
    # an obvious next step. A "no"/decline reply here ends the turn
    # exactly like it does after "anything_else" (_is_decline doesn't
    # care which prompt preceded it); a "yes" or a search phrase routes
    # normally through the same router as any other command, e.g. to
    # web_search or open_apple_music.
    "brave_search_prompt": [
        "Would you like to search up anything?",
        "Want me to look something up?",
        "Anything you'd like to search for?",
    ],
    # Spoken after any other outcome that actually said something (see
    # listener/vad_listener.py's `expecting_reply`), to re-arm active
    # listening for a follow-up instead of dropping back to idle.
    "anything_else": [
        "Anything else I can help with?",
        "What else do you need?",
        "Anything else?",
    ],
    # Preferred over the bare "anything_else" above whenever
    # domains/finance matched this turn's llm_fallback answer and Gemini's
    # own `follow_up` (a grounded, guardrail-constrained next step) came
    # back null -- see the "anything_else:<domain>" comment above RESPONSES
    # and domains/finance/persona.md's "Voice" section, which this is
    # written to match: calm, direct, still a question, never the
    # brighter/more generic default tone.
    "anything_else:finance": [
        "Anything else on that?",
        "Want to go over anything else?",
        "Anything else you'd like to work through?",
    ],
    # Spoken when a follow-up reply is recognized as a decline (see
    # `_is_decline` in vad_listener.py), ending the multi-turn loop.
    "ok_bye": [
        "Alright, let me know if you need anything.",
        "Okay, I'll be here if you need me.",
        "Sounds good, catch you later.",
    ],
    # No intent scored above the threshold. Says "didn't catch that"
    # rather than "I can't do that", since at this point EKKO genuinely
    # doesn't know which of the two it was.
    "not_understood": [
        "Sorry, I didn't catch that.",
        "Sorry, could you say that again?",
        "Didn't quite get that.",
    ],
    # Intent matched but a slot couldn't be filled, e.g. "open" with no
    # app named. Worth its own key precisely because it's the one failure
    # where EKKO knows enough to ask a useful question back.
    "missing_slot": [
        "I didn't catch which one you meant.",
        "Which one did you mean?",
        "Sorry, which one?",
    ],
    # A handler ran and failed. Deliberately vague, the exit code and
    # stderr go to the log, not out loud.
    "error": [
        "Something went wrong running that.",
        "That didn't work.",
        "Ran into a problem with that.",
    ],
    # Spoken when "hey ekko" fires but the voice doesn't match the
    # enrolled speaker (see vad_listener.py's is_wake_check branch).
    # Deliberately vague about who *did* say it, same reasoning as
    # "error" staying vague about what broke: there's nothing useful to
    # say back to a voice EKKO doesn't recognize.
    "access_denied": [
        "Sorry, I didn't recognize that voice.",
        "That's not a voice I recognize.",
        "I don't recognize you, sorry.",
    ],
}

DEFAULT_KEY = "generic_ack"


def load_voice(model_path: str | Path = DEFAULT_MODEL_PATH) -> PiperVoice:
    """Load the Piper voice model once. Raises FileNotFoundError with
    a pointer to the download location if the model hasn't been
    fetched yet, callers (vad_listener.py) should let that surface at
    startup rather than fail silently mid-pipeline.
    """
    model_path = Path(model_path)
    if not model_path.exists():
        raise FileNotFoundError(
            f"Piper voice model not found at {model_path}. Download a "
            "voice (.onnx + .onnx.json pair) from "
            "https://huggingface.co/rhasspy/piper-voices/tree/main and "
            f"place it there, or pass a different --model path."
        )
    return PiperVoice.load(str(model_path))


# Cross-process playback lock. Every spoken utterance in this project
# eventually calls speak() below, but not always from the same process:
# listener/vad_listener.py calls it in-process, while scripts/
# daily_briefing.py (and anything else launched as a detached window)
# shells out to `python feedback/speech.py --text ...` as a separate
# process instead. Two sounddevice streams from two different processes
# have no idea about each other -- nothing before this lock existed
# stopped both from calling sd.play() at once and coming out of the
# speakers on top of each other, which is exactly what happened the first
# time daily_briefing.py's news summary landed while vad_listener.py was
# still mid-sentence on the system-status SPEAK: line.
#
# A file lock (not an in-process threading.Lock) because the two callers
# genuinely are different OS processes; msvcrt.locking is stdlib-only, no
# new dependency, matches render_report.py's own use of msvcrt for its
# keypress wait. Held only around the actual sd.play()/sd.wait() call
# below, not synthesis -- there's no reason a second utterance can't be
# synthesizing while the first is still audibly playing, only the
# playback itself needs to be serialized.
_SPEECH_LOCK_PATH = Path(tempfile.gettempdir()) / "ekko_speech.lock"
# How long a second caller waits for the device before giving up and
# playing anyway. Deliberately not infinite: a stale lock (the process
# that held it crashed mid-utterance without releasing) must not
# permanently silence EKKO for everyone after it. Playing over a stuck
# lock's audio is the same "overlapping speech" bug this exists to fix,
# but a rare, self-healing one beats a total outage.
_SPEECH_LOCK_TIMEOUT = 10.0
_SPEECH_LOCK_POLL = 0.05


@contextlib.contextmanager
def _playback_lock(timeout: float = _SPEECH_LOCK_TIMEOUT, poll: float = _SPEECH_LOCK_POLL):
    """Held for the duration of one utterance's actual audio playback.
    Blocks until acquired or `timeout` elapses, in which case it gives up
    and lets playback proceed unlocked rather than deadlock -- see the
    module comment above _SPEECH_LOCK_PATH for why that's the right
    failure mode here.

    msvcrt.locking needs at least one byte in the file to lock; a fresh
    or empty lock file gets one written before the first lock attempt.
    """
    import msvcrt

    handle = open(_SPEECH_LOCK_PATH, "a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        deadline = time.monotonic() + timeout
        acquired = False
        while time.monotonic() < deadline:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                break
            except OSError:
                time.sleep(poll)
        if not acquired:
            print("[speech] playback lock busy past timeout, speaking anyway")

        try:
            yield
        finally:
            if acquired:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        handle.close()


def _stream_latency(stream) -> float:
    """Seconds of audio the device still has buffered downstream of us.

    PortAudio reports this per stream, but not uniformly: an OutputStream
    exposes a single float, a duplex Stream a (input, output) tuple, and
    some host APIs don't report one at all. Normalize all three to a
    float, and treat "unknown" as zero rather than guessing.
    """
    latency = getattr(stream, "latency", None)
    if isinstance(latency, (tuple, list)):
        latency = latency[-1] if latency else None
    try:
        return max(0.0, float(latency))
    except (TypeError, ValueError):
        return 0.0


def speak(voice: PiperVoice, text: str) -> float:
    """Synthesize `text` with an already-loaded Piper voice and play
    it immediately via sounddevice. No file is written to disk at any
    point, the audio exists only in memory for the duration of
    playback.

    Returns the time.monotonic() timestamp at which this audio actually
    stops coming out of the speakers, or 0.0 (after printing a warning)
    if synthesis or playback failed for any reason, callers in the
    listening pipeline must never crash because TTS hiccuped.

    That timestamp is later than sd.wait() returning, which is the whole
    reason it's returned rather than left for the caller to assume.
    wait() comes back once PortAudio has handed off its last buffer, but
    the device still has some of it queued: on WASAPI that tail can run a
    few hundred ms, and the room keeps ringing after that. Anything that
    needs to know when the sound is genuinely gone (see listener/'s
    MicGate) can only get it from here, where the stream is in scope.
    """
    try:
        chunks = list(voice.synthesize(text))
        if not chunks:
            print(f"[speech] Piper produced no audio for {text!r}")
            return 0.0
        # Concatenate rather than play chunk-by-chunk: sequential
        # play()/wait() calls per chunk introduce an audible gap
        # between them, one buffer plays cleanly. audio_int16_array is
        # AudioChunk's own numpy view of the samples (cached on first
        # access), no need to reparse audio_int16_bytes ourselves.
        audio = np.concatenate([c.audio_int16_array for c in chunks])
        sample_rate = chunks[0].sample_rate
        channels = chunks[0].sample_channels
        if channels > 1:
            audio = audio.reshape(-1, channels)
        # "high" latency, same reasoning as the input stream's own
        # latency="high" in listener/vad_listener.py: sd.play() opens a
        # fresh output stream every call, and the default (low) latency
        # gives PortAudio/WASAPI no headroom to actually get the device
        # flowing before it starts feeding samples. With no buffer, that
        # cold start ate the first syllable of every single utterance,
        # not intermittently -- there's no warm stream sitting around
        # between calls to hide it.
        # Locked for the same reason the module comment above
        # _SPEECH_LOCK_PATH gives: two different processes can both reach
        # this line at once (the listener speaking a SPEAK: confirmation,
        # a detached window's own feedback/speech.py --text call), and
        # sounddevice has no cross-process notion of "someone's already
        # using the output device." Synthesis above already happened
        # outside the lock, only the audible part is serialized.
        with _playback_lock():
            sd.play(audio, sample_rate, latency="high")
            # Read the latency off the stream play() just opened, while
            # it's still the active one, wait() may have closed it by the
            # time we come back.
            output_latency = _stream_latency(sd.get_stream())
            sd.wait()
    except Exception as exc:
        print(f"[speech] synthesis/playback failed: {exc}")
        return 0.0
    return time.monotonic() + output_latency


def render(
    response_key: str,
    intent: str | None = None,
    domain: str | None = None,
    slots: dict | None = None,
) -> str | None:
    """Pick one text variant for `response_key`, formatted with `slots`.

    Prefers "<response_key>:<intent>" over the bare key whenever both
    `intent` is given and that specific list exists in RESPONSES, e.g.
    "command_confirmed:open_app" over "command_confirmed" — see the
    comment above RESPONSES for why that's a plain string match rather
    than an import from routing/. Falls back to the bare key for any
    intent that doesn't have its own entry, which is also what a status
    that isn't intent-shaped (listening_timeout, not_understood, ...)
    gets by always taking this branch.

    `domain` is the same mechanism one level up ("<response_key>:<domain>",
    e.g. "anything_else:finance"), tried only when `intent` didn't already
    resolve to a specific entry -- the two never both apply in practice
    (a domain-matched turn is always a NO_MATCH bundle with intent=None),
    but intent stays the more specific lookup by construction if that ever
    changes. See the "anything_else:<domain>" comment above RESPONSES.

    random.choice() is the whole "rotation": which variant gets said is
    not tracked or cycled, just picked fresh each call, so the same text
    can repeat back to back. That's fine here, the point is not hearing
    the exact same sentence every time, not guaranteeing every sentence
    an equal turn.

    A variant with no `{slot}` placeholder ignores `slots` entirely, so
    plain lists like "anything_else" don't need one. If a variant does
    reference a placeholder `slots` doesn't have, that's a wiring bug (a
    template added without a matching slot), not something to crash the
    listening pipeline over, so it's logged and the call falls back to
    the generic key's own random variant instead of raising.
    """
    lookup_key = response_key
    if intent is not None:
        specific_key = f"{response_key}:{intent}"
        if specific_key in RESPONSES:
            lookup_key = specific_key
    if lookup_key == response_key and domain is not None:
        domain_key = f"{response_key}:{domain}"
        if domain_key in RESPONSES:
            lookup_key = domain_key

    variants = RESPONSES.get(lookup_key)
    if variants is None:
        return None
    text = random.choice(variants)
    if slots is None:
        return text
    try:
        return text.format(**slots)
    except (KeyError, IndexError):
        print(f"[speech] {lookup_key!r} variant {text!r} doesn't match slots {slots!r}")
        fallback = RESPONSES.get(response_key)
        return random.choice(fallback) if fallback else text


def say(
    voice: PiperVoice,
    response_key: str | None,
    intent: str | None = None,
    slots: dict | None = None,
) -> float:
    """Speak one variant of one of RESPONSES' texts by key. None means
    stay quiet, which is a real outcome rather than an error: an empty
    transcript means nothing was said, so there's nothing to answer.

    `intent` and `slots` are optional and only matter for intent-aware
    keys like "command_confirmed" (see render()); every other call site
    just omits them and gets the bare key's rotation.

    This replaces the old respond_to_intent() seam, which was written to
    be the place that inspects a matched intent and picks a key. That job
    turned out to belong further up: routing/execute.py is where both the
    routing status and the handler's exit code are known, so it does the
    picking (see its response_key()) and this just speaks the result.
    Which keeps feedback/ dependency-free, it knows nothing about intents
    (the `intent` argument here is an opaque string, not something this
    module inspects or imports routing/ to validate), and the listener ->
    feedback direction stays one-way.

    Returns whatever speak() returned: the monotonic timestamp at which
    the audio stops reaching the room, or 0.0 for "nothing was played",
    which covers both the deliberate silences below and real failures.
    """
    if response_key is None:
        return 0.0
    text = render(response_key, intent=intent, slots=slots)
    if text is None:
        # A caller asking for a key that doesn't exist is a wiring bug,
        # but not one worth taking the listening pipeline down over.
        print(f"[speech] unknown response key {response_key!r}")
        return 0.0
    return speak(voice, text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL_PATH),
        help=f"Path to a Piper voice .onnx file (default: {DEFAULT_MODEL_PATH}).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List RESPONSES keys and every text variant each one can speak.",
    )
    parser.add_argument(
        "--key",
        metavar="KEY",
        help="Speak a random variant of one of the RESPONSES texts, e.g. --key generic_ack.",
    )
    parser.add_argument(
        "--intent",
        metavar="INTENT",
        help="Prefer KEY:INTENT's variants over KEY's, e.g. --key command_confirmed --intent open_app.",
    )
    parser.add_argument(
        "--slot",
        metavar="NAME=VALUE",
        action="append",
        default=[],
        help="Fill a {placeholder} in the chosen variant, e.g. --slot app=chrome. Repeatable.",
    )
    parser.add_argument(
        "--text",
        metavar="TEXT",
        help="Speak arbitrary text directly, not limited to RESPONSES.",
    )
    args = parser.parse_args()

    if args.list:
        for key, variants in RESPONSES.items():
            print(f"  {key}")
            for variant in variants:
                print(f"      {variant!r}")
        sys.exit(0)

    if args.key:
        if args.key not in RESPONSES:
            print(f"[speech] unknown response key {args.key!r}")
            sys.exit(1)
        try:
            slots = dict(pair.split("=", 1) for pair in args.slot)
        except ValueError:
            print(f"[speech] --slot expects NAME=VALUE, got {args.slot!r}")
            sys.exit(1)
        text_to_speak = render(args.key, intent=args.intent, slots=slots)
    elif args.text:
        text_to_speak = args.text
    else:
        parser.print_help()
        sys.exit(0)

    try:
        loaded_voice = load_voice(args.model)
    except FileNotFoundError as exc:
        print(f"[speech] {exc}")
        sys.exit(1)

    # speak() returns a monotonic timestamp (always truthy) on success and
    # 0.0 on failure, so this stays a plain success check.
    if not speak(loaded_voice, text_to_speak):
        sys.exit(1)
