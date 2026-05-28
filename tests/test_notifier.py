import json
from unittest.mock import patch, MagicMock, call
from lark_listener.notifier import Notifier, build_summary_text
from lark_listener.fetcher import MessageCategory
from lark_listener.analyzer import AnalysisResult


SAMPLE_MESSAGES = {
    MessageCategory.P2P: [
        {"message_id": "msg_001", "sender": {"name": "张三"}, "body": {"content": "线上挂了"}},
    ],
    MessageCategory.AT_ME: [
        {"message_id": "msg_002", "sender": {"name": "李四"}, "chat_name": "技术群", "body": {"content": "@你 review PR"}},
    ],
    MessageCategory.KEYWORD: [
        {"message_id": "msg_003", "sender": {"name": "王五"}, "chat_name": "运维群", "body": {"content": "部署流水线挂了"}, "matched_keyword": "部署"},
    ],
}

SAMPLE_ANALYSIS = {
    "msg_001": AnalysisResult("msg_001", "high", "urgent", "线上故障求助"),
    "msg_002": AnalysisResult("msg_002", "high", "normal", "请求代码审查"),
    "msg_003": AnalysisResult("msg_003", "high", "normal", "CI/CD 流水线故障"),
}


def test_build_summary_text():
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30")
    assert "私聊消息" in text
    assert "张三" in text
    assert "查看原文" in text
    assert "@我" in text
    assert "关键词命中" in text
    assert "部署" in text


def test_build_summary_empty():
    empty = {cat: [] for cat in MessageCategory}
    text = build_summary_text(empty, {}, "15:00", "15:30")
    assert text == ""


@patch("lark_listener.notifier.subprocess.run")
def test_notify_sends_message_and_notification(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout='{"ok": true}')
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30")

    # Should call subprocess twice: lark-cli send + terminal-notifier
    assert mock_run.call_count == 2


@patch("lark_listener.notifier.subprocess.run")
def test_notify_skips_when_no_messages(mock_run):
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    empty = {cat: [] for cat in MessageCategory}
    notifier.notify(empty, {}, "15:00", "15:30")

    mock_run.assert_not_called()
