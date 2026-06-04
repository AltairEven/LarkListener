import pytest
from lark_listener.config import load_config


SAMPLE_CONFIG = """\
poll_interval: 120
keywords:
  - 部署
  - 故障
ai:
  provider: claude
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY
  base_url: ""
notify:
  user_id: ou_test123
  bot_chat_id: oc_test456
"""


def test_load_config_from_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(SAMPLE_CONFIG)
    config = load_config(str(config_file))

    assert config["poll_interval"] == 120
    assert config["keywords"] == ["部署", "故障"]
    assert config["ai"]["provider"] == "claude"
    assert config["notify"]["user_id"] == "ou_test123"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_load_config_missing_user_id_raises(tmp_path):
    """Missing notify.user_id should raise a clear ValueError, not a later KeyError."""
    bad = """\
ai:
  provider: claude
  model: claude-sonnet-4-6
notify:
  bot_chat_id: oc_test
"""
    config_file = tmp_path / "bad.yaml"
    config_file.write_text(bad)
    with pytest.raises(ValueError, match="notify.user_id"):
        load_config(str(config_file))


def test_load_config_missing_ai_provider_raises(tmp_path):
    bad = """\
ai:
  model: claude-sonnet-4-6
notify:
  user_id: ou_test
  bot_chat_id: oc_test
"""
    config_file = tmp_path / "bad2.yaml"
    config_file.write_text(bad)
    with pytest.raises(ValueError, match="ai.provider"):
        load_config(str(config_file))


def test_load_config_defaults(tmp_path):
    """Config with only required fields gets defaults for optional ones."""
    minimal = """\
keywords:
  - test
ai:
  provider: claude
  model: claude-sonnet-4-6
  api_key_env: TEST_KEY
notify:
  user_id: ou_test
  bot_chat_id: oc_test
"""
    config_file = tmp_path / "minimal.yaml"
    config_file.write_text(minimal)
    config = load_config(str(config_file))

    assert config["poll_interval"] == 300  # default
    assert config["ai"]["base_url"] == ""  # default


def test_load_config_list_fields_default_empty(tmp_path):
    """Optional list fields absent from the file default to [] so the bot can
    add the first entry (otherwise they'd be rejected as unknown fields)."""
    minimal = """\
ai:
  provider: claude
  model: claude-sonnet-4-6
notify:
  user_id: ou_test
  bot_chat_id: oc_test
"""
    config_file = tmp_path / "minimal.yaml"
    config_file.write_text(minimal)
    config = load_config(str(config_file))

    assert config["keywords"] == []
    assert config["exclude_chat_ids"] == []
