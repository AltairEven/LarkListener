import json
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

def _mock_cfg(poll_interval=None):
    """run()/cmd_summarize 系测试共用的最小 mock 配置。"""
    cfg = {"lark_cli_appid": "cli",
           "notify": {"user_id": "ou", "bot_chat_id": "oc"}}
    if poll_interval is not None:
        cfg["poll_interval"] = poll_interval
    return cfg



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
    main_mod._handle_message("汇总", "ou_test", config_path, state_path)
    mock_poll.assert_called_once()


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_config_rejects_non_owner(mock_parse, mock_reply, tmp_path):
    """非 owner 在 intent.parse 之前即被静默忽略（不回复、不烧 AI）。"""
    config_path, state_path = _write_cfg(tmp_path)
    mock_parse.return_value = Intent(type="config_view")
    main_mod._handle_message("当前配置", "ou_stranger", config_path, state_path)
    mock_reply.assert_not_called()
    mock_parse.assert_not_called()


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
    main_mod._handle_message("总结", "ou_test", config_path, state_path, "om_1")
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
    config = {"keywords": ["k"], "exclude_chats": [{"chat_id": "oc_x", "name": ""}]}
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


def test_cmd_summarize_start_after_end_errors(capsys):
    assert main_mod.cmd_summarize(2000, 1000) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == 1
    assert "必须早于" in out["errorMsg"]
    assert main_mod.cmd_summarize(1000, 1000) == 1


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.build_summary_response")
@patch("lark_listener.main._analyze_window", return_value={"oc": "a"})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_default_pushes_feishu_and_json_stdout(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_resp, MockNotifier, capsys):
    mock_cfg.return_value = _mock_cfg()
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    mock_resp.return_value = {
        "code": 0, "errorMsg": "",
        "data": {"period": {"start": "06-09 10:00", "end": "06-09 11:00"},
                 "conversations": [{"category": "p2p", "title": "张三"}]},
    }
    code = main_mod.cmd_summarize(1000, 2000, quiet=False)
    out = json.loads(capsys.readouterr().out)  # stdout must be valid JSON envelope
    assert out["code"] == 0
    assert out["data"]["conversations"][0]["title"] == "张三"
    MockNotifier.return_value.notify.assert_called_once()
    args = mock_fw.call_args.args
    assert args[1] == datetime.fromtimestamp(1000, main_mod.TZ)
    assert args[2] == datetime.fromtimestamp(2000, main_mod.TZ)
    assert code == 0


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.build_summary_response")
@patch("lark_listener.main._analyze_window", return_value={"oc": "a"})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_quiet_skips_feishu(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_resp, MockNotifier, capsys):
    mock_cfg.return_value = _mock_cfg()
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    mock_resp.return_value = {
        "code": 0, "errorMsg": "",
        "data": {"period": {"start": "s", "end": "e"},
                 "conversations": [{"category": "p2p", "title": "张三"}]},
    }
    code = main_mod.cmd_summarize(1000, 2000, quiet=True)
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == 0
    MockNotifier.return_value.notify.assert_not_called()
    assert code == 0


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_empty_outputs_json_and_delegates_push(
        mock_cfg, mock_prof, mock_fw, MockNotifier, capsys):
    """No messages → still a valid empty envelope on stdout. 是否推送由 notify
    统一裁决（空封套 → 卡片 None → 不发），cmd_summarize 不重复判空；
    传入的 resp 必须就是 stdout 那份封套（同源）。"""
    mock_cfg.return_value = _mock_cfg()
    mock_fw.return_value = ({}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000)
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == 0
    assert out["data"]["conversations"] == []
    passed_resp = MockNotifier.return_value.notify.call_args.kwargs["resp"]
    assert passed_resp["data"]["conversations"] == []
    assert code == 0


@patch("lark_listener.main.build_summary_response", side_effect=RuntimeError("脏数据"))
@patch("lark_listener.main._analyze_window", return_value={})
@patch("lark_listener.main._fetch_window")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_cmd_summarize_envelope_build_error_still_json(
        mock_cfg, mock_prof, mock_fw, mock_aw, mock_resp, capsys):
    """封套构建本身抛异常（如 chat_id 为 null 的脏消息）也必须产出错误封套，
    stdout 永远是合法 JSON，不能裸 traceback。"""
    mock_cfg.return_value = _mock_cfg()
    mock_fw.return_value = ({"cat": [{"message_id": "m1"}]}, MagicMock())
    code = main_mod.cmd_summarize(1000, 2000, quiet=True)
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["code"] == 1
    assert "汇总失败" in out["errorMsg"]


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
    mock_cfg.return_value = _mock_cfg()
    code = main_mod.cmd_summarize(1_000_000, 1_717_900_000_000)
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert "时间戳无效" in out["errorMsg"]


# --- poll_interval=0 关闭自动轮询 ---


def test_poll_wait_timeout_bounded_when_zero():
    # 0/负数 = 关自动轮询 → 有界等待（定期醒来 reload 配置，否则 config_cli
    # 从另一进程改写 config.yaml 永远无法被感知）；正数 = 轮询间隔
    assert main_mod._poll_wait_timeout(0) == main_mod.IDLE_RELOAD_SECONDS
    assert main_mod._poll_wait_timeout(-5) == main_mod.IDLE_RELOAD_SECONDS
    assert main_mod._poll_wait_timeout(300) == 300


def test_startup_message_reflects_mode():
    assert "轮询间隔 300 秒" in main_mod._startup_message(300)
    assert "自动轮询已关闭" in main_mod._startup_message(0)


@patch("lark_listener.main._dispatch_trigger")
@patch("lark_listener.main.poll_once")
@patch("lark_listener.main.threading.Thread")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_run_skips_poll_when_interval_zero(mock_cfg, mock_prof, mock_reply,
                                           mock_thread, mock_poll, mock_disp):
    mock_cfg.return_value = _mock_cfg(poll_interval=0)
    main_mod._running = True
    try:
        with patch.object(main_mod._trigger_queue, "get", return_value=None):
            main_mod.run()
    finally:
        main_mod._running = True
    mock_poll.assert_not_called()


@patch("lark_listener.main._dispatch_trigger")
@patch("lark_listener.main.poll_once")
@patch("lark_listener.main.threading.Thread")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_run_polls_when_interval_positive(mock_cfg, mock_prof, mock_reply,
                                          mock_thread, mock_poll, mock_disp):
    mock_cfg.return_value = _mock_cfg(poll_interval=300)
    main_mod._running = True
    try:
        with patch.object(main_mod._trigger_queue, "get", return_value=None):
            main_mod.run()
    finally:
        main_mod._running = True
    mock_poll.assert_called_once()


ZERO_POLL_CONFIG = SAMPLE_CONFIG.replace("poll_interval: 60", "poll_interval: 0")


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_interval_zero_uses_default_lookback(MockFetcher, MockAnalyzer, MockNotifier, tmp_path):
    """interval=0 + 无 last_poll_time：手动「汇总」回溯 30 分钟，
    绝不能用 interval 兜底（回溯 0 秒＝零宽窗口必然为空）。"""
    mock_fetcher = MockFetcher.return_value
    mock_fetcher.fetch.return_value = {cat: [] for cat in MessageCategory}
    config_path = str(tmp_path / "config.yaml")
    state_path = str(tmp_path / "state.json")
    with open(config_path, "w") as f:
        f.write(ZERO_POLL_CONFIG)

    poll_once(config_path, state_path)

    start, end = mock_fetcher.fetch.call_args.args[:2]
    width = (end - start).total_seconds()
    assert width == pytest.approx(main_mod.MANUAL_LOOKBACK_SECONDS, abs=5)


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_interval_zero_caps_stale_window(MockFetcher, MockAnalyzer, MockNotifier, tmp_path):
    """interval=0 + 很旧的 last_poll_time（如刚从轮询模式切过来）：
    窗口封顶 24h，防止一次拉取数周消息爆 AI 成本。"""
    mock_fetcher = MockFetcher.return_value
    mock_fetcher.fetch.return_value = {cat: [] for cat in MessageCategory}
    config_path = str(tmp_path / "config.yaml")
    state_path = str(tmp_path / "state.json")
    with open(config_path, "w") as f:
        f.write(ZERO_POLL_CONFIG)
    stale = (_dt.now(main_mod.TZ) - _td(days=14)).isoformat()
    with open(state_path, "w") as f:
        f.write(json.dumps({"last_poll_time": stale, "processed_message_ids": []}))

    poll_once(config_path, state_path)

    start, end = mock_fetcher.fetch.call_args.args[:2]
    width = (end - start).total_seconds()
    assert width == pytest.approx(main_mod.MANUAL_WINDOW_CAP_SECONDS, abs=5)


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_interval_positive_window_unchanged(MockFetcher, MockAnalyzer, MockNotifier, tmp_path):
    """interval>0 的常规路径行为不变：无 last_poll_time 回溯 interval 秒。"""
    mock_fetcher = MockFetcher.return_value
    mock_fetcher.fetch.return_value = {cat: [] for cat in MessageCategory}
    config_path = str(tmp_path / "config.yaml")
    state_path = str(tmp_path / "state.json")
    with open(config_path, "w") as f:
        f.write(SAMPLE_CONFIG)  # poll_interval: 60

    poll_once(config_path, state_path)

    start, end = mock_fetcher.fetch.call_args.args[:2]
    assert (end - start).total_seconds() == pytest.approx(60, abs=5)

# --- review fixes (2026-06-10 全工程 review 高优先级 4 项) ---


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_notify_failure_still_advances_state(MockFetcher, MockAnalyzer, MockNotifier, mock_reply, tmp_path):
    """notify 抛异常（如封套构建遇脏数据）不得阻断 state 推进——否则
    last_poll_time 冻结，同一条毒消息每轮必炸，汇总永久中断。"""
    mf = MockFetcher.return_value
    mf.fetch.return_value = {
        MessageCategory.P2P: [{"message_id": "m_poison"}],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    mf.fetch_context.return_value = {}
    MockAnalyzer.return_value.analyze.return_value = {}
    MockNotifier.return_value.notify.side_effect = TypeError("chat_id is None")
    config_path, state_path = _write_cfg(tmp_path)

    poll_once(config_path, state_path)  # 必须不抛

    with open(state_path) as f:
        state = json.load(f)
    assert state["last_poll_time"]
    assert "m_poison" in state["processed_message_ids"]


@patch("lark_listener.main._kill_stale_event_subscribers")
@patch("lark_listener.main.time.sleep")
@patch("lark_listener.main.subprocess.Popen")
def test_bot_listener_does_not_pipe_stderr(mock_popen, mock_sleep, mock_kill):
    """stderr 不能开 PIPE 又不读：lark-cli 长驻子进程写满 64KB 管道缓冲后
    会阻塞在 stderr 写入，事件流静默冻结。继承父进程 stderr（None）：
    零管道零死锁，且订阅失败原因经 launchd 落进 stderr.log 可排查。"""
    proc = MagicMock()
    proc.stdout = iter([])
    mock_popen.return_value = proc
    mock_sleep.side_effect = lambda *a: setattr(main_mod, "_running", False)

    main_mod._running = True
    try:
        main_mod._bot_listener()
    finally:
        main_mod._running = True

    import subprocess as _sp
    assert mock_popen.call_args.kwargs.get("stderr") is None


@patch("lark_listener.main.time.sleep")
@patch("lark_listener.main.threading.Thread")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.load_config", side_effect=ValueError("配置缺少必填项: notify"))
def test_run_bad_startup_config_no_crash_loop(mock_cfg, mock_reply, mock_thread, mock_sleep):
    """启动期配置坏掉不得裸崩：launchd KeepAlive + ThrottleInterval=10 会进入
    每 10 秒无限重启循环。必须捕获、慢退（sleep ≥ 60s）后才退出。"""
    main_mod._running = True
    main_mod.run()  # 必须不抛
    # 慢退总时长 ≥ 60s，且必须分片睡（裸 time.sleep(60) 因 PEP 475 对
    # SIGTERM 免疫，stop 会等到 launchd SIGKILL）。
    assert sum(c.args[0] for c in mock_sleep.call_args_list) >= 60
    assert max(c.args[0] for c in mock_sleep.call_args_list) <= 5


@patch("lark_listener.main._add_reaction")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.poll_once")
def test_handle_message_ignores_stranger_before_intent(mock_poll, mock_parse, mock_reply, mock_react, tmp_path):
    """非 owner 消息在 intent.parse 之前就拦截：不烧 AI、不触发汇总、
    不回复、不加表情（防陌生人刷 AI 配额/扰动汇总窗口）。"""
    config_path, state_path = _write_cfg(tmp_path)
    main_mod._handle_message("总结", "ou_stranger", config_path, state_path, "om_x")
    mock_parse.assert_not_called()
    mock_poll.assert_not_called()
    mock_reply.assert_not_called()
    mock_react.assert_not_called()


# --- 二轮 review：守护循环与通知兜底的补全 ---


@patch("lark_listener.main._dispatch_trigger")
@patch("lark_listener.main.poll_once")
@patch("lark_listener.main.threading.Thread")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.set_lark_profile")
@patch("lark_listener.main.load_config")
def test_run_trigger_does_not_restart_poll_cycle(mock_cfg, mock_prof, mock_reply,
                                                 mock_thread, mock_poll, mock_disp):
    """bot 消息处理完回到等待：不得立即多跑一轮 poll_once——否则任何人发
    任意消息（含陌生人闲聊）都能提前触发轮询、扰动调度节奏。"""
    mock_cfg.return_value = _mock_cfg(poll_interval=300)
    main_mod._running = True
    try:
        with patch.object(main_mod._trigger_queue, "get",
                          side_effect=[("hi", "ou_x", "om_1"), None]):
            main_mod.run()
    finally:
        main_mod._running = True
    mock_poll.assert_called_once()
    mock_disp.assert_called_once()


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_notify_failure_alerts_owner(MockFetcher, MockAnalyzer, MockNotifier, mock_reply, tmp_path):
    """notify 失败丢弃该轮汇总时必须给 owner 一条 best-effort 告警——
    否则消息被静默永久丢弃，用户零感知。"""
    mf = MockFetcher.return_value
    mf.fetch.return_value = {
        MessageCategory.P2P: [{"message_id": "m1"}],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    mf.fetch_context.return_value = {}
    MockAnalyzer.return_value.analyze.return_value = {}
    MockNotifier.return_value.notify.side_effect = TypeError("dirty")
    config_path, state_path = _write_cfg(tmp_path)

    poll_once(config_path, state_path)

    assert any("失败" in c.args[1] for c in mock_reply.call_args_list)


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_skips_messages_without_id(MockFetcher, MockAnalyzer, MockNotifier, mock_reply, tmp_path):
    """收集 all_ids 时缺 message_id 的脏消息跳过——硬下标会在 notify 兜底
    之后、state.save 之前抛 KeyError，毒消息循环换个位置复活。"""
    mf = MockFetcher.return_value
    mf.fetch.return_value = {
        MessageCategory.P2P: [{"message_id": "m1"}, {"no_id": True}],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    mf.fetch_context.return_value = {}
    MockAnalyzer.return_value.analyze.return_value = {}
    config_path, state_path = _write_cfg(tmp_path)

    poll_once(config_path, state_path)  # 必须不抛

    with open(state_path) as f:
        state = json.load(f)
    assert state["processed_message_ids"] == ["m1"]


@patch("lark_listener.main.subprocess.run")
def test_kill_stale_event_subscribers_scoped_to_profile(mock_run):
    """pkill 模式必须同时带 subscribe 限定词与本实例 profile：丢掉 subscribe
    会误杀同 profile 的 `lark-cli event consume`（其它 agent 会话的真实用法）；
    丢掉 profile 会误杀 dev/prod 对方实例。"""
    import lark_listener.binaries as binaries
    orig = binaries._lark_profile
    binaries.set_lark_profile("cli_mine")
    try:
        main_mod._kill_stale_event_subscribers()
    finally:
        binaries.set_lark_profile(orig)
    pattern = mock_run.call_args.args[0][2]
    assert "subscribe" in pattern
    assert "cli_mine" in pattern


@patch("lark_listener.main.subprocess.run")
def test_kill_stale_event_subscribers_skips_without_profile(mock_run):
    """profile 未知时宁可不杀（孤儿会在下次带 profile 启动时清理），
    也不能用全局模式误杀无关进程。"""
    import lark_listener.binaries as binaries
    orig = binaries._lark_profile
    binaries.set_lark_profile(None)
    try:
        main_mod._kill_stale_event_subscribers()
    finally:
        binaries.set_lark_profile(orig)
    mock_run.assert_not_called()


# --- Task 7: registry 接线 ---

from lark_listener.common import TZ


class _FakeRegistry:
    def __init__(self, special_enabled=False):
        self.special_enabled = special_enabled
    def refresh(self):
        return False
    def classify(self, chat_id, chat_type):
        from lark_listener.chats import ChatClass
        return ChatClass.MUTED if chat_type == "group" else ChatClass.NORMAL
    def special_chat_ids(self):
        return []
    def name_of(self, chat_id):
        return ""


@pytest.fixture(autouse=True)
def _stub_chat_registry(monkeypatch):
    """poll_once/_fetch_window 不真发 chat-list；每个用例重置全局 registry。"""
    monkeypatch.setattr(main_mod, "_chat_registry", None, raising=False)
    monkeypatch.setattr(main_mod, "ChatRegistry", _FakeRegistry, raising=False)
    yield
    main_mod._chat_registry = None


def test_fetch_window_builds_registry_and_fetcher(tmp_path, monkeypatch):
    """_fetch_window：建 registry、refresh、按 special_focus 配置组装 Fetcher。"""
    captured = {}

    class _SpyFetcher:
        def __init__(self, keywords=None, registry=None, special_max_messages=20):
            captured["keywords"] = keywords
            captured["registry"] = registry
            captured["special_max_messages"] = special_max_messages
        def fetch(self, start, end, processed_ids, exclude_chat_ids=None):
            captured["exclude"] = exclude_chat_ids
            return {cat: [] for cat in MessageCategory}

    monkeypatch.setattr(main_mod, "Fetcher", _SpyFetcher)
    config = {
        "keywords": ["SDK"],
        "exclude_chats": [{"chat_id": "oc_bot", "name": ""}],
        "special_focus": {"enabled": True, "max_messages": 5, "chats": []},
    }
    start = datetime(2026, 6, 11, 10, 0, tzinfo=TZ)
    end = datetime(2026, 6, 11, 11, 0, tzinfo=TZ)
    main_mod._fetch_window(config, start, end, set())
    assert isinstance(captured["registry"], _FakeRegistry)
    assert captured["registry"].special_enabled is True
    assert captured["special_max_messages"] == 5
    assert captured["exclude"] == {"oc_bot"}


def test_analyze_window_passes_special_chats(monkeypatch):
    """_analyze_window：把「出现在本轮且属特别关注」的会话与绑定词传给 analyzer。"""
    captured = {}

    class _SpyAnalyzer:
        def __init__(self, **kwargs):
            pass
        def analyze(self, categorized, my_user_id="", context=None, special_chats=None):
            captured["special_chats"] = special_chats
            return {}

    monkeypatch.setattr(main_mod, "Analyzer", _SpyAnalyzer)

    class _Reg(_FakeRegistry):
        def special_chat_ids(self):
            return ["oc_vip", "oc_quiet_this_round"]

    class _F:
        registry = _Reg()
        def fetch_context(self, *a, **k):
            return {}

    config = {
        "context_messages": 0,
        "ai": {"provider": "claude", "model": "m"},
        "special_focus": {"enabled": True, "max_messages": 20,
                          "chats": [{"chat_id": "oc_vip", "name": "", "keywords": ["扩容"]}]},
    }
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.SPECIAL] = [{"message_id": "m1", "chat_id": "oc_vip"}]
    start = datetime(2026, 6, 11, 10, 0, tzinfo=TZ)
    end = datetime(2026, 6, 11, 11, 0, tzinfo=TZ)
    main_mod._analyze_window(config, _F(), categorized, start, end, "ou_me")
    # 只包含本轮出现的特别关注会话；绑定词跟随
    assert captured["special_chats"] == {"oc_vip": ["扩容"]}


@patch("lark_listener.main.time.sleep")
@patch("lark_listener.main.threading.Thread")
@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.load_config", side_effect=ValueError("bad"))
def test_run_startup_backoff_exits_early_on_stop(mock_cfg, mock_reply, mock_thread, mock_sleep):
    """分片睡的意义：stop（SIGTERM 置 _running=False）后 1 秒内退出，
    不等满 60 秒。"""
    def _stop_after_three(_secs):
        if mock_sleep.call_count >= 3:
            main_mod._running = False
    mock_sleep.side_effect = _stop_after_three
    main_mod._running = True
    try:
        main_mod.run()
    finally:
        main_mod._running = True
    assert mock_sleep.call_count <= 5


def test_autofill_skipped_for_non_registry(monkeypatch, tmp_path):
    """fetcher.registry 非 ChatRegistry 实例（mock/None）时跳过补名，
    单测/降级路径不碰配置文件。"""
    p = tmp_path / "config.yaml"
    p.write_text("exclude_chats: []\n", encoding="utf-8")
    class _F:
        registry = object()
    main_mod._autofill_config_names(str(p), _F())   # 不抛、不改文件
    assert p.read_text(encoding="utf-8") == "exclude_chats: []\n"
