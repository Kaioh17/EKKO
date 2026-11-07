"""Native Spotify / Apple Music desktop control: detect, launch, search/play.

OS scripting only -- no web player, no OAuth, no cloud API.
  Windows: winreg / Get-AppxPackage to detect, os.startfile() on URIs.
  macOS:   osascript (AppleScript dictionaries of Spotify and Music).
  Linux:   which / flatpak to detect, xdg-open on spotify: URIs.

Every public function returns (ok, message): message is a clean, speakable
string either way, never an exception.

Safety (this handler backs a `transcript` slot, see scripts/README.md): the
query only ever reaches a percent-encoded URI or AppleScript *argv*
(`on run argv`), never script text or a shell line, and every subprocess
call is an argv list with shell=False.

Transport controls (pause/next/previous) are not here: scripts/<os>/
media_control.* already does that (playerctl/MPRIS on Linux).

Usage:
    python scripts/media_player.py "play the arcane soundtrack on spotify"
    python scripts/media_player.py --self-check
"""

import functools
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import quote

PLAYERS = ("spotify", "apple_music")  # preference order when none is named
NAMES = {"spotify": "Spotify", "apple_music": "Apple Music"}
_MAC_APP = {"spotify": "Spotify", "apple_music": "Music"}
_APPLE_MUSIC_WIN_PKG = "AppleInc.AppleMusicWin"

Result = tuple[bool, str]


# ---- detection ----

@functools.cache
def _apple_music_pfn() -> str | None:
    """Windows Store package family name of Apple Music, or None."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"(Get-AppxPackage -Name {_APPLE_MUSIC_WIN_PKG}).PackageFamilyName"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    return out.splitlines()[0] if out else None


def _run(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def is_installed(player: str) -> bool:
    try:
        if sys.platform == "win32":
            if player == "spotify":
                import winreg

                # The spotify: URI handler; both the desktop and the Store
                # installers register it.
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "spotify"))
                return True
            return _apple_music_pfn() is not None
        if sys.platform == "darwin":
            return _run(["osascript", "-e", f'id of application "{_MAC_APP[player]}"']).returncode == 0
        if sys.platform.startswith("linux"):
            if player == "apple_music":
                return False  # no Linux desktop app exists
            if shutil.which("spotify"):
                return True
            return bool(shutil.which("flatpak")) and _run(["flatpak", "info", "com.spotify.Client"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def _not_installed(player: str) -> str:
    """Says what's missing and, if the other player is installed, points
    there instead of leaving the user at a dead end. Doesn't switch on its
    own: playing in an app nobody asked for would be a guess."""
    if player == "apple_music" and sys.platform.startswith("linux"):
        msg = "Apple Music has no Linux desktop app."
    else:
        msg = f"{NAMES[player]} isn't installed on this computer."
    other = next(p for p in PLAYERS if p != player)
    if is_installed(other):
        msg += f" {NAMES[other]} is, just ask me to play it on {NAMES[other]}."
    return msg


def _failed(what: str, e: Exception) -> str:
    """Speakable text for an unexpected failure; the technical detail goes
    to stderr, which the listener log shows (routing/execute.py describe())."""
    print(f"media_player: {what}: {type(e).__name__}: {e}", file=sys.stderr)
    return f"I couldn't {what}. The details are in the log."


# ---- launch / play ----

def _open_uri(uri: str) -> None:
    if sys.platform == "win32":
        os.startfile(uri)  # noqa: S606 - fixed scheme, query percent-encoded
    else:
        _run(["xdg-open", uri]).check_returncode()


def _osa(lines: list[str], *argv: str) -> str:
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    done = _run([*cmd, *argv], timeout=15)
    done.check_returncode()
    return done.stdout.strip()


def launch(player: str) -> Result:
    if player not in NAMES:
        return False, f"I don't know a player called {player}."
    if not is_installed(player):
        return False, _not_installed(player)
    try:
        if sys.platform == "darwin":
            _osa([f'tell application "{_MAC_APP[player]}" to activate'])
        elif sys.platform == "win32" and player == "apple_music":
            os.startfile(rf"shell:AppsFolder\{_apple_music_pfn()}!App")  # noqa: S606
        else:
            _open_uri("spotify:")
    except (OSError, subprocess.SubprocessError) as e:
        return False, _failed(f"open {NAMES[player]}", e)
    return True, f"Opened {NAMES[player]}."


def _search(player: str, query: str) -> Result:
    name = NAMES[player]
    if sys.platform == "darwin":
        if player == "spotify":
            # ponytail: `play track` rejects a search URI, so this only opens
            # the results page, same as Windows/Linux. Starting the top hit
            # needs a track ID (Web API) or UI automation.
            _osa([
                "on run argv",
                'tell application "Spotify"',
                "activate",
                'open location ("spotify:search:" & item 1 of argv)',
                "end tell",
                "end run",
            ], quote(query))
            return True, f"Searching Spotify for {query}."
        # ponytail: `search` covers the local library only, not the Apple Music catalogue.
        found = _osa([
            "on run argv",
            'tell application "Music"',
            "activate",
            "set hits to search library playlist 1 for (item 1 of argv)",
            'if hits is {} then return "none"',
            "play item 1 of hits",
            "end tell",
            'return "ok"',
            "end run",
        ], query)
        if found == "none":
            return False, f"Couldn't find {query} in your Apple Music library."
        return True, f"Playing {query} on Apple Music."

    if player == "apple_music":  # Windows: the app exposes no search URI
        ok, msg = launch(player)
        return ok, f"{msg} Searching it from here isn't supported on Windows yet." if ok else msg

    # ponytail: spotify:search: opens the results page, it doesn't start the
    # top hit -- that needs the Web API (out of scope). Upgrade: MPRIS
    # OpenUri on Linux once we have a track URI to hand it.
    _open_uri("spotify:search:" + quote(query))
    return True, f"Searching Spotify for {query}."


def pick_player(requested: str | None) -> str | None:
    """The named player, or the first installed one in PLAYERS order."""
    if requested:
        return requested
    return next((p for p in PLAYERS if is_installed(p)), None)


def play(player: str | None = None, query: str | None = None) -> Result:
    """Launch `player` (or the first installed one) and, if `query` is
    given, search for and play it there."""
    chosen = pick_player(player)
    if chosen is None:
        return False, "Neither Spotify nor Apple Music is installed."
    if chosen not in NAMES:
        return False, f"I don't know a player called {chosen}."
    if not query:
        return launch(chosen)
    if not is_installed(chosen):
        return False, _not_installed(chosen)
    try:
        return _search(chosen, query)
    except (OSError, subprocess.SubprocessError) as e:
        return False, _failed(f"play that on {NAMES[chosen]}", e)


# ---- request parsing (the raw utterance from the play_media intent) ----

_PLAYER_RE = re.compile(
    r"\b(?:(?:on|in|with|using|from|via|through)\s+)?(?:the\s+)?(?:my\s+)?"
    r"(spotify|apple\s+music|itunes)(?:\s+app)?\b"
)
_LEADING_RE = re.compile(
    r"^(?:hey\s+ekko|ekko|okay|ok|please|can\s+you|could\s+you|would\s+you|will\s+you|"
    r"i\s+want\s+to|i\s+wanna|i'd\s+like\s+to|let's|go\s+ahead\s+and|"
    r"play|put\s+on|throw\s+on|put|open\s+up|open|launch|start\s+up|start|pull\s+up|fire\s+up|"
    r"listen\s+to|me|some)\b\s*"
)
_TRAILING_RE = re.compile(r"\s*\b(?:for\s+me|please|now)$")
# Nothing worth searching for: just open the app.
_GENERIC = {"", "music", "some music", "a song", "something", "anything", "songs", "it", "the music"}


def parse_request(text: str) -> tuple[str | None, str | None]:
    """'play the arcane soundtrack on spotify' -> ('spotify', 'the arcane soundtrack')."""
    t = re.sub(r"[^\w\s'-]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()

    player = None
    m = _PLAYER_RE.search(t)
    if m:
        player = "spotify" if m.group(1) == "spotify" else "apple_music"
        t = _PLAYER_RE.sub(" ", t)
        t = re.sub(r"\s+", " ", t).strip()

    prev = None
    while prev != t:
        prev = t
        t = _LEADING_RE.sub("", t).strip()
        t = _TRAILING_RE.sub("", t).strip()
    return player, (None if t in _GENERIC else t)


def _self_check() -> None:
    cases = {
        "play the Arcane soundtrack": (None, "the arcane soundtrack"),
        "play the arcane soundtrack on spotify": ("spotify", "the arcane soundtrack"),
        "Can you play Taylor Swift on Apple Music?": ("apple_music", "taylor swift"),
        "open spotify": ("spotify", None),
        "open up the spotify app": ("spotify", None),
        "launch apple music": ("apple_music", None),
        "put on some music": (None, None),
        "play some lo-fi music for me": (None, "lo-fi music"),
        "i want to listen to some jazz": (None, "jazz"),
        "put apple music on": ("apple_music", None),
        "play my workout playlist in itunes": ("apple_music", "my workout playlist"),
    }
    for text, expected in cases.items():
        got = parse_request(text)
        assert got == expected, f"{text!r}: {got} != {expected}"

    global is_installed
    real = is_installed
    try:
        is_installed = lambda p: p == "apple_music"  # noqa: E731
        assert pick_player(None) == "apple_music"
        assert pick_player("spotify") == "spotify"
        assert play("spotify", "x") == (False, _not_installed("spotify"))
        if not sys.platform.startswith("linux"):
            assert play("spotify")[1] == (
                "Spotify isn't installed on this computer. Apple Music is, just ask me to play it on Apple Music."
            )
        is_installed = lambda p: False  # noqa: E731
        assert play() == (False, "Neither Spotify nor Apple Music is installed.")
    finally:
        is_installed = real
    print("media_player: self-check passed.")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        _self_check()
        raise SystemExit(0)
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    player, query = parse_request(sys.argv[1])
    print(f"parsed: player={player!r} query={query!r}", file=sys.stderr)
    ok, message = play(player, query)
    print(message)
    print(f"SPEAK: {message}")
    # 4 = failed with a spoken explanation (routing/execute.py
    # EXIT_EXPLAINED_FAILURE); every failure message here is user-facing.
    raise SystemExit(0 if ok else 4)
