"""
Python orchestration wrapper around scripts/<os>/media_control.{ps1,sh}.

This is the pattern for all EKKO OS-level handlers: the script does the
OS-level thing and returns JSON, Python parses it and decides what to do
with the result. Keeps decision logic out of the shell script.

Not named by any routing/intents.yaml handler today (nothing routes
media_pause/next/etc. through routing/execute.py yet), so this resolves
its script path directly via routing/host.py rather than going through a
validated IntentBundle.

Usage:
    python media_handler.py play_pause
    python media_handler.py next
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from routing import host  # noqa: E402

SCRIPT_PATH = host.script("media_control")

# Maps EKKO intent keys to the -Action/--action values media_control
# accepts. Intent names stay snake_case to match the rest of the intent
# config.
INTENT_TO_ACTION = {
    "play_pause": "PlayPause",
    "next_track": "Next",
    "previous_track": "Previous",
    "stop_playback": "Stop",
    "volume_up": "VolumeUp",
    "volume_down": "VolumeDown",
    "mute": "Mute",
}


def run_media_action(intent_key: str) -> dict:
    if intent_key not in INTENT_TO_ACTION:
        raise KeyError(f"Unknown media intent: {intent_key}")

    # routing/host.py's command() builds the OS-appropriate invocation --
    # -NoProfile/-ExecutionPolicy Bypass on Windows (unsigned local
    # scripts, no human around to run Set-ExecutionPolicy first, scoped to
    # this one invocation), `bash <path>` on Linux.
    result = subprocess.run(
        host.command(SCRIPT_PATH, {"action": INTENT_TO_ACTION[intent_key]}),
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode != 0 and not result.stdout.strip():
        return {
            "status": "error",
            "error": result.stderr.strip() or "media_control script failed with no output",
        }

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error": f"Could not parse script output: {result.stdout[:200]}",
        }


def response_key_for(result: dict) -> str:
    """Maps the script result to an audio_playback.py response key."""
    # Media keys are fire-and-forget, the OS won't tell us whether a
    # player actually consumed the keypress. So there's no honest way to
    # report 'already_done' here, treat a sent key as task_complete and
    # let the user hear the music (or not) as the real feedback.
    return "task_complete" if result.get("status") == "ok" else "error"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("intent", choices=sorted(INTENT_TO_ACTION.keys()))
    args = parser.parse_args()

    outcome = run_media_action(args.intent)
    print(json.dumps(outcome, indent=2))
    print(f"Response key: {response_key_for(outcome)}")