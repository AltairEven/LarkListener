import pytest
import yaml as _yaml
from lark_listener.config import load_config, exclude_chat_id_set


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
    assert config["exclude_chats"] == []


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


# --- 二轮 review：load_config 钳制扩展到 context_messages / 列表字段 ---

BAD_TYPES_CONFIG = """\
poll_interval: 120
lark_cli_appid: cli_test
context_messages: null
keywords: 上线
exclude_chat_ids: null
ai:
  provider: claude
  model: m
  api_key: k
notify:
  user_id: ou_x
  bot_chat_id: oc_y
"""


def test_load_config_clamps_context_messages(tmp_path):
    """context_messages 坏值（null/字符串）回退默认 20，绝不让 main 的
    `context_limit > 0` 比较 TypeError 崩掉每一轮 poll。"""
    p = tmp_path / "config.yaml"
    p.write_text(BAD_TYPES_CONFIG)
    cfg = load_config(str(p))
    assert cfg["context_messages"] == 20

    p.write_text(BAD_TYPES_CONFIG.replace("context_messages: null", "context_messages: abc"))
    assert load_config(str(p))["context_messages"] == 20


def test_load_config_coerces_scalar_keywords_to_list(tmp_path):
    """keywords 写成标量（`keywords: 上线`）应整体视为单个关键词，
    绝不被 fetcher 逐字符迭代成「上」「线」两次搜索。"""
    p = tmp_path / "config.yaml"
    p.write_text(BAD_TYPES_CONFIG)
    cfg = load_config(str(p))
    assert cfg["keywords"] == ["上线"]


def test_load_config_coerces_null_list_fields(tmp_path):
    """`exclude_chat_ids:`（null）旧键兼容迁移为 exclude_chats=[]，绝不让 `set(None)` TypeError。"""
    p = tmp_path / "config.yaml"
    p.write_text(BAD_TYPES_CONFIG)
    cfg = load_config(str(p))
    assert cfg["exclude_chats"] == []
    assert "exclude_chat_ids" not in cfg


def test_load_config_list_items_coerced_str_and_none_dropped(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(BAD_TYPES_CONFIG.replace(
        "keywords: 上线", "keywords:\n  - 部署\n  - null\n  - 123"))
    cfg = load_config(str(p))
    assert cfg["keywords"] == ["部署", "123"]


def test_load_config_negative_context_messages_clamps_to_zero(tmp_path):
    """负数按 0（关闭上下文拉取）处理，与 poll_interval 的负数语义一致，
    而不是回退默认 20（用户写 -1 的意图显然是关掉）。"""
    p = tmp_path / "config.yaml"
    p.write_text(BAD_TYPES_CONFIG.replace("context_messages: null", "context_messages: -1"))
    assert load_config(str(p))["context_messages"] == 0


# --- Task 2: special_focus / exclude_chats 钳制与兼容 ---


def _write_cfg(tmp_path, extra: dict):
    base = {
        "lark_cli_appid": "cli_x",
        "ai": {"provider": "claude", "model": "m"},
        "notify": {"user_id": "ou_me", "bot_chat_id": "oc_bot"},
    }
    base.update(extra)
    p = tmp_path / "config.yaml"
    p.write_text(_yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    return str(p)


def test_special_focus_defaults(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert cfg["special_focus"] == {"enabled": False, "max_messages": 20, "chats": []}


def test_special_focus_clamps_bad_values(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"special_focus": {
        "enabled": "yes",                      # 非 bool → False
        "max_messages": "abc",                 # 非法 → 20
        "chats": [
            {"chat_id": "oc_a", "keywords": "扩容"},   # 标量 keywords → 单元素列表
            {"name": "缺id"},                          # 缺 chat_id → 丢弃
            "oc_bare",                                 # 非 dict → 丢弃
        ],
    }}))
    sf = cfg["special_focus"]
    assert sf["enabled"] is False
    assert sf["max_messages"] == 20
    assert sf["chats"] == [{"chat_id": "oc_a", "name": "", "keywords": ["扩容"]}]


def test_special_focus_non_dict_falls_back(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"special_focus": "on"}))
    assert cfg["special_focus"] == {"enabled": False, "max_messages": 20, "chats": []}


def test_exclude_chats_new_format(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"exclude_chats": [
        {"chat_id": "oc_a", "name": "A群"},
        {"chat_id": "oc_b"},
    ]}))
    assert cfg["exclude_chats"] == [
        {"chat_id": "oc_a", "name": "A群"},
        {"chat_id": "oc_b", "name": ""},
    ]
    assert exclude_chat_id_set(cfg) == {"oc_a", "oc_b"}


def test_exclude_chats_legacy_key_compat(tmp_path):
    """旧键 exclude_chat_ids（纯 id 列表）兼容读取为新结构。"""
    cfg = load_config(_write_cfg(tmp_path, {"exclude_chat_ids": ["oc_old"]}))
    assert cfg["exclude_chats"] == [{"chat_id": "oc_old", "name": ""}]
    assert "exclude_chat_ids" not in cfg


def test_exclude_chats_bad_entries_dropped(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"exclude_chats": [
        "oc_str",            # 纯 str 兼容为 chat_id
        {"name": "缺id"},    # 丢弃
        None,                # 丢弃
        123,                 # 丢弃（非 str 非 dict）
        {"chat_id": True},   # bool 丢弃
    ]}))
    assert cfg["exclude_chats"] == [{"chat_id": "oc_str", "name": ""}]


def test_include_at_all_removed_and_ignored(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"include_at_all": False}))
    assert "include_at_all" not in cfg
