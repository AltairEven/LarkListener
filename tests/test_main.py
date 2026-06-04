import os
import tempfile
from unittest.mock import patch, MagicMock
from lark_listener.main import poll_once
from lark_listener.fetcher import MessageCategory


SAMPLE_CONFIG = """\
poll_interval: 60
keywords:
  - 部署
ai:
  provider: claude
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY
  base_url: ""
notify:
  user_id: ou_test
  bot_chat_id: oc_test
"""


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_full_cycle(MockFetcher, MockAnalyzer, MockNotifier, tmp_path):
    # Setup fetcher mock
    mock_fetcher = MockFetcher.return_value
    mock_fetcher.fetch.return_value = {
        MessageCategory.P2P: [{"message_id": "msg_001", "sender": {"name": "张三"}, "body": {"content": "hello"}}],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
    }

    # Setup analyzer mock
    mock_analyzer = MockAnalyzer.return_value
    mock_analyzer.analyze.return_value = {
        "msg_001": MagicMock(urgency="normal", summary="打招呼"),
    }

    # Setup notifier mock
    mock_notifier = MockNotifier.return_value

    # Create temp config and state
    config_path = str(tmp_path / "config.yaml")
    state_path = str(tmp_path / "state.json")
    with open(config_path, "w") as f:
        f.write(SAMPLE_CONFIG)

    poll_once(config_path, state_path)

    mock_fetcher.fetch.assert_called_once()
    mock_analyzer.analyze.assert_called_once()
    mock_notifier.notify.assert_called_once()


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_no_messages_skips_analysis(MockFetcher, MockAnalyzer, MockNotifier, tmp_path):
    mock_fetcher = MockFetcher.return_value
    mock_fetcher.fetch.return_value = {cat: [] for cat in MessageCategory}

    mock_analyzer = MockAnalyzer.return_value
    mock_notifier = MockNotifier.return_value

    config_path = str(tmp_path / "config.yaml")
    state_path = str(tmp_path / "state.json")
    with open(config_path, "w") as f:
        f.write(SAMPLE_CONFIG)

    poll_once(config_path, state_path)

    mock_analyzer.analyze.assert_not_called()
    mock_notifier.notify.assert_not_called()


# --- _add_reaction tests (组件1) ---

import json as _json2
from lark_listener.main import _add_reaction


@patch("lark_listener.main.subprocess.run")
def test_add_reaction_builds_command(mock_run):
    _add_reaction("om_test123")
    args = mock_run.call_args[0][0]
    assert args[0].endswith("lark-cli")
    assert args[1:4] == ["im", "reactions", "create"]
    assert "--as" in args and "bot" in args
    # message_id 在 --params，Get 在 --data
    params = args[args.index("--params") + 1]
    data = args[args.index("--data") + 1]
    assert _json2.loads(params)["message_id"] == "om_test123"
    assert _json2.loads(data)["reaction_type"]["emoji_type"] == "Get"


@patch("lark_listener.main.subprocess.run")
def test_add_reaction_custom_emoji(mock_run):
    _add_reaction("om_x", emoji_type="OK")
    data = mock_run.call_args[0][0]
    d = data[data.index("--data") + 1]
    assert _json2.loads(d)["reaction_type"]["emoji_type"] == "OK"


@patch("lark_listener.main.subprocess.run")
def test_add_reaction_swallows_errors(mock_run):
    mock_run.side_effect = Exception("boom")
    _add_reaction("om_x")  # 必须不抛


# --- poll_once manual progress notification tests (组件2) ---

from datetime import datetime as _dt, timezone as _tz, timedelta as _td
_TZ8 = _tz(_td(hours=8))


def _write_cfg(tmp_path):
    config_path = str(tmp_path / "config.yaml")
    state_path = str(tmp_path / "state.json")
    with open(config_path, "w") as f:
        f.write(SAMPLE_CONFIG)
    return config_path, state_path


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_manual_sends_progress(MockFetcher, MockAnalyzer, MockNotifier, mock_reply, tmp_path):
    mf = MockFetcher.return_value
    mf.fetch.return_value = {
        MessageCategory.P2P: [{"message_id": "m1"}],
        MessageCategory.AT_ME: [{"message_id": "m2"}],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }
    mf.fetch_context.return_value = {}
    MockAnalyzer.return_value.analyze.return_value = {}
    config_path, state_path = _write_cfg(tmp_path)

    poll_once(config_path, state_path,
              custom_start=_dt(2026, 6, 3, 16, 0, 0, tzinfo=_TZ8), is_manual=True)

    progress = [c.args[1] for c in mock_reply.call_args_list if "找到" in c.args[1]]
    assert len(progress) == 1
    assert "2" in progress[0]              # 2 条相关消息
    assert "约" in progress[0]             # 预估时间
    assert "06-03 16:00" in progress[0]    # 时间范围起点 (custom_start)


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_manual_no_messages_no_progress(MockFetcher, MockAnalyzer, MockNotifier, mock_reply, tmp_path):
    MockFetcher.return_value.fetch.return_value = {cat: [] for cat in MessageCategory}
    config_path, state_path = _write_cfg(tmp_path)

    poll_once(config_path, state_path,
              custom_start=_dt(2026, 6, 3, 16, 0, 0, tzinfo=_TZ8), is_manual=True)

    assert not any("找到" in c.args[1] for c in mock_reply.call_args_list)
    assert any("没有新消息" in c.args[1] for c in mock_reply.call_args_list)


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_auto_no_progress(MockFetcher, MockAnalyzer, MockNotifier, mock_reply, tmp_path):
    mf = MockFetcher.return_value
    mf.fetch.return_value = {
        MessageCategory.P2P: [{"message_id": "m1"}],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    mf.fetch_context.return_value = {}
    MockAnalyzer.return_value.analyze.return_value = {}
    config_path, state_path = _write_cfg(tmp_path)

    poll_once(config_path, state_path)  # is_manual=False

    assert not any("找到" in c.args[1] for c in mock_reply.call_args_list)


# --- _handle_message dispatch ---

import lark_listener.main as main_mod
from lark_listener.intent import Intent
from lark_listener.config_editor import ApplyResult


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.poll_once")
def test_dispatch_summary_calls_poll(mock_poll, mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="summary", start_time=None)
    main_mod._handle_message("汇总", "ou_anyone", config_path, state_path)
    mock_poll.assert_called_once()


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_config_rejects_non_owner(mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="config_view")
    main_mod._handle_message("当前配置", "ou_stranger", config_path, state_path)
    mock_reply.assert_called_once()
    assert "仅本人" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_config_modify_sets_pending(mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    main_mod._pending_change = None
    mock_parse.return_value = Intent(
        type="config_modify",
        changes=[{"field": "poll_interval", "op": "set", "value": 600}])
    main_mod._handle_message("轮询间隔改成10分钟", "ou_test", config_path, state_path)
    assert main_mod._pending_change is not None
    assert "确认" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.config_editor.apply_changes")
def test_dispatch_confirm_applies_pending(mock_apply, mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    main_mod._pending_change = {"changes": [{"field": "poll_interval", "op": "set", "value": 600}], "diff": "x"}
    mock_apply.return_value = ApplyResult(True, diff="poll_interval: 60 → 600")
    mock_parse.return_value = Intent(type="confirm")
    main_mod._handle_message("确认", "ou_test", config_path, state_path)
    mock_apply.assert_called_once()
    assert main_mod._pending_change is None


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_confirm_without_pending(mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    main_mod._pending_change = None
    mock_parse.return_value = Intent(type="confirm")
    main_mod._handle_message("确认", "ou_test", config_path, state_path)
    assert "没有待确认" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.config_editor.render_config", return_value="CFG_TEXT")
def test_dispatch_config_view_replies_config(mock_render, mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="config_view")
    main_mod._handle_message("当前配置", "ou_test", config_path, state_path)
    assert mock_reply.call_args.args[1] == "CFG_TEXT"
    assert mock_reply.call_args.kwargs.get("markdown") is True  # rendered as code block


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.config_editor.render_help", return_value="HELP_TEXT")
def test_dispatch_config_help_replies_help(mock_render, mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="config_help")
    main_mod._handle_message("帮助", "ou_test", config_path, state_path)
    assert mock_reply.call_args.args[1] == "HELP_TEXT"


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.config_editor.compute_diff", return_value=(None, "poll_interval 需为正整数"))
def test_dispatch_config_modify_error_preserves_pending(mock_diff, mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    prior = {"changes": [{"field": "poll_interval", "op": "set", "value": 600}], "diff": "old"}
    main_mod._pending_change = prior
    mock_parse.return_value = Intent(type="config_modify", changes=[{"field": "poll_interval", "op": "set", "value": -1}])
    main_mod._handle_message("轮询间隔改成-1", "ou_test", config_path, state_path)
    assert main_mod._pending_change == prior  # prior valid pending untouched
    assert "正整数" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.config_editor.apply_changes")
def test_dispatch_confirm_apply_failure_replies_error(mock_apply, mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    main_mod._pending_change = {"changes": [{"field": "poll_interval", "op": "set", "value": 600}], "diff": "x"}
    mock_apply.return_value = ApplyResult(False, error="写入失败")
    mock_parse.return_value = Intent(type="confirm")
    main_mod._handle_message("确认", "ou_test", config_path, state_path)
    assert main_mod._pending_change is None
    assert "写入失败" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_cancel_with_pending_clears(mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    main_mod._pending_change = {"changes": [], "diff": "x"}
    mock_parse.return_value = Intent(type="cancel")
    main_mod._handle_message("取消", "ou_test", config_path, state_path)
    assert main_mod._pending_change is None
    assert "已取消" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_error_replies_not_understood(mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="error")
    main_mod._handle_message("???", "ou_test", config_path, state_path)
    assert "帮助" in mock_reply.call_args.args[1]
