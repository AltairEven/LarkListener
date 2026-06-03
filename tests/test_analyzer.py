import json
from unittest.mock import patch
import pytest
from lark_listener.analyzer import (
    Analyzer,
    ConversationAnalysis,
    format_msg_content,
    _parse_card,
    _extract_json,
)
from lark_listener.fetcher import MessageCategory


# --- _extract_json tests ---


def test_extract_json_plain_array():
    assert _extract_json('[{"a": 1}]') == [{"a": 1}]


def test_extract_json_markdown_fence():
    text = '```json\n[{"conversation_id": "c1"}]\n```'
    assert _extract_json(text) == [{"conversation_id": "c1"}]


def test_extract_json_bare_fence():
    assert _extract_json('```\n{"x": 1}\n```') == {"x": 1}


def test_extract_json_with_surrounding_prose():
    text = '好的，这是结果：\n[{"a": 1}, {"b": 2}]\n希望有帮助'
    assert _extract_json(text) == [{"a": 1}, {"b": 2}]


def test_extract_json_unrecoverable_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("completely not json at all")


# --- format_msg_content tests ---


def test_format_text_message():
    msg = {"msg_type": "text", "content": "hello world"}
    assert format_msg_content(msg) == "hello world"
    assert format_msg_content(msg, for_display=True) == "hello world"


def test_format_image_label():
    msg = {"msg_type": "image", "content": "img_key_xxx"}
    assert format_msg_content(msg) == "[图片]"
    assert format_msg_content(msg, for_display=True) == "[图片]"


def test_format_file_label():
    msg = {"msg_type": "file", "content": "file_key_xxx"}
    assert format_msg_content(msg) == "[文件]"


def test_format_video_label():
    msg = {"msg_type": "video", "content": ""}
    assert format_msg_content(msg) == "[视频]"


def test_format_audio_label():
    msg = {"msg_type": "audio", "content": ""}
    assert format_msg_content(msg) == "[语音]"


def test_format_sticker_label():
    msg = {"msg_type": "sticker", "content": ""}
    assert format_msg_content(msg) == "[表情]"


def test_format_merge_forward_label():
    msg = {"msg_type": "merge_forward", "content": ""}
    assert format_msg_content(msg) == "[合并转发]"


def test_format_card_for_display_with_title():
    """Card with title should show [卡片] title when for_display=True."""
    content = '<card title="汇率告警">详细内容</card>'
    msg = {"msg_type": "interactive", "content": content}
    assert format_msg_content(msg, for_display=True) == "[卡片] 汇率告警"


def test_format_card_for_display_without_title():
    """Card without parseable title should show [卡片消息]."""
    msg = {"msg_type": "interactive", "content": '{"header":{"title":"通知"}}'}
    assert format_msg_content(msg, for_display=True) == "[卡片消息]"


def test_format_card_for_analysis_with_title():
    """Card content for AI should include title prefix and body text."""
    content = '<card title="汇率告警">\n⚠️ 实付/原价比例异常\n币种：NGN\n</card>'
    msg = {"msg_type": "interactive", "content": content}
    result = format_msg_content(msg)
    assert result.startswith("[卡片: 汇率告警]")
    assert "实付/原价比例异常" in result
    assert "币种：NGN" in result


def test_format_card_for_analysis_json_content():
    """Non-XML card content should pass through as-is."""
    card_content = '{"header":{"title":"通知"},"elements":[{"text":"服务异常"}]}'
    msg = {"msg_type": "interactive", "content": card_content}
    assert format_msg_content(msg) == card_content


def test_format_card_xml_for_display():
    """XML-style card detected by content prefix, with title."""
    msg = {"msg_type": "text", "content": '<card title="告警通知">内容</card>'}
    assert format_msg_content(msg, for_display=True) == "[卡片] 告警通知"


def test_format_card_xml_for_analysis():
    content = '<card title="告警通知">CPU 100%</card>'
    msg = {"msg_type": "text", "content": content}
    result = format_msg_content(msg)
    assert "[卡片: 告警通知]" in result
    assert "CPU 100%" in result


def test_format_empty_content():
    msg = {"msg_type": "unknown_type", "content": ""}
    assert format_msg_content(msg) == "[未知消息类型]"


def test_format_missing_content():
    msg = {"msg_type": "unknown_type"}
    assert format_msg_content(msg) == "[未知消息类型]"


# --- _parse_card tests ---


def test_parse_card_with_title_and_body():
    title, body = _parse_card('<card title="汇率告警">\n⚠️ 异常\n详情\n</card>')
    assert title == "汇率告警"
    assert "⚠️ 异常" in body
    assert "详情" in body


def test_parse_card_without_title_match():
    """Non-matching content returns empty title, original as body."""
    title, body = _parse_card('{"header":{"title":"test"}}')
    assert title == ""
    assert body == '{"header":{"title":"test"}}'


def test_parse_card_empty_title():
    title, body = _parse_card('<card title="">some body</card>')
    assert title == ""
    assert body == "some body"


def test_parse_card_multiline_body():
    content = '<card title="告警">\n第一行\n第二行\n第三行\n</card>'
    title, body = _parse_card(content)
    assert title == "告警"
    assert "第一行" in body
    assert "第三行" in body


# --- Analyzer tests ---

SAMPLE_MESSAGES = {
    MessageCategory.P2P: [
        {
            "message_id": "msg_001",
            "chat_id": "oc_chat1",
            "sender": {"id": "ou_zhangsan", "name": "张三"},
            "msg_type": "text",
            "content": "线上服务挂了，能帮忙看看吗",
            "create_time": "1716796800",
        },
    ],
    MessageCategory.AT_ME: [
        {
            "message_id": "msg_002",
            "chat_id": "oc_chat2",
            "chat_name": "技术群",
            "sender": {"id": "ou_lisi", "name": "李四"},
            "msg_type": "text",
            "content": "明天开会改时间了",
            "create_time": "1716796900",
        },
    ],
    MessageCategory.KEYWORD: [],
    MessageCategory.AT_ALL: [],
}

SAMPLE_AI_RESPONSE = [
    {
        "conversation_id": "oc_chat1",
        "relevance": "high",
        "urgency": "urgent",
        "summary": "线上服务故障求助",
        "relevant_message_id": "msg_001",
    },
    {
        "conversation_id": "oc_chat2",
        "relevance": "medium",
        "urgency": "normal",
        "summary": "会议时间变更",
        "relevant_message_id": "msg_002",
    },
]


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_returns_conversation_analysis(mock_call_ai):
    mock_call_ai.return_value = SAMPLE_AI_RESPONSE

    analyzer = Analyzer(
        provider="claude",
        model="claude-sonnet-4-6",
        api_key="test-key",
        base_url="",
        keywords=["故障"],
    )
    results = analyzer.analyze(SAMPLE_MESSAGES, my_user_id="ou_me")

    assert "oc_chat1" in results
    assert isinstance(results["oc_chat1"], ConversationAnalysis)
    assert results["oc_chat1"].urgency == "urgent"
    assert results["oc_chat1"].summary == "线上服务故障求助"
    assert results["oc_chat1"].relevant_message_id == "msg_001"

    assert "oc_chat2" in results
    assert results["oc_chat2"].relevance == "medium"
    assert results["oc_chat2"].summary == "会议时间变更"


def test_analyze_empty_messages():
    analyzer = Analyzer(
        provider="claude",
        model="claude-sonnet-4-6",
        api_key="test-key",
        base_url="",
        keywords=["故障"],
    )
    empty = {cat: [] for cat in MessageCategory}
    results = analyzer.analyze(empty)
    assert results == {}


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_groups_by_chat_id(mock_call_ai):
    """Multiple messages in the same chat should be grouped into one conversation."""
    mock_call_ai.return_value = [
        {
            "conversation_id": "oc_same_chat",
            "relevance": "high",
            "urgency": "normal",
            "summary": "多人讨论部署问题",
            "relevant_message_id": "msg_b",
        },
    ]
    messages = {
        MessageCategory.AT_ME: [
            {
                "message_id": "msg_a",
                "chat_id": "oc_same_chat",
                "chat_name": "运维群",
                "sender": {"id": "ou_a", "name": "A"},
                "msg_type": "text",
                "content": "部署失败了",
                "create_time": "1716796800",
            },
            {
                "message_id": "msg_b",
                "chat_id": "oc_same_chat",
                "chat_name": "运维群",
                "sender": {"id": "ou_b", "name": "B"},
                "msg_type": "text",
                "content": "@你 帮忙看看日志",
                "create_time": "1716796900",
            },
        ],
        MessageCategory.P2P: [],
        MessageCategory.KEYWORD: [],
    }

    analyzer = Analyzer(
        provider="claude", model="m", api_key="k", base_url="", keywords=["部署"],
    )
    results = analyzer.analyze(messages, my_user_id="ou_me")

    # Should produce one conversation, not two
    assert len(results) == 1
    assert "oc_same_chat" in results

    # Verify AI received both messages in the prompt
    prompt = mock_call_ai.call_args[0][0]
    assert "msg_a" in prompt
    assert "msg_b" in prompt


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_marks_self_messages(mock_call_ai):
    """Messages from the user themselves should be marked with [我]."""
    mock_call_ai.return_value = [
        {"conversation_id": "oc_c", "relevance": "low", "urgency": "low", "summary": "s", "relevant_message_id": "msg_other"},
    ]
    messages = {
        MessageCategory.P2P: [
            {
                "message_id": "msg_me",
                "chat_id": "oc_c",
                "sender": {"id": "ou_me", "name": "我"},
                "msg_type": "text",
                "content": "收到",
                "create_time": "1716796800",
            },
            {
                "message_id": "msg_other",
                "chat_id": "oc_c",
                "sender": {"id": "ou_other", "name": "对方"},
                "msg_type": "text",
                "content": "帮个忙",
                "create_time": "1716796700",
            },
        ],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
    }

    analyzer = Analyzer(
        provider="claude", model="m", api_key="k", base_url="", keywords=[],
    )
    analyzer.analyze(messages, my_user_id="ou_me")

    prompt = mock_call_ai.call_args[0][0]
    assert "[我]" in prompt


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_card_content_sent_to_ai(mock_call_ai):
    """Card message full content should be included in the AI prompt."""
    card_content = '<card title="CPU告警">\n⚠️ CPU 100%\n服务器: prod-01\n</card>'
    mock_call_ai.return_value = [
        {"conversation_id": "oc_card", "relevance": "high", "urgency": "urgent", "summary": "CPU告警", "relevant_message_id": "msg_card"},
    ]
    messages = {
        MessageCategory.AT_ME: [
            {
                "message_id": "msg_card",
                "chat_id": "oc_card",
                "chat_name": "告警群",
                "sender": {"id": "ou_bot", "name": "监控机器人"},
                "msg_type": "interactive",
                "content": card_content,
                "create_time": "1716796800",
            },
        ],
        MessageCategory.P2P: [],
        MessageCategory.KEYWORD: [],
    }

    analyzer = Analyzer(
        provider="claude", model="m", api_key="k", base_url="", keywords=["告警"],
    )
    results = analyzer.analyze(messages, my_user_id="ou_me")

    # Card title and body should be in the AI prompt
    prompt = mock_call_ai.call_args[0][0]
    assert "CPU告警" in prompt
    assert "CPU 100%" in prompt
    assert "[卡片消息]" not in prompt

    assert results["oc_card"].summary == "CPU告警"


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_defaults_for_missing_fields(mock_call_ai):
    """Missing fields in AI response should get defaults."""
    mock_call_ai.return_value = [
        {"conversation_id": "oc_x"},  # minimal response
    ]
    messages = {
        MessageCategory.P2P: [
            {
                "message_id": "msg_x",
                "chat_id": "oc_x",
                "sender": {"id": "ou_x", "name": "X"},
                "msg_type": "text",
                "content": "test",
                "create_time": "1716796800",
            },
        ],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
    }
    analyzer = Analyzer(
        provider="claude", model="m", api_key="k", base_url="", keywords=[],
    )
    results = analyzer.analyze(messages)

    assert results["oc_x"].relevance == "medium"
    assert results["oc_x"].urgency == "normal"
    assert results["oc_x"].summary == ""


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_with_context_messages(mock_call_ai):
    """Context messages should appear in AI prompt marked with [上下文]."""
    mock_call_ai.return_value = [
        {"conversation_id": "oc_ctx", "relevance": "high", "urgency": "normal", "summary": "部署讨论", "relevant_message_id": "msg_matched"},
    ]
    messages = {
        MessageCategory.KEYWORD: [
            {
                "message_id": "msg_matched",
                "chat_id": "oc_ctx",
                "chat_name": "运维群",
                "sender": {"id": "ou_a", "name": "A"},
                "msg_type": "text",
                "content": "部署失败了",
                "create_time": "1716796900",
            },
        ],
        MessageCategory.P2P: [],
        MessageCategory.AT_ME: [],
        MessageCategory.AT_ALL: [],
    }
    context = {
        "oc_ctx": [
            {
                "message_id": "msg_before",
                "chat_id": "oc_ctx",
                "sender": {"id": "ou_b", "name": "B"},
                "msg_type": "text",
                "content": "我开始部署了",
                "create_time": "1716796800",
            },
            {
                "message_id": "msg_after",
                "chat_id": "oc_ctx",
                "sender": {"id": "ou_c", "name": "C"},
                "msg_type": "text",
                "content": "日志在哪看",
                "create_time": "1716797000",
            },
        ],
    }

    analyzer = Analyzer(
        provider="claude", model="m", api_key="k", base_url="", keywords=["部署"],
    )
    results = analyzer.analyze(messages, my_user_id="ou_me", context=context)

    prompt = mock_call_ai.call_args[0][0]
    # Context messages should be marked
    assert "[上下文]" in prompt
    assert "我开始部署了" in prompt
    assert "日志在哪看" in prompt
    # Matched message should NOT be marked as context
    assert "部署失败了" in prompt
    # Verify order: before < matched < after
    assert prompt.index("我开始部署了") < prompt.index("部署失败了") < prompt.index("日志在哪看")


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_without_context(mock_call_ai):
    """Analyze should work fine without context (backward compatible)."""
    mock_call_ai.return_value = [
        {"conversation_id": "oc_no_ctx", "relevance": "low", "urgency": "low", "summary": "test", "relevant_message_id": "msg_1"},
    ]
    messages = {
        MessageCategory.P2P: [
            {
                "message_id": "msg_1",
                "chat_id": "oc_no_ctx",
                "sender": {"id": "ou_a", "name": "A"},
                "msg_type": "text",
                "content": "hello",
                "create_time": "1716796800",
            },
        ],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
        MessageCategory.AT_ALL: [],
    }

    analyzer = Analyzer(
        provider="claude", model="m", api_key="k", base_url="", keywords=[],
    )
    results = analyzer.analyze(messages, my_user_id="ou_me")

    assert "oc_no_ctx" in results
    prompt = mock_call_ai.call_args[0][0]
    # No message lines should have [上下文] prefix (template text is ok)
    msg_lines = [l for l in prompt.split("\n") if l.startswith("[")]
    assert not any("[上下文]" in l for l in msg_lines)
