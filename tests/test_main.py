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
