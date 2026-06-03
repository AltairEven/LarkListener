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


# --- _parse_trigger_with_ai robustness (#6) ---

import json as _json
from lark_listener.main import _parse_trigger_with_ai

_OLLAMA_CONFIG = {"ai": {"provider": "ollama", "model": "x", "api_key": "", "base_url": ""}}


def _mock_ollama(mock_urlopen, content_obj):
    resp = MagicMock()
    resp.read.return_value = _json.dumps(
        {"message": {"content": _json.dumps(content_obj)}}
    ).encode()
    mock_urlopen.return_value.__enter__.return_value = resp


@patch("urllib.request.urlopen")
def test_trigger_invalid_start_time_still_triggers(mock_urlopen):
    """is_trigger=true with an unparseable start_time should keep the trigger,
    falling back to the default time range (start_time=None) instead of dropping it."""
    _mock_ollama(mock_urlopen, {"is_trigger": True, "start_time": "not-a-date"})
    is_trigger, start_time = _parse_trigger_with_ai("汇总最近的消息", _OLLAMA_CONFIG)
    assert is_trigger is True
    assert start_time is None


@patch("urllib.request.urlopen")
def test_trigger_valid_start_time_parsed(mock_urlopen):
    _mock_ollama(mock_urlopen, {"is_trigger": True, "start_time": "2026-06-03T10:00:00+08:00"})
    is_trigger, start_time = _parse_trigger_with_ai("汇总今天上午", _OLLAMA_CONFIG)
    assert is_trigger is True
    assert start_time is not None
    assert start_time.hour == 10


@patch("urllib.request.urlopen")
def test_trigger_not_a_trigger(mock_urlopen):
    _mock_ollama(mock_urlopen, {"is_trigger": False, "start_time": None})
    is_trigger, start_time = _parse_trigger_with_ai("你好", _OLLAMA_CONFIG)
    assert is_trigger is False


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
