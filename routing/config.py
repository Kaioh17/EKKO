"""Loads and validates intents.yaml into typed specs.

YAML rather than JSON because this file is hand-edited and grown over
time, and the reasoning behind a phrasing or an alias belongs next to it
as a comment, which JSON can't carry. PyYAML is already an installed
dependency (speechbrain pulls it in via hyperpyyaml), so it costs nothing.

Validation is strict and up front, and it reports every problem it finds
rather than the first: this file is edited by hand, so a typo'd handler
path or a duplicated alias should fail loudly at startup, not silently at
the moment a command actually fires.

Usage:
    python routing/config.py             # validate and summarise the config
    python routing/config.py --config other.yaml
"""

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

try:
    from . import host
except ImportError:
    import host

# Anchored to this file, not cwd, same reasoning as listener/vad_listener.py's
# DEFAULT_SAVE_DIR: these are run as `python routing/route.py` from the repo
# root, where a bare relative path would resolve somewhere else entirely.
_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
DEFAULT_CONFIG_PATH = _PACKAGE_DIR / "intents.yaml"
# Handlers must live under scripts/<os>/ for the current EKKO_OS (see
# routing/host.py, .env) -- not a style rule, a containment one: it's what
# stops a config edit from pointing an intent at an arbitrary script
# somewhere else on disk.
HANDLER_ROOT = host.script_root()

_INTENT_KEYS = {"examples", "handler", "slots", "os"}
# Bare stem only -- no directory separators, no extension. This (not a
# path-containment check alone) is what makes a config-file traversal
# impossible by construction: there's no "../.." or absolute path shape
# this pattern can match in the first place.
_BARE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_HANDLER_NAME_RE_MSG = "must be a bare script name (lowercase letters, digits, underscore only), no path, no extension"
_SLOT_KEYS = {"type", "values", "triggers"}
_SUPPORTED_SLOT_TYPES = {"closed_vocabulary", "free_text", "transcript"}


class ConfigError(ValueError):
    """Raised with every validation problem found, not just the first."""


@dataclass(frozen=True)
class SlotSpec:
    name: str
    # "closed_vocabulary" (the default, everything before free_text
    # existed), "free_text", or "transcript". Determines which of the two
    # fields below is populated and which extraction function in slots.py
    # applies. A "transcript" slot populates neither: its value is the
    # whole utterance, so there is nothing to configure.
    type: str = "closed_vocabulary"
    # closed_vocabulary only: canonical value -> the phrasings that resolve
    # to it, the canonical value itself always included. Sorted
    # longest-first so a longer alias wins over a shorter one it contains
    # ("visual studio code" over "code editor"); slots.py depends on that
    # ordering.
    phrasings: tuple[tuple[str, str], ...] = ()  # (phrasing, canonical value)
    # free_text: phrases stripped from the front of the transcript,
    # whatever follows becomes the slot value. Sorted longest-first for the
    # same reason phrasings is, see slots.py's find_free_text.
    # transcript: optional, and classification-only -- the phrase fires the
    # intent (route.py's _trigger_match) but is NOT stripped, since the
    # slot is the whole utterance regardless.
    triggers: tuple[str, ...] = ()

    @property
    def values(self) -> tuple[str, ...]:
        # dict.fromkeys rather than set(), to keep config order stable in
        # error messages and --summary output. closed_vocabulary only.
        return tuple(dict.fromkeys(canonical for _, canonical in self.phrasings))


@dataclass(frozen=True)
class IntentSpec:
    key: str
    examples: tuple[str, ...]
    # A bare script stem, e.g. "open_app" -- routing/host.py's script()
    # resolves it to scripts/<os>/open_app.{ps1,sh} for whichever EKKO_OS
    # is current. Kept as a string rather than a resolved Path on purpose:
    # execute.resolve_handler() re-resolves and re-checks it at run time
    # instead of trusting a Path built here, and one way of turning a
    # handler into a real path is better than two.
    handler: str
    slots: tuple[SlotSpec, ...] = ()


@dataclass(frozen=True)
class IntentConfig:
    path: Path
    # SHA-256 of the file bytes. The embedding cache stores this so an
    # edit here invalidates it automatically (see matcher.load_index).
    file_hash: str
    intents: tuple[IntentSpec, ...]

    def get(self, key: str) -> IntentSpec | None:
        return next((i for i in self.intents if i.key == key), None)

    def example_pairs(self) -> list[tuple[str, str]]:
        """Every (intent key, example phrasing) pair, in config order.
        This is the exact row order of the embedding matrix.
        """
        return [(intent.key, example) for intent in self.intents for example in intent.examples]


def config_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> IntentConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Intent config not found at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"{path} must be a non-empty mapping of intent key -> definition")

    problems: list[str] = []
    intents: list[IntentSpec] = []
    for key, body in raw.items():
        intent = _parse_intent(str(key), body, problems)
        if intent is not None:
            intents.append(intent)

    if problems:
        raise ConfigError(f"{path} has {len(problems)} problem(s):\n  - " + "\n  - ".join(problems))

    return IntentConfig(path=path, file_hash=config_hash(path), intents=tuple(intents))


def _parse_intent(key: str, body: object, problems: list[str]) -> IntentSpec | None:
    where = f"{key}"
    if not isinstance(body, dict):
        problems.append(f"{where}: must be a mapping, got {type(body).__name__}")
        return None

    unknown = set(body) - _INTENT_KEYS
    if unknown:
        problems.append(f"{where}: unknown key(s) {sorted(unknown)}, expected {sorted(_INTENT_KEYS)}")

    os_list = _parse_os(where, body.get("os"), problems)
    if os_list is not None and host.current_os() not in os_list:
        # Not a config problem -- this intent just doesn't exist under the
        # current EKKO_OS (see open_ghelper/start_maison in intents.yaml).
        # Dropped before examples/handler are even looked at, so it never
        # becomes an embedding row or a MATCHED bundle on this OS.
        return None

    examples = body.get("examples")
    if not isinstance(examples, list) or not examples:
        problems.append(f"{where}.examples: must be a non-empty list of phrasings")
        examples = []
    elif not all(isinstance(e, str) and e.strip() for e in examples):
        problems.append(f"{where}.examples: every entry must be a non-empty string")
        examples = [e for e in examples if isinstance(e, str) and e.strip()]

    handler = body.get("handler")
    if not isinstance(handler, str) or not handler.strip():
        problems.append(f"{where}.handler: must be a bare script name under scripts/{host.current_os()}/")
        handler = ""
    else:
        _validate_handler(where, handler, problems)

    slots = _parse_slots(where, body.get("slots"), problems)

    if not examples or not handler:
        return None
    return IntentSpec(key=key, examples=tuple(examples), handler=handler, slots=slots)


# Valid values for an intent's optional `os:` list -- the OS names
# routing/host.py knows about, not only the ones with a scripts/ tree
# today, so `os: [mac]` fails loudly as "not one of ..." only once mac
# genuinely isn't a name EKKO understands, and as "no such file" (from
# _validate_handler) once it is a name but has no tree yet.
_VALID_OS_NAMES = {"windows", "linux", "mac"}


def _parse_os(where: str, raw: object, problems: list[str]) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        problems.append(f"{where}.os: must be a non-empty list of OS names, got {raw!r}")
        return []
    bad = sorted(set(raw) - _VALID_OS_NAMES)
    if bad:
        problems.append(f"{where}.os: unknown value(s) {bad}, expected {sorted(_VALID_OS_NAMES)}")
    return [v for v in raw if v in _VALID_OS_NAMES]


def _validate_handler(where: str, handler: str, problems: list[str]) -> None:
    if not _BARE_NAME_RE.match(handler):
        problems.append(f"{where}.handler: {handler!r} {_HANDLER_NAME_RE_MSG}")
        return
    resolved = host.script(handler)
    try:
        # Redundant with the bare-name check above by construction (a
        # regex-validated stem joined under HANDLER_ROOT can't escape it),
        # kept anyway as the same defence-in-depth execute.py's own
        # re-check at run time is: one guarantee shouldn't depend on
        # trusting that the other one ran.
        resolved.relative_to(HANDLER_ROOT.resolve())
    except ValueError:
        problems.append(f"{where}.handler: {handler!r} resolves outside {HANDLER_ROOT}")
        return
    if not resolved.is_file():
        problems.append(f"{where}.handler: no such file, {resolved}")


def _parse_slots(where: str, raw: object, problems: list[str]) -> tuple[SlotSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict) or not raw:
        problems.append(f"{where}.slots: must be a non-empty mapping of slot name -> definition")
        return ()

    specs: list[SlotSpec] = []
    for name, body in raw.items():
        slot_where = f"{where}.slots.{name}"
        if not isinstance(body, dict):
            problems.append(f"{slot_where}: must be a mapping, got {type(body).__name__}")
            continue

        unknown = set(body) - _SLOT_KEYS
        if unknown:
            problems.append(f"{slot_where}: unknown key(s) {sorted(unknown)}")

        slot_type = body.get("type")
        if slot_type not in _SUPPORTED_SLOT_TYPES:
            problems.append(
                f"{slot_where}.type: must be one of {sorted(_SUPPORTED_SLOT_TYPES)}, "
                f"got {slot_type!r}."
            )
            continue

        if slot_type == "closed_vocabulary":
            if "triggers" in body:
                problems.append(f"{slot_where}: closed_vocabulary slots take 'values', not 'triggers'")
            values = body.get("values")
            if not isinstance(values, dict) or not values:
                problems.append(f"{slot_where}.values: must be a non-empty mapping of value -> aliases")
                continue
            phrasings = _parse_slot_values(slot_where, values, problems)
            if phrasings:
                specs.append(SlotSpec(name=str(name), type=slot_type, phrasings=phrasings))
        elif slot_type == "transcript":
            # A transcript slot's VALUE never depends on configuration --
            # it's the whole utterance either way -- so a "values" block
            # here would read as though it constrained something and
            # wouldn't.
            if "values" in body:
                problems.append(f"{slot_where}: transcript slots take 'triggers', not 'values'")
            # Triggers are optional here and mean something narrower than
            # they do on a free_text slot: they only decide WHETHER the
            # intent fires (route.py's _trigger_match), never what the slot
            # is filled with. Free of the usual cost, therefore -- a
            # free_text trigger is consumed, eating the verb that followed
            # it, and a transcript slot's isn't.
            raw_triggers = body.get("triggers")
            triggers: tuple[str, ...] = ()
            if raw_triggers is not None:
                if not isinstance(raw_triggers, list) or not raw_triggers:
                    problems.append(f"{slot_where}.triggers: must be a non-empty list of trigger phrases")
                    continue
                triggers = _parse_triggers(slot_where, raw_triggers, problems)
            specs.append(SlotSpec(name=str(name), type=slot_type, triggers=triggers))
        else:  # free_text
            if "values" in body:
                problems.append(f"{slot_where}: free_text slots take 'triggers', not 'values'")
            raw_triggers = body.get("triggers")
            if not isinstance(raw_triggers, list) or not raw_triggers:
                problems.append(f"{slot_where}.triggers: must be a non-empty list of trigger phrases")
                continue
            triggers = _parse_triggers(slot_where, raw_triggers, problems)
            if triggers:
                specs.append(SlotSpec(name=str(name), type=slot_type, triggers=triggers))
    return tuple(specs)


def _parse_triggers(where: str, raw: list, problems: list[str]) -> tuple[str, ...]:
    """Normalise and dedupe a free_text slot's trigger phrases, longest
    first -- same reasoning as _parse_slot_values's phrasings ordering,
    so "search up for" is tried before "search up" and doesn't leave a
    stray "for" stuck to the front of the extracted query.
    """
    seen: dict[str, None] = {}
    for trigger in raw:
        if not isinstance(trigger, str):
            problems.append(f"{where}.triggers: every entry must be a string, got {trigger!r}")
            continue
        normalised = normalise(trigger)
        if not normalised:
            problems.append(f"{where}.triggers: empty trigger phrase")
            continue
        seen.setdefault(normalised, None)
    return tuple(sorted(seen, key=len, reverse=True))


def _parse_slot_values(
    where: str, values: dict, problems: list[str]
) -> tuple[tuple[str, str], ...]:
    seen: dict[str, str] = {}  # normalised phrasing -> canonical value it belongs to
    for canonical, body in values.items():
        canonical = str(canonical)
        aliases: list[str] = []
        if isinstance(body, dict):
            unknown = set(body) - {"aliases"}
            if unknown:
                problems.append(f"{where}.{canonical}: unknown key(s) {sorted(unknown)}")
            raw_aliases = body.get("aliases") or []
            if not isinstance(raw_aliases, list):
                problems.append(f"{where}.{canonical}.aliases: must be a list")
            else:
                aliases = [str(a) for a in raw_aliases]
        elif body is not None:
            problems.append(f"{where}.{canonical}: must be a mapping with an 'aliases' list")

        # The canonical value is matchable in its own right, no need to
        # repeat it under aliases.
        for phrasing in [canonical, *aliases]:
            normalised = normalise(phrasing)
            if not normalised:
                problems.append(f"{where}.{canonical}: empty alias")
                continue
            if normalised in seen and seen[normalised] != canonical:
                # Two values claiming the same words is unresolvable at
                # match time, so it's a config error rather than a
                # tiebreak rule.
                problems.append(
                    f"{where}: {phrasing!r} is claimed by both "
                    f"{seen[normalised]!r} and {canonical!r}"
                )
                continue
            seen[normalised] = canonical

    # Longest first: "visual studio code" must win over any shorter
    # phrasing contained in it. slots.py relies on this ordering.
    return tuple(sorted(seen.items(), key=lambda item: len(item[0]), reverse=True))


def normalise(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    Lives here rather than in slots.py because config validation and slot
    matching must normalise identically, otherwise an alias could pass
    the duplicate check and then never match anything.
    """
    lowered = text.lower()
    kept = [c if (c.isalnum() or c.isspace()) else " " for c in lowered]
    return " ".join("".join(kept).split())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path to the intent config (default: {DEFAULT_CONFIG_PATH}).",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"[config] {exc}")

    total_examples = sum(len(i.examples) for i in config.intents)
    print(f"{config.path}  (sha256 {config.file_hash[:12]})")
    print(f"EKKO_OS={host.current_os()}  handlers under {HANDLER_ROOT}")
    print(f"{len(config.intents)} intents, {total_examples} example phrasings\n")
    for intent in config.intents:
        print(f"  {intent.key}")
        print(f"    handler:  {intent.handler}")
        print(f"    examples: {len(intent.examples)}")
        for slot in intent.slots:
            if slot.type == "closed_vocabulary":
                print(f"    slot {slot.name} (closed_vocabulary): {list(slot.values)}")
            elif slot.type == "transcript":
                print(f"    slot {slot.name} (transcript): the whole utterance")
            else:
                print(f"    slot {slot.name} (free_text), triggers: {list(slot.triggers)}")
    print("\nValid.")
