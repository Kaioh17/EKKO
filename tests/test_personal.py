"""personal/ extends the shipped config without ever escaping containment."""

import pytest

from conftest import DATA_DIR

PERSONAL = DATA_DIR / "personal"
INTENT = "mine:\n  os: [windows, linux, mac]\n  examples: [\"do my thing\"]\n  handler: my_thing\n"


@pytest.fixture
def personal():
    from routing import config, host

    ext = ".ps1" if host.current_os() == "windows" else ".sh"
    scripts = PERSONAL / "scripts" / host.current_os()
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / f"my_thing{ext}").write_text("echo hi")
    (PERSONAL / "intents.yaml").write_text(INTENT)
    yield config, scripts / f"my_thing{ext}"
    (PERSONAL / "intents.yaml").unlink(missing_ok=True)
    (scripts / f"my_thing{ext}").unlink(missing_ok=True)


def test_personal_intent_and_handler_merge(personal):
    config, script = personal
    keys = {i.key for i in config.load_config().intents}
    assert "mine" in keys and "open_app" in keys
    from routing import host

    assert host.script("my_thing") == script
    assert host.within_handler_roots(script)


def test_personal_hash_changes_with_file(personal):
    config, _ = personal
    before = config.config_hash(config.DEFAULT_CONFIG_PATH)
    (PERSONAL / "intents.yaml").write_text(INTENT + "\n")
    assert config.config_hash(config.DEFAULT_CONFIG_PATH) != before


def test_clash_with_shipped_intent_rejected(personal):
    config, _ = personal
    (PERSONAL / "intents.yaml").write_text(INTENT.replace("mine:", "open_app:"))
    with pytest.raises(config.ConfigError, match="redefines"):
        config.load_config()


def test_traversal_handler_rejected(personal):
    config, _ = personal
    (PERSONAL / "intents.yaml").write_text(INTENT.replace("my_thing", "../evil"))
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_shipped_tree_has_nothing_personal():
    from routing import config

    assert not {"open_ghelper", "start_maison"} & {i.key for i in config.load_config().intents}
