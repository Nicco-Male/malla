# New unit tests for configuration loader

from pathlib import Path

from malla.config import AppConfig, _clear_config_cache, load_config


def test_yaml_loading(tmp_path: Path, monkeypatch):
    """Ensure that values from a YAML file are loaded into AppConfig."""

    # Clear any cached config from other imports
    _clear_config_cache()

    # Clear any environment variables that might override the YAML
    monkeypatch.delenv("MALLA_NAME", raising=False)
    monkeypatch.delenv("MALLA_PORT", raising=False)
    monkeypatch.delenv("MALLA_HOME_MARKDOWN", raising=False)

    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("""
name: CustomName
home_markdown: "# Welcome\nThis is **markdown** content."
port: 9999
""")

    cfg = load_config(config_path=yaml_file)

    assert isinstance(cfg, AppConfig)
    assert cfg.name == "CustomName"
    assert "markdown" in cfg.home_markdown
    assert cfg.port == 9999


def test_env_override(monkeypatch):
    """Environment variables with the `MALLA_` prefix override YAML/defaults."""

    # Clear any cached config from other imports
    _clear_config_cache()

    monkeypatch.setenv("MALLA_NAME", "EnvName")
    monkeypatch.setenv("MALLA_DEBUG", "true")
    cfg = load_config(config_path=None)

    assert cfg.name == "EnvName"
    assert cfg.debug is True


def test_get_ignored_node_ids_parsing():
    """Ensure ignored_node_ids parses decimal and hex formats correctly."""
    config = AppConfig(
        ignored_node_ids="1127955948, !433b3dec, 433b3dec, 0x1a2b, invalid"
    )

    ignored_ids = config.get_ignored_node_ids()

    assert 1127955948 in ignored_ids  # decimal
    assert int("433b3dec", 16) in ignored_ids  # hex without prefix
    assert int("1a2b", 16) in ignored_ids  # hex with 0x prefix
    assert len(ignored_ids) == 4
