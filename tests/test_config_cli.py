import json
import pytest

from lark_listener import config_cli

FULL = {
    "poll_interval": 300, "keywords": ["上线"],
    "ai": {"provider": "claude", "model": "m", "api_key": "secret", "base_url": ""},
    "notify": {"user_id": "ou_x", "bot_chat_id": "oc_y"},
    "lark_cli_appid": "cli_x",
}


def test_config_get_masks_api_key(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get(as_json=True, path="/tmp/x.yaml")
    data = json.loads(capsys.readouterr().out)
    assert data["ai"]["api_key"] == "***"
    assert data["ai"]["model"] == "m"
    assert code == 0


def test_config_get_dotted_key(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get("ai.provider", as_json=True, path="/tmp/x.yaml")
    assert json.loads(capsys.readouterr().out) == "claude"
    assert code == 0


def test_config_get_unknown_key(monkeypatch):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    assert config_cli.config_get("ai.nope", path="/tmp/x.yaml") == 1


def test_config_get_load_failure(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad")
    monkeypatch.setattr(config_cli.config_mod, "load_config", boom)
    assert config_cli.config_get(path="/tmp/x.yaml") == 1


def test_config_get_yaml_output_masks(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get(path="/tmp/x.yaml")  # no key, not json → yaml
    out = capsys.readouterr().out
    assert "provider: claude" in out
    assert "***" in out and "secret" not in out
    assert code == 0


def test_config_get_scalar_print(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get("poll_interval", path="/tmp/x.yaml")  # scalar, not json
    assert "300" in capsys.readouterr().out
    assert code == 0


def test_config_path_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LARK_LISTENER_HOME", str(tmp_path))
    assert config_cli._config_path() == tmp_path / "config.yaml"


def test_config_path_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("LARK_LISTENER_HOME", "/somewhere")
    from pathlib import Path
    assert config_cli._config_path("/tmp/explicit.yaml") == Path("/tmp/explicit.yaml")
