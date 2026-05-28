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
