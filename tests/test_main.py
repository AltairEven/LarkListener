import os
import tempfile
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from lark_listener.main import poll_once
from lark_listener.fetcher import MessageCategory


SAMPLE_CONFIG = """\
poll_interval: 60
lark_cli_appid: cli_test
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


# --- _reply_bot best-effort (review blind spot, CLAUDE.md #6) ---


@patch("lark_listener.main.subprocess.run")
def test_reply_bot_swallows_errors(mock_run):
    mock_run.side_effect = Exception("boom")
    main_mod._reply_bot("ou_x", "hello")  # 必须不抛


# --- reaction only on actionable messages (review #6) ---


@patch("lark_listener.main._add_reaction")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.poll_once")
def test_handle_message_reacts_on_summary(mock_poll, mock_parse, mock_reply, mock_react, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="summary", start_time=None)
    main_mod._handle_message("总结", "ou_anyone", config_path, state_path, "om_1")
    mock_react.assert_called_once_with("om_1")


@patch("lark_listener.main._add_reaction")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.config_editor.render_config", return_value="CFG")
def test_handle_message_reacts_on_owner_config_op(mock_render, mock_parse, mock_reply, mock_react, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="config_view")
    main_mod._handle_message("当前配置", "ou_test", config_path, state_path, "om_2")
    mock_react.assert_called_once_with("om_2")


@patch("lark_listener.main._add_reaction")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_handle_message_no_reaction_for_non_owner(mock_parse, mock_reply, mock_react, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="config_view")
    main_mod._handle_message("当前配置", "ou_stranger", config_path, state_path, "om_3")
    mock_react.assert_not_called()


@patch("lark_listener.main._add_reaction")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_handle_message_no_reaction_for_none(mock_parse, mock_reply, mock_react, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="none")
    main_mod._handle_message("你好", "ou_test", config_path, state_path, "om_4")
    mock_react.assert_not_called()


# --- _bot_listener subprocess lifecycle (review #3, blind spot) ---


@patch("lark_listener.main._kill_stale_event_subscribers")
@patch("lark_listener.main.time.sleep")
@patch("lark_listener.main.subprocess.Popen")
def test_bot_listener_terminates_proc_when_stdout_raises(mock_popen, mock_sleep, mock_kill):
    """If stdout iteration raises, the event subprocess must still be terminated
    (else each reconnect leaks an orphan node/Go subscriber)."""
    proc = MagicMock()
    proc.stdout.__iter__.side_effect = RuntimeError("boom")
    mock_popen.return_value = proc
    mock_sleep.side_effect = lambda *a: setattr(main_mod, "_running", False)

    main_mod._running = True
    try:
        main_mod._bot_listener()
    finally:
        main_mod._running = True
    proc.terminate.assert_called_once()


@patch("lark_listener.main._kill_stale_event_subscribers")
@patch("lark_listener.main.time.sleep")
@patch("lark_listener.main.subprocess.Popen")
def test_bot_listener_enqueues_message_with_id(mock_popen, mock_sleep, mock_kill):
    # drain queue first
    while not main_mod._trigger_queue.empty():
        main_mod._trigger_queue.get_nowait()

    line = _json2.dumps({
        "event": {
            "message": {"message_id": "om_1", "content": _json2.dumps({"text": "总结"})},
            "sender": {"sender_id": {"open_id": "ou_x"}},
        }
    }) + "\n"
    proc = MagicMock()
    proc.stdout = iter([line])
    mock_popen.return_value = proc
    mock_sleep.side_effect = lambda *a: setattr(main_mod, "_running", False)

    main_mod._running = True
    try:
        main_mod._bot_listener()
    finally:
        main_mod._running = True

    item = main_mod._trigger_queue.get_nowait()
    assert item == ("总结", "ou_x", "om_1")


# --- run() helpers extracted for testability (review blind spot) ---


@patch("lark_listener.main._reply_bot")
def test_note_poll_error_alerts_at_threshold_then_resets(mock_reply):
    # 前两次不告警，第三次（达到 MAX_ERRORS=3）告警并归零，便于持续故障周期性再提醒。
    c = main_mod._note_poll_error(0, "ou_x")
    assert c == 1 and mock_reply.call_count == 0
    c = main_mod._note_poll_error(c, "ou_x")
    assert c == 2 and mock_reply.call_count == 0
    c = main_mod._note_poll_error(c, "ou_x")
    assert c == 0 and mock_reply.call_count == 1
    assert "连续出错" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main._handle_message")
def test_dispatch_trigger_swallows_handler_error(mock_handle, mock_reply, tmp_path):
    config_path, state_path = _write_cfg(tmp_path)
    mock_handle.side_effect = Exception("kaboom")
    # 必须不抛，并向用户回报出错
    main_mod._dispatch_trigger(("总结", "ou_x", "om_1"), config_path, state_path, "ou_owner")
    assert "出错" in mock_reply.call_args.args[1]


import sys as _sys


@patch("lark_listener.main.run")
def test_main_run_subcommand_invokes_run(mock_run, monkeypatch):
    monkeypatch.setattr(_sys, "argv", ["lark-listener", "run"])
    main_mod.main()
    mock_run.assert_called_once()


@patch("lark_listener.main.run")
def test_main_no_subcommand_does_not_run(mock_run, monkeypatch):
    monkeypatch.setattr(_sys, "argv", ["lark-listener"])
    main_mod.main()
    mock_run.assert_not_called()


@patch("lark_listener.service.cmd_start", return_value=0)
def test_main_start_subcommand_dispatches_to_service(mock_start, monkeypatch):
    monkeypatch.setattr(_sys, "argv", ["lark-listener", "start"])
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0
    mock_start.assert_called_once()


def test_main_status_dispatch_exit_code(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "status", "--json"])
    from lark_listener import service
    monkeypatch.setattr(service, "cmd_status", lambda as_json=False: 3 if as_json else 0)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 3


def test_main_config_set_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv",
                        ["lark-listener", "config", "set", "keywords", "上线", "--add"])
    captured = {}
    from lark_listener import config_cli
    def fake_set(key, value, add=False, remove=False, force=False):
        captured.update(key=key, value=value, add=add, remove=remove, force=force)
        return 0
    monkeypatch.setattr(config_cli, "config_set", fake_set)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0
    assert captured == {"key": "keywords", "value": "上线", "add": True,
                        "remove": False, "force": False}


def test_main_doctor_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "doctor", "--deep"])
    from lark_listener import doctor
    seen = {}
    monkeypatch.setattr(doctor, "cmd_doctor",
                        lambda as_json=False, deep=False: seen.update(deep=deep) or 1)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 1 and seen["deep"] is True


def test_main_agent_skills_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "agent-skills", "install"])
    from lark_listener import agent_adapters
    monkeypatch.setattr(agent_adapters, "install_agent_skills", lambda: 0)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0


def test_main_config_get_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv",
                        ["lark-listener", "config", "get", "ai.provider", "--json"])
    captured = {}
    from lark_listener import config_cli
    def fake_get(key=None, as_json=False):
        captured.update(key=key, as_json=as_json)
        return 0
    monkeypatch.setattr(config_cli, "config_get", fake_get)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0
    assert captured == {"key": "ai.provider", "as_json": True}


def test_main_agent_skills_uninstall_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "agent-skills", "uninstall"])
    from lark_listener import agent_adapters
    seen = {}
    def fake_uninstall():
        seen["u"] = True
        return 0
    monkeypatch.setattr(agent_adapters, "uninstall_agent_skills", fake_uninstall)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0 and seen["u"] is True


@patch("lark_listener.main.Fetcher")
def test_fetch_window_returns_categorized_and_fetcher(MockFetcher):
    fetcher = MockFetcher.return_value
    fetcher.fetch.return_value = {"cat": [{"message_id": "m1"}]}
    config = {"keywords": ["k"], "include_at_all": True, "exclude_chat_ids": ["oc_x"]}
    start = datetime(2026, 6, 9, 10, 0, tzinfo=main_mod.TZ)
    end = datetime(2026, 6, 9, 11, 0, tzinfo=main_mod.TZ)
    categorized, returned = main_mod._fetch_window(config, start, end, set())
    assert categorized == {"cat": [{"message_id": "m1"}]}
    assert returned is fetcher
    fetcher.fetch.assert_called_once_with(
        start, end, processed_ids=set(), exclude_chat_ids={"oc_x"})


@patch("lark_listener.main.Analyzer")
def test_analyze_window_returns_analysis(MockAnalyzer):
    fetcher = MagicMock()
    MockAnalyzer.return_value.analyze.return_value = {"oc": "analysis"}
    config = {"context_messages": 0, "keywords": [],
              "ai": {"provider": "claude", "model": "m", "api_key": "k", "base_url": ""}}
    start = datetime(2026, 6, 9, 10, 0, tzinfo=main_mod.TZ)
    end = datetime(2026, 6, 9, 11, 0, tzinfo=main_mod.TZ)
    analysis = main_mod._analyze_window(
        config, fetcher, {"cat": [{"message_id": "m1"}]}, start, end, "ou")
    assert analysis == {"oc": "analysis"}
    MockAnalyzer.return_value.analyze.assert_called_once()
    fetcher.fetch_context.assert_not_called()


def test_cmd_summarize_start_after_end_errors():
    assert main_mod.cmd_summarize(2000, 1000) == 1
    assert main_mod.cmd_summarize(1000, 1000) == 1


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.build_summary_text", return_value="汇总ABC")
@patch("lark_listener.main._analyze_window", return_value={"oc": "a"})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_default_pushes_feishu_and_stdout(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_text, MockNotifier, capsys):
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000, quiet=False)
    out = capsys.readouterr().out
    assert "汇总ABC" in out
    MockNotifier.return_value.notify.assert_called_once()
    args = mock_fw.call_args.args
    assert args[1] == datetime.fromtimestamp(1000, main_mod.TZ)
    assert args[2] == datetime.fromtimestamp(2000, main_mod.TZ)
    assert code == 0


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.build_summary_text", return_value="汇总ABC")
@patch("lark_listener.main._analyze_window", return_value={"oc": "a"})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_quiet_skips_feishu(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_text, MockNotifier, capsys):
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000, quiet=True)
    assert "汇总ABC" in capsys.readouterr().out
    MockNotifier.return_value.notify.assert_not_called()
    assert code == 0


@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_no_messages(mock_cfg, mock_prof, mock_fw, capsys):
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    mock_fw.return_value = ({}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000)
    assert "没有新消息" in capsys.readouterr().out
    assert code == 0


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.build_summary_text", return_value="")
@patch("lark_listener.main._analyze_window", return_value={"oc": "a"})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_empty_text_no_push(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_text, MockNotifier, capsys):
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000, quiet=False)
    assert "没有可汇总的内容" in capsys.readouterr().out
    MockNotifier.return_value.notify.assert_not_called()
    assert code == 0


def test_main_summarize_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv",
                        ["lark-listener", "summarize", "--start", "1000", "--end", "2000", "--quiet"])
    seen = {}
    monkeypatch.setattr(main_mod, "cmd_summarize",
                        lambda start_ts, end_ts, quiet=False: seen.update(
                            start=start_ts, end=end_ts, quiet=quiet) or 0)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0
    assert seen == {"start": 1000, "end": 2000, "quiet": True}


def test_main_summarize_requires_start_end(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "summarize"])
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 2  # argparse usage error: missing required --start/--end


@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_out_of_range_timestamp(mock_cfg, mock_prof, capsys):
    # AI agent 误传毫秒时间戳 → 应友好报错而非 traceback
    mock_cfg.return_value = {"lark_cli_appid": "cli",
                             "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    code = main_mod.cmd_summarize(1_000_000, 1_717_900_000_000)
    assert code == 1
    assert "时间戳无效" in capsys.readouterr().out
