from unittest.mock import patch, MagicMock
from lark_listener.notifier import Notifier, build_summary_text
from lark_listener.fetcher import MessageCategory
from lark_listener.analyzer import ConversationAnalysis


MY_USER_ID = "ou_me"


def _make_msg(message_id, chat_id, sender_id, sender_name, content,
              msg_type="text", chat_name="", create_time="1716796800",
              matched_keyword=""):
    msg = {
        "message_id": message_id,
        "chat_id": chat_id,
        "sender": {"id": sender_id, "name": sender_name},
        "msg_type": msg_type,
        "content": content,
        "create_time": create_time,
    }
    if chat_name:
        msg["chat_name"] = chat_name
    if matched_keyword:
        msg["matched_keyword"] = matched_keyword
    return msg


SAMPLE_MESSAGES = {
    MessageCategory.P2P: [
        _make_msg("msg_001", "oc_p2p1", "ou_zhangsan", "张三", "线上挂了"),
    ],
    MessageCategory.AT_ME: [
        _make_msg("msg_002", "oc_group1", "ou_lisi", "李四", "@你 review PR",
                  chat_name="技术群"),
    ],
    MessageCategory.KEYWORD: [
        _make_msg("msg_003", "oc_group2", "ou_wangwu", "王五", "部署流水线挂了",
                  chat_name="运维群", matched_keyword="部署"),
    ],
    MessageCategory.AT_ALL: [],
}

SAMPLE_ANALYSIS = {
    "oc_p2p1": ConversationAnalysis("oc_p2p1", "high", "urgent", "线上故障求助", "msg_001"),
    "oc_group1": ConversationAnalysis("oc_group1", "high", "normal", "请求代码审查", "msg_002"),
    "oc_group2": ConversationAnalysis("oc_group2", "high", "normal", "CI/CD 流水线故障", "msg_003"),
}


# --- build_summary_text tests ---


def test_build_summary_text_contains_sections():
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "**━━ 私聊消息" in text
    assert "**━━ @我" in text
    assert "**━━ 关键词命中" in text
    assert "查看原文" in text


def test_build_summary_text_contains_names():
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "张三" in text
    assert "技术群" in text
    assert "运维群" in text


def test_build_summary_text_shows_keyword():
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "命中：部署" in text


def test_build_summary_text_shows_time_range():
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "15:00" in text
    assert "15:30" in text


def test_build_summary_text_shows_urgency_icon():
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    # oc_p2p1 is urgent
    assert "🔴" in text


def test_build_summary_text_shows_ai_summary():
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "线上故障求助" in text
    assert "请求代码审查" in text


def test_build_summary_empty():
    empty = {cat: [] for cat in MessageCategory}
    text = build_summary_text(empty, {}, "15:00", "15:30", MY_USER_ID)
    assert text == ""


def test_build_summary_only_self_messages_returns_empty():
    """If all messages are from myself, should return empty."""
    messages = {
        MessageCategory.P2P: [
            _make_msg("msg_self", "oc_self", MY_USER_ID, "我", "自己说的"),
        ],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }
    text = build_summary_text(messages, {}, "15:00", "15:30", MY_USER_ID)
    assert text == ""


def test_build_summary_without_analysis():
    """Should still produce output even without AI analysis."""
    text = build_summary_text(SAMPLE_MESSAGES, {}, "15:00", "15:30", MY_USER_ID)
    assert "私聊消息" in text
    assert "张三" in text
    # No AI line
    assert "💡" not in text


def test_build_summary_truncates_long_content():
    """Content longer than 80 chars should be truncated."""
    long_content = "这是一条非常长的消息" * 20  # well over 80 chars
    messages = {
        MessageCategory.P2P: [
            _make_msg("msg_long", "oc_long", "ou_other", "长文", long_content),
        ],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }
    text = build_summary_text(messages, {}, "15:00", "15:30", MY_USER_ID)
    assert "..." in text


def test_build_summary_card_message_shows_title():
    """Card messages with title should show [卡片] title in the summary display."""
    messages = {
        MessageCategory.AT_ME: [
            _make_msg("msg_card", "oc_card", "ou_bot", "机器人",
                      '<card title="汇率告警">详细内容</card>', msg_type="interactive",
                      chat_name="告警群"),
        ],
        MessageCategory.P2P: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }
    analysis = {
        "oc_card": ConversationAnalysis("oc_card", "high", "urgent", "汇率异常告警", "msg_card"),
    }
    text = build_summary_text(messages, analysis, "15:00", "15:30", MY_USER_ID)
    assert "[卡片] 汇率告警" in text
    assert "汇率异常告警" in text


def test_build_summary_card_without_title_shows_label():
    """Card messages without parseable title should show [卡片消息]."""
    messages = {
        MessageCategory.AT_ME: [
            _make_msg("msg_card", "oc_card", "ou_bot", "机器人",
                      '{"elements":[]}', msg_type="interactive",
                      chat_name="告警群"),
        ],
        MessageCategory.P2P: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }
    analysis = {
        "oc_card": ConversationAnalysis("oc_card", "high", "normal", "通知", "msg_card"),
    }
    text = build_summary_text(messages, analysis, "15:00", "15:30", MY_USER_ID)
    assert "[卡片消息]" in text


def test_build_summary_relevant_message_used():
    """The relevant_message_id from analysis should be used for display."""
    messages = {
        MessageCategory.AT_ME: [
            _make_msg("msg_early", "oc_conv", "ou_a", "A", "无关消息",
                      chat_name="群", create_time="1716796800"),
            _make_msg("msg_important", "oc_conv", "ou_b", "B", "重要内容在这里",
                      chat_name="群", create_time="1716796900"),
        ],
        MessageCategory.P2P: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }
    analysis = {
        "oc_conv": ConversationAnalysis("oc_conv", "high", "normal", "重要讨论", "msg_important"),
    }
    text = build_summary_text(messages, analysis, "15:00", "15:30", MY_USER_ID)
    assert "重要内容在这里" in text


def test_build_summary_urgent_conversations_first():
    """Urgent conversations should appear before normal ones in the same category."""
    messages = {
        MessageCategory.AT_ME: [
            _make_msg("msg_normal", "oc_normal", "ou_a", "A", "普通消息",
                      chat_name="普通群", create_time="1716796800"),
            _make_msg("msg_urgent", "oc_urgent", "ou_b", "B", "紧急消息",
                      chat_name="紧急群", create_time="1716796900"),
        ],
        MessageCategory.P2P: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }
    analysis = {
        "oc_normal": ConversationAnalysis("oc_normal", "medium", "normal", "普通", "msg_normal"),
        "oc_urgent": ConversationAnalysis("oc_urgent", "high", "urgent", "紧急", "msg_urgent"),
    }
    text = build_summary_text(messages, analysis, "15:00", "15:30", MY_USER_ID)
    # Urgent should come before normal
    urgent_pos = text.index("紧急群")
    normal_pos = text.index("普通群")
    assert urgent_pos < normal_pos


def test_build_summary_conversation_count():
    """Section header should show conversation count."""
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "1 个会话" in text


def test_build_summary_sections_separated_by_divider():
    """Sections should be separated by --- divider."""
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "---" in text


def test_build_summary_at_all_section():
    """@所有人 messages should appear in their own section."""
    messages = {
        MessageCategory.P2P: [],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [
            _make_msg("msg_all", "oc_all", "ou_bot", "机器人",
                      "全员通知 @everyone", chat_name="全员群"),
        ],
    }
    analysis = {
        "oc_all": ConversationAnalysis("oc_all", "medium", "normal", "全员通知", "msg_all"),
    }
    text = build_summary_text(messages, analysis, "15:00", "15:30", MY_USER_ID)
    assert "**━━ @所有人" in text
    assert "全员群" in text


def test_build_summary_section_order():
    """Sections should appear in order: 私聊 → @我 → 关键词 → @所有人."""
    messages = {
        MessageCategory.P2P: [
            _make_msg("msg_p", "oc_p", "ou_a", "A", "私聊"),
        ],
        MessageCategory.AT_ME: [
            _make_msg("msg_m", "oc_m", "ou_b", "B", "@你 看看", chat_name="群1"),
        ],
        MessageCategory.KEYWORD: [
            _make_msg("msg_k", "oc_k", "ou_c", "C", "部署完成",
                      chat_name="群2", matched_keyword="部署"),
        ],
        MessageCategory.AT_ALL: [
            _make_msg("msg_a", "oc_a", "ou_d", "D", "通知 @everyone", chat_name="群3"),
        ],
    }
    text = build_summary_text(messages, {}, "15:00", "15:30", MY_USER_ID)
    assert text.index("私聊消息") < text.index("@我") < text.index("关键词命中") < text.index("@所有人")


# --- Notifier tests ---


@patch("lark_listener.notifier.subprocess.run")
def test_notify_sends_message_and_notification(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout='{"ok": true}')
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    # Should call subprocess twice: lark-cli send + terminal-notifier
    assert mock_run.call_count == 2

    # First call: lark-cli im +messages-send
    first_call_args = mock_run.call_args_list[0][0][0]
    assert "lark-cli" in first_call_args
    assert "--markdown" in first_call_args

    # Second call: terminal-notifier
    second_call_args = mock_run.call_args_list[1][0][0]
    assert "terminal-notifier" in second_call_args


@patch("lark_listener.notifier.subprocess.run")
def test_notify_skips_when_no_messages(mock_run):
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    empty = {cat: [] for cat in MessageCategory}
    notifier.notify(empty, {}, "15:00", "15:30")

    mock_run.assert_not_called()


@patch("lark_listener.notifier.subprocess.run")
def test_notify_macos_notification_counts(mock_run):
    """macOS notification should show correct conversation counts."""
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    # terminal-notifier call
    notifier_call = mock_run.call_args_list[1]
    notifier_args = notifier_call[0][0]
    message_idx = notifier_args.index("-message") + 1
    message_text = notifier_args[message_idx]
    assert "1个私聊" in message_text
    assert "1个@我" in message_text
    assert "1个关键词命中" in message_text


@patch("lark_listener.notifier.subprocess.run")
def test_notify_skips_only_self_messages(mock_run):
    """Should not send notification if all messages are from self."""
    messages = {
        MessageCategory.P2P: [
            _make_msg("msg_self", "oc_chat", MY_USER_ID, "我", "自言自语"),
        ],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(messages, {}, "15:00", "15:30", my_user_id=MY_USER_ID)

    mock_run.assert_not_called()
