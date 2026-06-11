from lark_listener import config_editor
from lark_listener.config_editor import (
    _plan_changes, compute_diff, removes_bot_chat,
    autofill_chat_names, load_roundtrip,
)

SAMPLE = """\
# 轮询间隔
poll_interval: 300
keywords:
  - 部署   # 关注部署
ai:
  provider: claude
notify:
  user_id: ou_test
"""


def test_roundtrip_preserves_comments(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")

    data = config_editor.load_roundtrip(str(p))
    data["poll_interval"] = 600
    config_editor.dump_roundtrip(str(p), data)

    text = p.read_text(encoding="utf-8")
    assert "# 轮询间隔" in text        # 顶部注释保留
    assert "# 关注部署" in text        # 行内注释保留
    assert "poll_interval: 600" in text


def test_dump_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    data = config_editor.load_roundtrip(str(p))
    config_editor.dump_roundtrip(str(p), data)
    assert not (tmp_path / "config.yaml.tmp").exists()
    assert "poll_interval: 300" in p.read_text(encoding="utf-8")


EFFECTIVE = {
    "poll_interval": 300,
    "context_messages": 20,
    "keywords": ["部署", "故障"],
    "exclude_chats": [{"chat_id": "oc_bot", "name": ""}],
    "special_focus": {"enabled": False, "max_messages": 20, "chats": []},
    "lark_cli_appid": "cli_test",
    "ai": {"provider": "claude", "api_key": "secret"},
    "notify": {"user_id": "ou_test", "bot_chat_id": "oc_bot"},
}


def test_protected_field_rejected():
    diff, err = config_editor.compute_diff(
        [{"field": "ai", "op": "set", "value": {}}], EFFECTIVE)
    assert diff is None
    assert "受保护" in err


def test_lark_cli_appid_is_protected():
    """Changing which bot carries the service needs a restart, so the bot must
    not edit lark_cli_appid via chat."""
    diff, err = config_editor.compute_diff(
        [{"field": "lark_cli_appid", "op": "set", "value": "cli_other"}], EFFECTIVE)
    assert diff is None
    assert "受保护" in err


def test_scalar_set_coerces_string_to_int():
    diff, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": "600"}], EFFECTIVE)
    assert err is None
    assert "300" in diff and "600" in diff


def test_poll_interval_accepts_zero():
    # 0 = 关闭自动轮询，应被接受
    diff, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": 0}], EFFECTIVE)
    assert err is None


def test_poll_interval_rejects_negative():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": -1}], EFFECTIVE)
    assert err is not None
    assert "非负" in err


def test_poll_interval_rejects_non_number():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": "abc"}], EFFECTIVE)
    assert "整数" in err


def test_bool_coercion():
    # special_focus.enabled 是嵌套布尔标量，点号路径应被接受
    diff, err = config_editor.compute_diff(
        [{"field": "special_focus.enabled", "op": "set", "value": "true"}], EFFECTIVE)
    assert err is None
    assert "True" in diff


def test_list_add_dedupes():
    diff, err = config_editor.compute_diff(
        [{"field": "keywords", "op": "add", "value": "部署"}], EFFECTIVE)
    assert err is None
    assert diff == ""  # already present → no-op, no diff


def test_list_set_dedupes_preserving_order():
    """`set` must drop duplicates (like add does) so keyword search doesn't run
    the same keyword twice."""
    new_value, err = config_editor._apply_list_op(["部署"], "set", ["上线", "上线", "故障", "上线"])
    assert err is None
    assert new_value == ["上线", "故障"]


def test_list_add_new_keyword_shows_diff():
    diff, err = config_editor.compute_diff(
        [{"field": "keywords", "op": "add", "value": "上线"}], EFFECTIVE)
    assert err is None
    assert "上线" in diff


def test_add_to_empty_list_field():
    """A list field present but empty (e.g. exclude_chats defaulted to [])
    accepts the first add — it must not be treated as an unknown field."""
    effective = {"exclude_chats": [], "ai": {}, "notify": {}}
    diff, err = config_editor.compute_diff(
        [{"field": "exclude_chats", "op": "add", "value": "oc_123"}], effective)
    assert err is None
    assert "oc_123" in diff


def test_scalar_noop_skipped():
    diff, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": 300}], EFFECTIVE)
    assert err is None
    assert diff == ""


def test_list_remove():
    diff, err = config_editor.compute_diff(
        [{"field": "keywords", "op": "remove", "value": "故障"}], EFFECTIVE)
    assert err is None
    assert "故障" not in diff.split("→")[1]


def test_scalar_field_rejects_list_op():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "add", "value": 1}], EFFECTIVE)
    assert "列表" in err


def test_apply_changes_writes_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    result = config_editor.apply_changes(
        str(p), [{"field": "poll_interval", "op": "set", "value": 600}], EFFECTIVE)
    assert result.ok
    assert "poll_interval: 600" in p.read_text(encoding="utf-8")


def test_apply_changes_adds_missing_key(tmp_path):
    # context_messages 不在文件里（靠默认值），set 时应新增
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    result = config_editor.apply_changes(
        str(p), [{"field": "context_messages", "op": "set", "value": 5}], EFFECTIVE)
    assert result.ok
    assert "context_messages: 5" in p.read_text(encoding="utf-8")


def test_list_op_rejects_none_value():
    _, err = config_editor.compute_diff(
        [{"field": "keywords", "op": "add", "value": None}], EFFECTIVE)
    assert err  # must reject, not append the string "None"


def test_int_field_rejects_bool_value():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": True}], EFFECTIVE)
    assert "整数" in err  # bool must not coerce to 1


def test_apply_changes_protected_returns_error(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    result = config_editor.apply_changes(
        str(p), [{"field": "notify", "op": "set", "value": {}}], EFFECTIVE)
    assert not result.ok
    assert "受保护" in result.error


def test_render_config_excludes_protected_yaml_block():
    text = config_editor.render_config(EFFECTIVE)
    assert "```yaml" in text              # wrapped in a code block
    assert "poll_interval: 300" in text   # raw YAML, not Python repr
    # protected blocks (and their secrets) are excluded entirely
    assert "ai:" not in text
    assert "notify:" not in text
    assert "secret" not in text
    assert "ou_test" not in text


def test_render_help_mentions_protected():
    text = config_editor.render_help()
    assert "ai" in text and "notify" in text


# --- 二轮 review：权限保持 / 空文件兜底 / bot 会话保护 ---

import os as _os
import stat as _stat


def test_dump_roundtrip_preserves_mode_600(tmp_path):
    """config.yaml 含明文 api_key：tmp+replace 不得把用户的 chmod 600
    静默重置回 644。"""
    p = tmp_path / "config.yaml"
    p.write_text("poll_interval: 300\n", encoding="utf-8")
    _os.chmod(p, 0o600)
    data = config_editor.load_roundtrip(p)
    data["poll_interval"] = 600
    config_editor.dump_roundtrip(p, data)
    assert _stat.S_IMODE(p.stat().st_mode) == 0o600


def test_dump_roundtrip_new_file_mode_600(tmp_path):
    """新建文件默认 0600（配置可能含 api_key）。"""
    p = tmp_path / "new.yaml"
    config_editor.dump_roundtrip(p, {"a": 1})
    assert _stat.S_IMODE(p.stat().st_mode) == 0o600


def test_apply_changes_empty_file_no_crash(tmp_path):
    """空 config 文件 load_roundtrip 返回 None，apply_changes 不得 TypeError。"""
    p = tmp_path / "config.yaml"
    p.write_text("", encoding="utf-8")
    result = config_editor.apply_changes(str(p), [{"field": "poll_interval", "op": "set", "value": 600}],
                           {"poll_interval": 300})
    assert result.ok


def test_plan_rejects_removing_bot_chat_from_exclude():
    """exclude_chats 里的 bot 会话不可被 bot 指令移除——移除后汇总卡片
    命中关键词会被自身轮询再次捞起，形成自反馈循环。"""
    cfg = {"keywords": [], "exclude_chats": [{"chat_id": "oc_bot", "name": ""}],
           "notify": {"user_id": "ou_x", "bot_chat_id": "oc_bot"}}
    diff, err = config_editor.compute_diff(
        [{"field": "exclude_chats", "op": "remove", "value": "oc_bot"}], cfg)
    assert err and ("bot" in err.lower() or "自反馈" in err)

    diff, err = config_editor.compute_diff(
        [{"field": "exclude_chats", "op": "set", "value": ["oc_other"]}], cfg)
    assert err is not None  # 整体替换掉 bot 会话同样拒绝


# --- Task 8：新结构与点号路径 ---

def test_exclude_chats_add_by_chat_id():
    resolved, err = _plan_changes(
        [{"field": "exclude_chats", "op": "add", "value": "oc_new"}], EFFECTIVE)
    assert err is None
    field, new_value, _ = resolved[0]
    assert {"chat_id": "oc_new", "name": ""} in new_value


def test_exclude_chats_remove_bot_chat_rejected():
    _, err = compute_diff(
        [{"field": "exclude_chats", "op": "remove", "value": "oc_bot"}], EFFECTIVE)
    assert err and "bot 会话不可移除" in err


def test_exclude_chats_remove_other_ok():
    cfg = dict(EFFECTIVE)
    cfg["exclude_chats"] = [{"chat_id": "oc_bot", "name": ""},
                            {"chat_id": "oc_x", "name": "X群"}]
    resolved, err = _plan_changes(
        [{"field": "exclude_chats", "op": "remove", "value": "oc_x"}], cfg)
    assert err is None
    assert resolved[0][1] == [{"chat_id": "oc_bot", "name": ""}]


def test_special_focus_dotted_scalar_set():
    resolved, err = _plan_changes(
        [{"field": "special_focus.enabled", "op": "set", "value": "true"}], EFFECTIVE)
    assert err is None
    assert resolved[0][1] is True


def test_special_focus_dict_field_rejected():
    _, err = compute_diff(
        [{"field": "special_focus", "op": "set", "value": "on"}], EFFECTIVE)
    assert err and "special_focus.enabled" in err


def test_removes_bot_chat_handles_dict_entries():
    cur = [{"chat_id": "oc_bot", "name": ""}]
    assert removes_bot_chat("oc_bot", cur, []) is True
    assert removes_bot_chat("oc_bot", cur, cur) is False
    # 旧形态纯 str 仍兼容
    assert removes_bot_chat("oc_bot", ["oc_bot"], []) is True


# ---------------------------------------------------------------------------
# autofill_chat_names
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_autofill_fills_missing_names(tmp_path):
    path = _write_yaml(tmp_path, (
        "exclude_chats:\n"
        "  - chat_id: oc_a\n"
        "    name: ''\n"
        "  - chat_id: oc_b\n"
        "    name: 已有名\n"
        "special_focus:\n"
        "  enabled: true\n"
        "  chats:\n"
        "    - chat_id: oc_vip\n"
        "      name: ''\n"
        "      keywords: [扩容]\n"
    ))
    changed = autofill_chat_names(path, lambda cid: {"oc_a": "A群", "oc_vip": "VIP群"}.get(cid, ""))
    assert changed is True
    data = load_roundtrip(path)
    assert data["exclude_chats"][0]["name"] == "A群"
    assert data["exclude_chats"][1]["name"] == "已有名"     # 手填不覆盖
    assert data["special_focus"]["chats"][0]["name"] == "VIP群"


def test_autofill_migrates_legacy_exclude_key(tmp_path):
    path = _write_yaml(tmp_path, (
        "# 注释要保留\n"
        "include_at_all: false\n"
        "exclude_chat_ids:\n"
        "  - oc_old\n"
    ))
    changed = autofill_chat_names(path, lambda cid: "")
    assert changed is True
    data = load_roundtrip(path)
    assert "exclude_chat_ids" not in data
    assert "include_at_all" not in data
    assert data["exclude_chats"] == [{"chat_id": "oc_old", "name": ""}]
    # 注释保留（ruamel round-trip）
    assert "注释要保留" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_autofill_noop_returns_false(tmp_path):
    path = _write_yaml(tmp_path, "exclude_chats:\n  - chat_id: oc_a\n    name: A群\n")
    assert autofill_chat_names(path, lambda cid: "新名") is False


def test_autofill_resolver_failure_leaves_empty(tmp_path):
    path = _write_yaml(tmp_path, "exclude_chats:\n  - chat_id: oc_a\n    name: ''\n")
    assert autofill_chat_names(path, lambda cid: "") is False
