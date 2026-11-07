"""Guards against the docs drifting from the code. Each one fails when someone
adds a setting or a log event and forgets to say what it does."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTENER_README = (ROOT / "listener" / "README.md").read_text(encoding="utf-8")


def test_every_general_setting_is_in_the_listener_readme():
    from backend.schemas.settings import GeneralSettings

    missing = [name for name in GeneralSettings.model_fields if f"`{name}`" not in LISTENER_README]
    assert not missing, f"document these in listener/README.md's settings reference: {missing}"


def test_every_segment_log_event_is_documented():
    events = set()
    for path in (ROOT / "listener").glob("*.py"):
        events |= set(re.findall(r'"event": "([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert {"segment", "activation", "overflow", "engine_loaded"} <= events  # the search itself works
    undocumented = sorted(e for e in events if f"`{e}`" not in LISTENER_README)
    assert not undocumented, f"add these to the segment log table in listener/README.md: {undocumented}"


def test_the_cli_flags_the_readme_names_exist():
    import argparse

    source = (ROOT / "listener" / "vad_listener.py").read_text(encoding="utf-8")
    defined = set(re.findall(r'"(--[a-z0-9-]+)"', source))
    named = set(re.findall(r"`(--[a-z0-9-]+)`", LISTENER_README))
    assert named, "the README should mention some flags"
    assert not named - defined, f"README names flags that don't exist: {sorted(named - defined)}"
    assert argparse  # imported only to make the intent obvious: these are argparse flags


def test_docs_no_longer_make_claims_the_code_stopped_backing():
    features = (ROOT / "FEATURES.md").read_text(encoding="utf-8")
    assert "base.en" not in features  # the CPU model is small, or Parakeet
    assert "the Voice page picks" not in features  # the processor is chosen on General & Voice
    readme = (ROOT / "readme.md").read_text(encoding="utf-8")
    # Whisper reaches the GPU only if CUDA libraries are present, which the installer doesn't ship
    assert "already uses the GPU without that" not in readme


def test_help_topics_exist_for_the_stt_guides():
    help_panel = (ROOT / "ekko-ui" / "src" / "components" / "panels" / "HelpPanel.tsx").read_text(encoding="utf-8")
    for topic in ("stt-engine", "stt-gpu", "stt-calibrate", "hold-to-talk"):
        assert f'id: "{topic}"' in help_panel, topic
    audio_panel = (ROOT / "ekko-ui" / "src" / "components" / "panels" / "AudioPanel.tsx").read_text(encoding="utf-8")
    for topic in ("stt-engine", "stt-gpu", "stt-calibrate", "hold-to-talk"):
        linked = f'onHelp("{topic}")' in audio_panel or f'topic="{topic}"' in audio_panel
        assert linked, f"nothing on the Audio page links to the {topic} guide"


def test_no_unmeasured_speed_claims_about_parakeet():
    # Parakeet (0.6B) hasn't been benchmarked against Whisper small on a CPU here.
    # Until it has (see listener/STT_IMPROVEMENTS.md), user-facing text must not say
    # it's faster. Delete this test when the benchmark is in.
    files = [
        "ekko-ui/src/components/panels/AudioPanel.tsx",
        "ekko-ui/src/components/panels/HelpPanel.tsx",
        "listener/README.md",
        "FEATURES.md",
        "readme.md",
        "docs/DESIGN.md",
    ]
    claim = re.compile(r"Parakeet[^.\n]{0,80}\b(faster|fast|quicker|quick)\b|\b(faster|quicker) (choice|option)\b[^.\n]{0,40}processor", re.I)
    for name in files:
        text = (ROOT / name).read_text(encoding="utf-8")
        hit = claim.search(text)
        assert not hit, f"{name}: unmeasured speed claim: {hit.group(0)!r}"
    assert "Runs best on" not in LISTENER_README
