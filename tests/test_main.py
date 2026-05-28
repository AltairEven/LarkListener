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
