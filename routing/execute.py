"""Runs the handler named by a validated IntentBundle. Nothing else.

This is the only place in EKKO that starts a process, and it is reachable
only through an IntentBundle, never a string. That's the whole security
design: by the time control arrives here, the intent came from the closed
set in intents.yaml, the handler path was checked at config load to be a
.ps1 inside scripts/, and every slot value is a canonical config value
rather than a span of transcript. There is no user-controlled text left
to sanitise, because none of it survived routing.

Three habits reinforce that rather than relying on it:
  - the handler path is re-resolved and re-checked against scripts/ here,
    at run time, instead of trusting that config validation ran
  - the command is built as an argument list, never a shell string, so
    there is no shell to inject into (shell=False is subprocess's default
    and is left that way deliberately)
  - a non-MATCHED bundle raises instead of quietly doing nothing, since
    "the executor silently ignored that" is a failure mode that hides

Not wired into listener/vad_listener.py. The permission boundary, what
EKKO is allowed to do on this machine at all, is still an open decision
in readme.md, and connecting the mic to this function is the moment that
decision takes effect. Doing it needs to be a deliberate act, not a side
effect of this file existing.

Usage:
    python routing/execute.py "open task manager"
    python routing/execute.py --dry-run "launch vs code"
"""

import argparse
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

try:
    from .bundle import IntentBundle, RoutingStatus
    from .config import HANDLER_ROOT, DEFAULT_CONFIG_PATH
    from .matcher import DEFAULT_THRESHOLD
    from .route import Router
except ImportError:
    from bundle import IntentBundle, RoutingStatus
    from config import HANDLER_ROOT, DEFAULT_CONFIG_PATH
    from matcher import DEFAULT_THRESHOLD
    from route import Router

# Handlers are PowerShell because the execution environment is native
# Windows (see scripts/README.md). -NoProfile keeps a user profile from
# changing how a handler behaves; -ExecutionPolicy Bypass is needed
# because these scripts are unsigned local files.
POWERSHELL = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]

# Handler exit code convention, see scripts/README.md.
EXIT_OK = 0
EXIT_ALREADY_DONE = 3

# A handler opts into having its own dynamic text spoken, instead of the
# fixed RESPONSES confirmation, by ending its stdout with this prefix on a
# line of its own. See spoken_override() below and
# scripts/system_diagnosis.ps1, the one handler that currently uses it.
SPEAK_PREFIX = "SPEAK: "

# Keys in feedback.speech.RESPONSES. Mapped here rather than in
# feedback/speech.py so that module keeps having no dependencies of its
# own, the existing listener -> feedback direction stays one-way.
RESPONSE_KEYS = {
    RoutingStatus.NO_MATCH: "not_understood",
    RoutingStatus.MISSING_SLOT: "missing_slot",
    # Nothing was said, so there's nothing to answer. The caller should
    # stay quiet rather than speak; that's why this maps to None.
    RoutingStatus.EMPTY_TRANSCRIPT: None,
}


@dataclass(frozen=True)
class ExecutionResult:
    handler: str
    exit_code: int
    stdout: str
    stderr: str
    # Which feedback.speech.RESPONSES key the response layer should speak,
    # or None to stay silent.
    response_key: str | None

    @property
    def succeeded(self) -> bool:
        return self.exit_code in (EXIT_OK, EXIT_ALREADY_DONE)

    def describe(self) -> str:
        message = (self.stdout or self.stderr).strip().splitlines()
        first_line = message[0] if message else "(no output)"
        return f"exit {self.exit_code}  [{self.response_key}]  {first_line}"


def response_key(bundle: IntentBundle, result: ExecutionResult | None = None) -> str | None:
    """The RESPONSES key for a routing outcome, with or without execution.

    Split out from execute() because the non-matched statuses need an
    answer without anything having run, which is the case that would
    otherwise fall through to silence.
    """
    if bundle.status is not RoutingStatus.MATCHED:
        return RESPONSE_KEYS[bundle.status]
    if result is None:
        return None
    if result.exit_code == EXIT_OK:
        return "command_confirmed"
    if result.exit_code == EXIT_ALREADY_DONE:
        return "already_done"
    return "error"


def spoken_override(result: ExecutionResult) -> str | None:
    """Text a handler asked to have spoken verbatim, opted into via a
    `SPEAK: ...` stdout line, instead of the fixed RESPONSES confirmation
    response_key() would otherwise pick -- e.g. system_diagnosis.ps1
    reporting live numbers computed at run time, which nothing in
    feedback.speech.RESPONSES can hold.

    Scans from the end of stdout for the LAST line starting with
    SPEAK_PREFIX, rather than requiring it be the literal final line.
    That's deliberate: a handler is free to print more after it (e.g.
    system_diagnosis.ps1's Open-ReportWindow logging "Report window:
    opened" once it's done), and the convention shouldn't be "nothing may
    ever print after SPEAK:" when what it actually needs to guarantee is
    "the last SPEAK: line is the one that gets spoken." Multiple SPEAK:
    lines are legal too; only the last one wins, same as intentionally
    printing a corrected value.

    Only consulted on EXIT_OK: already-done/error/no-match keep the fixed
    text, there's nothing dynamic worth saying about those, and a handler
    that failed shouldn't get to put arbitrary text in EKKO's mouth by
    printing a SPEAK line before dying.

    Returns None (falls back to the fixed confirmation) if the handler
    didn't opt in, same bug-tolerant shape as render() falling back to a
    generic key rather than raising.
    """
    if result.exit_code != EXIT_OK:
        return None
    for line in reversed(result.stdout.splitlines()):
        if line.strip().startswith(SPEAK_PREFIX):
            return line.strip()[len(SPEAK_PREFIX):].strip()
    return None


def resolve_handler(bundle: IntentBundle) -> Path:
    """Re-check at run time what config validation already checked at load
    time. Cheap, and it means this function is safe to call with a bundle
    from anywhere, not only one this process routed.
    """
    if bundle.handler is None:
        raise ValueError(f"{bundle.status} bundle has no handler to run")
    path = (HANDLER_ROOT.parent / bundle.handler).resolve()
    try:
        path.relative_to(HANDLER_ROOT.resolve())
    except ValueError:
        raise ValueError(f"handler {bundle.handler!r} resolves outside {HANDLER_ROOT}") from None
    if path.suffix.lower() != ".ps1" or not path.is_file():
        raise ValueError(f"handler {bundle.handler!r} is not an existing .ps1 script")
    return path


def build_command(bundle: IntentBundle) -> list[str]:
    """PowerShell invocation as an argument list.

    Slots become named parameters (-App chrome), matching how
    scripts/open_app.ps1 declares them. Parameter names come from the
    config's slot names and values from its closed vocabulary, so both
    halves of every argument were fixed before anyone spoke.
    """
    command = [*POWERSHELL, str(resolve_handler(bundle))]
    for name, value in bundle.slots.items():
        # -App rather than -app: PowerShell is case-insensitive here, but
        # matching the script's declared casing keeps the two readable
        # side by side.
        command += [f"-{name[:1].upper()}{name[1:]}", value]
    return command


def execute(bundle: IntentBundle, timeout: float = 15.0) -> ExecutionResult:
    """Run a MATCHED bundle's handler. Raises on any other status.

    timeout is a guard against a handler that hangs, not a promise about
    how long the app takes to appear: these scripts call Start-Process and
    return immediately rather than waiting on the program they launched.
    """
    if bundle.status is not RoutingStatus.MATCHED:
        raise ValueError(
            f"execute() takes MATCHED bundles only, got {bundle.status}. "
            "Use response_key() to answer the other statuses."
        )

    command = build_command(bundle)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired:
        # -1 is not a code any handler returns, so it can't collide with
        # the documented convention; it maps to "error" like any other
        # unexpected code.
        exit_code = -1
        stdout, stderr = "", f"handler did not finish within {timeout}s"

    result = ExecutionResult(
        handler=bundle.handler,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        response_key=None,
    )
    return replace(result, response_key=response_key(bundle, result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", help="The transcribed command to route and run")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Route and print the command that would run, without running it.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Cosine similarity below which a transcript is a no-match (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to the intent config (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for a handler before giving up (default: 15.0).",
    )
    args = parser.parse_args()

    router = Router.load(config_path=args.config, threshold=args.threshold)
    routed = router.route(args.transcript)
    print(f"Transcript: {args.transcript!r}")
    print(routed.describe())

    if routed.status is not RoutingStatus.MATCHED:
        print(f"Response:   {response_key(routed)}")
        print("Nothing to run.")
        raise SystemExit(0)

    print(f"Command:    {build_command(routed)}")
    if args.dry_run:
        print("--dry-run, not executing.")
        raise SystemExit(0)

    execution = execute(routed, timeout=args.timeout)
    print(execution.describe())
    raise SystemExit(0 if execution.succeeded else 1)
