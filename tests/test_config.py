import pytest
from lark_listener.config import load_config


SAMPLE_CONFIG = """\
poll_interval: 120
lark_cli_appid: cli_test
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


def test_load_config_missing_bot_chat_id_raises(tmp_path):
    """Missing notify.bot_chat_id must fail at load, not later with a KeyError in
    poll_once after messages were fetched/analyzed (which would freeze state)."""
    bad = """\
lark_cli_appid: cli_test
ai:
  provider: claude
  model: claude-sonnet-4-6
notify:
  user_id: ou_test
"""
    config_file = tmp_path / "bad.yaml"
    config_file.write_text(bad)
    with pytest.raises(ValueError, match="bot_chat_id"):
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


def test_load_config_missing_lark_cli_appid_raises(tmp_path):
    """lark_cli_appid is required: it pins the service to a specific bot."""
    bad = """\
ai:
  provider: claude
  model: claude-sonnet-4-6
notify:
  user_id: ou_test
  bot_chat_id: oc_test
"""
    config_file = tmp_path / "bad3.yaml"
    config_file.write_text(bad)
    with pytest.raises(ValueError, match="lark_cli_appid"):
        load_config(str(config_file))


def test_load_config_defaults(tmp_path):
    """Config with only required fields gets defaults for optional ones."""
    minimal = """\
lark_cli_appid: cli_test
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
lark_cli_appid: cli_test
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


@pytest.mark.parametrize("raw, expected", [
    ("poll_interval: 0", 0),          # 0 = 关闭自动轮询，合法
    ("poll_interval:", 300),          # null → 回退默认
    ('poll_interval: "120"', 120),    # 带引号的数字 → 容错转 int
    ("poll_interval: abc", 300),      # 垃圾 → 回退默认
    ("poll_interval: -300", 0),       # 负数 → 按 0（关闭）处理
    ("poll_interval: true", 300),     # bool → 非法，回退默认
])
def test_load_config_clamps_poll_interval(tmp_path, raw, expected):
    """poll_interval 在 load_config 咽喉钳制为非负 int：run 循环/_poll_wait_timeout/
    doctor/poll_once 都对它做数值比较，坏配置绝不能让服务 TypeError 崩进
    launchd KeepAlive 重启循环。"""
    content = SAMPLE_CONFIG.replace("poll_interval: 120", raw)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(content)
    config = load_config(str(config_file))
    assert config["poll_interval"] == expected
    assert isinstance(config["poll_interval"], int)
