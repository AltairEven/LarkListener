from lark_listener import config_editor

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
    "include_at_all": True,
    "context_messages": 20,
    "keywords": ["部署", "故障"],
    "lark_cli_appid": "cli_test",
    "ai": {"provider": "claude", "api_key": "secret"},
    "notify": {"user_id": "ou_test"},
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


def test_poll_interval_rejects_non_positive():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": 0}], EFFECTIVE)
    assert "正整数" in err


def test_poll_interval_rejects_non_number():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": "abc"}], EFFECTIVE)
    assert "整数" in err


def test_bool_coercion():
    diff, err = config_editor.compute_diff(
        [{"field": "include_at_all", "op": "set", "value": "false"}], EFFECTIVE)
    assert err is None
    assert "False" in diff


def test_list_add_dedupes():
    diff, err = config_editor.compute_diff(
        [{"field": "keywords", "op": "add", "value": "部署"}], EFFECTIVE)
    assert err is None
    assert diff == ""  # already present → no-op, no diff


def test_list_add_new_keyword_shows_diff():
    diff, err = config_editor.compute_diff(
        [{"field": "keywords", "op": "add", "value": "上线"}], EFFECTIVE)
    assert err is None
    assert "上线" in diff


def test_add_to_empty_list_field():
    """A list field present but empty (e.g. exclude_chat_ids defaulted to [])
    accepts the first add — it must not be treated as an unknown field."""
    effective = {"exclude_chat_ids": [], "ai": {}, "notify": {}}
    diff, err = config_editor.compute_diff(
        [{"field": "exclude_chat_ids", "op": "add", "value": "oc_123"}], effective)
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
