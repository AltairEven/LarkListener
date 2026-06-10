import json
from unittest.mock import patch, MagicMock
from lark_listener.notifier import (
    Notifier,
    build_summary_text,
    build_summary_response,
    build_summary_card,
    error_response,
)
from lark_listener.fetcher import MessageCategory
from lark_listener.analyzer import ConversationAnalysis
from lark_listener.notifier import _applescript_escape


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


def build_summary_text_legacy(categorized, analysis, start, end, my_user_id=""):
    """Render Markdown fallback the way callers do: response → text.

    build_summary_text now consumes the unified envelope; these legacy-shaped
    tests build the envelope first, asserting the rendered Markdown is unchanged.
    """
    return build_summary_text(
        build_summary_response(categorized, analysis, start, end, my_user_id)
    )


def test_build_summary_text_contains_sections():
    text = build_summary_text_legacy(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "**━━ 私聊消息" in text
    assert "**━━ @我" in text
    assert "**━━ 关键词命中" in text
    assert "查看原文" in text


def test_build_summary_text_contains_names():
    text = build_summary_text_legacy(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "张三" in text
    assert "技术群" in text
    assert "运维群" in text


def test_build_summary_text_shows_keyword():
    text = build_summary_text_legacy(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "命中：部署" in text


def test_build_summary_text_shows_time_range():
    text = build_summary_text_legacy(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "15:00" in text
    assert "15:30" in text


def test_build_summary_text_shows_urgency_icon():
    text = build_summary_text_legacy(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    # oc_p2p1 is urgent
    assert "🔴" in text


def test_build_summary_text_shows_ai_summary():
    text = build_summary_text_legacy(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "线上故障求助" in text
    assert "请求代码审查" in text


def test_build_summary_empty():
    empty = {cat: [] for cat in MessageCategory}
    text = build_summary_text_legacy(empty, {}, "15:00", "15:30", MY_USER_ID)
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
    text = build_summary_text_legacy(messages, {}, "15:00", "15:30", MY_USER_ID)
    assert text == ""


def test_build_summary_without_analysis():
    """Should still produce output even without AI analysis."""
    text = build_summary_text_legacy(SAMPLE_MESSAGES, {}, "15:00", "15:30", MY_USER_ID)
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
    text = build_summary_text_legacy(messages, {}, "15:00", "15:30", MY_USER_ID)
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
    text = build_summary_text_legacy(messages, analysis, "15:00", "15:30", MY_USER_ID)
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
    text = build_summary_text_legacy(messages, analysis, "15:00", "15:30", MY_USER_ID)
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
    text = build_summary_text_legacy(messages, analysis, "15:00", "15:30", MY_USER_ID)
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
    text = build_summary_text_legacy(messages, analysis, "15:00", "15:30", MY_USER_ID)
    # Urgent should come before normal
    urgent_pos = text.index("紧急群")
    normal_pos = text.index("普通群")
    assert urgent_pos < normal_pos


def test_build_summary_conversation_count():
    """Section header should show conversation count."""
    text = build_summary_text_legacy(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert "1 个会话" in text


def test_build_summary_sections_separated_by_divider():
    """Sections should be separated by --- divider."""
    text = build_summary_text_legacy(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
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
    text = build_summary_text_legacy(messages, analysis, "15:00", "15:30", MY_USER_ID)
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
    text = build_summary_text_legacy(messages, {}, "15:00", "15:30", MY_USER_ID)
    assert text.index("私聊消息") < text.index("@我") < text.index("关键词命中") < text.index("@所有人")


# --- build_summary_response (统一封套) tests ---


def test_response_envelope_shape():
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert resp["code"] == 0
    assert resp["errorMsg"] == ""
    assert "period" in resp["data"]
    assert "conversations" in resp["data"]


def test_response_period():
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    assert resp["data"]["period"] == {"start": "15:00", "end": "15:30"}


def test_response_is_json_serializable():
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    # Round-trips with non-ASCII preserved
    assert json.loads(json.dumps(resp, ensure_ascii=False))["code"] == 0


def test_response_conversation_fields():
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    convs = resp["data"]["conversations"]
    p2p = [c for c in convs if c["category"] == "p2p"][0]
    assert p2p["title"] == "张三"
    assert p2p["chat_id"] == "oc_p2p1"
    assert p2p["urgency"] == "urgent"
    assert p2p["summary"] == "线上故障求助"
    assert p2p["snippet"] == "线上挂了"
    assert p2p["count"] == 1
    assert "applink.feishu.cn" in p2p["link"]
    assert p2p["label"] == "私聊消息"


def test_response_keyword_carries_matched_keyword():
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    kw = [c for c in resp["data"]["conversations"] if c["category"] == "keyword"][0]
    assert kw["matched_keyword"] == "部署"


def test_response_category_order():
    messages = {
        MessageCategory.P2P: [_make_msg("m_p", "oc_p", "ou_a", "A", "私聊")],
        MessageCategory.AT_ME: [_make_msg("m_m", "oc_m", "ou_b", "B", "@你", chat_name="群1")],
        MessageCategory.KEYWORD: [_make_msg("m_k", "oc_k", "ou_c", "C", "部署完成",
                                            chat_name="群2", matched_keyword="部署")],
        MessageCategory.AT_ALL: [_make_msg("m_a", "oc_a", "ou_d", "D", "通知", chat_name="群3")],
    }
    convs = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)["data"]["conversations"]
    cats = [c["category"] for c in convs]
    assert cats == ["p2p", "at_me", "keyword", "at_all"]


def test_response_urgent_first_within_category():
    messages = {
        MessageCategory.AT_ME: [
            _make_msg("m_n", "oc_normal", "ou_a", "A", "普通", chat_name="普通群"),
            _make_msg("m_u", "oc_urgent", "ou_b", "B", "紧急", chat_name="紧急群"),
        ],
        MessageCategory.P2P: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    analysis = {
        "oc_normal": ConversationAnalysis("oc_normal", "medium", "normal", "普通", "m_n"),
        "oc_urgent": ConversationAnalysis("oc_urgent", "high", "urgent", "紧急", "m_u"),
    }
    convs = build_summary_response(messages, analysis, "15:00", "15:30", MY_USER_ID)["data"]["conversations"]
    titles = [c["title"] for c in convs]
    assert titles.index("紧急群") < titles.index("普通群")


def test_response_empty_when_only_self():
    messages = {
        MessageCategory.P2P: [_make_msg("m_self", "oc_self", MY_USER_ID, "我", "自己说的")],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    resp = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)
    assert resp["code"] == 0
    assert resp["data"]["conversations"] == []


def test_response_snippet_truncation():
    long_content = "这是一条非常长的消息" * 20
    messages = {
        MessageCategory.P2P: [_make_msg("m_l", "oc_l", "ou_o", "长文", long_content)],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    snippet = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)["data"]["conversations"][0]["snippet"]
    assert snippet.endswith("...")
    assert len(snippet) <= 83


def test_error_response_shape():
    resp = error_response("出错了")
    assert resp["code"] == 1
    assert resp["errorMsg"] == "出错了"
    assert resp["data"] is None


# --- build_summary_card tests ---


def test_card_none_when_empty():
    empty = {cat: [] for cat in MessageCategory}
    resp = build_summary_response(empty, {}, "15:00", "15:30", MY_USER_ID)
    assert build_summary_card(resp) is None


def test_card_none_on_error():
    assert build_summary_card(error_response("x")) is None


def test_card_has_table_with_rows():
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    card = build_summary_card(resp)
    elements = card["body"]["elements"]
    tables = [e for e in elements if e.get("tag") == "table"]
    assert tables, "card should contain at least one table element"
    total_rows = sum(len(t["rows"]) for t in tables)
    # 3 conversations across 3 categories
    assert total_rows == 3


def test_card_is_json_serializable_and_has_period_header():
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    card = build_summary_card(resp)
    blob = json.dumps(card, ensure_ascii=False)
    assert "15:00" in blob and "15:30" in blob


def test_card_style_finalized():
    """卡片样式定稿（2026-06-10 测试1-6 与用户逐版确认）：
    会话列纯名称（无 🔴，紧急靠类内排序）、保留命中提示；分类标题+彩色 emoji
    在「会话」列表头、无独立分隔行；row_height auto 让长文换行。"""
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    card = build_summary_card(resp)
    blob = json.dumps(card, ensure_ascii=False)
    assert "🔴" not in blob             # 会话列不带紧急 emoji
    assert "命中：部署" in blob          # keyword hint 保留
    assert "applink.feishu.cn" in blob  # 原文跳转链接

    elements = card["body"]["elements"]
    assert all(e["tag"] == "table" for e in elements)  # 无独立分隔行，全是表格
    assert all(e["row_height"] == "auto" for e in elements)
    headers = [e["columns"][0]["display_name"] for e in elements]
    assert any(h.startswith("🟦") and "私聊消息（1）" in h for h in headers)
    assert any(h.startswith("🟩") for h in headers)
    assert any(h.startswith("🟧") for h in headers)


def test_card_orig_column_is_snippet_link():
    """原文列：消息片段本身就是跳转链接（点文字即跳原会话）。"""
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    card = build_summary_card(resp)
    rows = [r for e in card["body"]["elements"] for r in e["rows"]]
    p2p_row = next(r for r in rows if "张三" in r["conv"])
    assert p2p_row["orig"].startswith("[“线上挂了”](")
    assert "applink.feishu.cn" in p2p_row["orig"]


def test_card_summary_dash_when_no_analysis():
    """摘要列只放 AI 摘要，没有就是 —（snippet 不再顶进摘要列，留在原文列）。"""
    resp = build_summary_response(SAMPLE_MESSAGES, {}, "15:00", "15:30", MY_USER_ID)
    card = build_summary_card(resp)
    rows = [r for e in card["body"]["elements"] for r in e["rows"]]
    assert all(r["summ"] == "—" for r in rows)
    assert all(r["orig"].startswith("[“") for r in rows)


# --- Notifier tests ---


@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_sends_message_and_notification(mock_run, mock_resolve):
    mock_run.return_value = MagicMock(returncode=0, stdout='{"ok": true}')
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    # Should call subprocess twice: lark-cli send + terminal-notifier
    assert mock_run.call_count == 2

    # First call: lark-cli im +messages-send as an interactive card
    first_call_args = mock_run.call_args_list[0][0][0]
    assert first_call_args[0].endswith("lark-cli")
    assert "interactive" in first_call_args
    assert "--content" in first_call_args
    # The card JSON payload should contain a table element
    content_idx = first_call_args.index("--content") + 1
    assert '"tag": "table"' in first_call_args[content_idx]

    # Second call: terminal-notifier (resolved to an absolute path)
    second_call_args = mock_run.call_args_list[1][0][0]
    assert second_call_args[0].endswith("terminal-notifier")


@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_falls_back_to_markdown_when_card_fails(mock_run, mock_resolve):
    """If the interactive card send fails, fall back to a Markdown message.

    The card may be rejected by tenants that don't render the table component;
    the user should still get a readable summary rather than nothing.
    """
    calls = []

    def side_effect(cmd, *a, **k):
        calls.append(cmd)
        if cmd[0].endswith("lark-cli") and "interactive" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="invalid card")
        return MagicMock(returncode=0, stdout="")

    mock_run.side_effect = side_effect
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    lark_calls = [c for c in calls if c[0].endswith("lark-cli")]
    assert any("interactive" in c for c in lark_calls)   # card attempted
    assert any("--markdown" in c for c in lark_calls)    # markdown fallback sent


@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_survives_failing_bot_message(mock_run, mock_resolve):
    """A failing bot send (lark-cli missing/timeout) must NOT crash notify.

    Otherwise the exception propagates out of poll_once before the caller
    advances state.last_poll_time, freezing the start time and re-pushing the
    same summary every cycle. The bot send is best-effort like the desktop toast.
    """
    import subprocess as _sp

    def side_effect(cmd, *args, **kwargs):
        if cmd[0].endswith("lark-cli"):
            raise _sp.TimeoutExpired(cmd="lark-cli", timeout=30)
        return MagicMock(returncode=0, stdout="")

    mock_run.side_effect = side_effect
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")

    # Must not raise even though the bot message fails.
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    # The desktop toast (secondary channel) must still have been attempted.
    assert any(c[0][0][0].endswith("terminal-notifier") for c in mock_run.call_args_list)


@patch("lark_listener.notifier.subprocess.run")
def test_notify_skips_when_no_messages(mock_run):
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    empty = {cat: [] for cat in MessageCategory}
    notifier.notify(empty, {}, "15:00", "15:30")

    mock_run.assert_not_called()


@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_macos_notification_counts(mock_run, mock_resolve):
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


@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_survives_failing_desktop_notification(mock_run, mock_resolve):
    """A missing/failing terminal-notifier must NOT crash notify.

    The bot message is the primary channel and is sent first; the macOS toast is
    secondary. If notify() raised here it would abort the poll cycle before the
    caller can advance state.last_poll_time, freezing the summary start time.
    """
    def side_effect(cmd, *args, **kwargs):
        if cmd[0].endswith("terminal-notifier"):
            raise FileNotFoundError(2, "No such file or directory", "terminal-notifier")
        return MagicMock(returncode=0, stdout='{"ok": true}')

    mock_run.side_effect = side_effect
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")

    # Must not raise even though the desktop notification fails.
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    # The bot message (primary channel) must still have been sent.
    first_call_args = mock_run.call_args_list[0][0][0]
    assert first_call_args[0].endswith("lark-cli")


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


def test_applescript_escape_quotes_and_backslashes():
    assert _applescript_escape('a"b') == 'a\\"b'
    assert _applescript_escape("a\\b") == "a\\\\b"
    # 反斜杠先转义，避免把已转义的引号再次破坏
    assert _applescript_escape('x"\\y') == 'x\\"\\\\y'


@patch("lark_listener.notifier.resolve_executable")
@patch("lark_listener.notifier.subprocess.run")
def test_notify_uses_osascript_when_no_terminal_notifier(mock_run, mock_resolve):
    # terminal-notifier 未安装 → resolve 返回裸名；osascript 解析到绝对路径。
    def resolve(name):
        return "terminal-notifier" if name == "terminal-notifier" else "/usr/bin/" + name
    mock_resolve.side_effect = resolve
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    assert mock_run.call_count == 2
    second = mock_run.call_args_list[1][0][0]
    assert second[0].endswith("osascript")
    assert "-e" in second
    script = second[second.index("-e") + 1]
    assert "display notification" in script
    assert "1个私聊" in script


@patch("lark_listener.notifier.resolve_executable")
@patch("lark_listener.notifier.subprocess.run")
def test_notify_prefers_terminal_notifier_when_present(mock_run, mock_resolve):
    # terminal-notifier 解析到绝对路径 → 走它，保留 -open 点击跳转。
    def resolve(name):
        return "/opt/homebrew/bin/" + name
    mock_resolve.side_effect = resolve
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    second = mock_run.call_args_list[1][0][0]
    assert second[0].endswith("terminal-notifier")
    assert "-open" in second


@patch("lark_listener.notifier.build_summary_response")
@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_uses_given_envelope_without_rebuilding(mock_run, mock_resolve, mock_build):
    """调用方传入 resp 时直接消费（cmd_summarize 路径）：不重建封套，
    保证 stdout JSON 与推送卡片同源。"""
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    resp = build_summary_response(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", MY_USER_ID)
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30",
                    my_user_id=MY_USER_ID, resp=resp)
    mock_build.assert_not_called()
    lark_calls = [c[0][0] for c in mock_run.call_args_list if c[0][0][0].endswith("lark-cli")]
    assert any("interactive" in c for c in lark_calls)  # 卡片确实发出


@patch("lark_listener.notifier.subprocess.run")
def test_notify_empty_envelope_sends_nothing(mock_run):
    """空窗口：卡片为 None → 不发 bot 消息也不发 macOS 通知（notify 统一裁决，
    cmd_summarize 不再自行判空，这条保证依赖于此）。"""
    empty = {cat: [] for cat in MessageCategory}
    Notifier(user_id="ou_test", bot_chat_id="oc_test").notify(
        empty, {}, "15:00", "15:30", my_user_id=MY_USER_ID)
    mock_run.assert_not_called()


# --- 机器人 p2p 会话标题（2026-06-10 调试定案）---


def _make_app_msg(message_id, chat_id, app_id, content, name=None, chat_type="p2p"):
    """对端为机器人应用的消息：sender 无 name（除非 fetcher 已补），id_type=app_id。"""
    sender = {"id": app_id, "id_type": "app_id", "sender_type": "app"}
    if name:
        sender["name"] = name
    return {"message_id": message_id, "chat_id": chat_id, "sender": sender,
            "msg_type": "text", "content": content, "create_time": "1716796800",
            "chat_type": chat_type}


def test_p2p_app_partner_with_filled_name():
    """fetcher 已补上机器人名 → 标题用真名。"""
    messages = {
        MessageCategory.P2P: [_make_app_msg("m1", "oc_bot1", "cli_abc12345", "答疑", name="SDK专家")],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    resp = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)
    assert resp["data"]["conversations"][0]["title"] == "SDK专家"


def test_p2p_app_partner_without_name_readable_fallback():
    """机器人名查不到（权限未批/失败）→ 可读回退「机器人(尾号8位)」而非「未知」。"""
    messages = {
        MessageCategory.P2P: [_make_app_msg("m1", "oc_bot1", "cli_a9b08bbbbdf89cc7", "答疑")],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    resp = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)
    title = resp["data"]["conversations"][0]["title"]
    assert title == "机器人(bdf89cc7)"


def test_p2p_prefers_named_sender_over_nameless():
    """首条非我消息无名（app）、后续有名（人）→ 取有名的，不再是「未知」。"""
    messages = {
        MessageCategory.P2P: [
            _make_app_msg("m1", "oc_mix", "cli_abc12345", "通知"),
            _make_msg("m2", "oc_mix", "ou_partner", "张三", "在吗", create_time="1716796900"),
        ],
        MessageCategory.AT_ME: [], MessageCategory.KEYWORD: [], MessageCategory.AT_ALL: [],
    }
    resp = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)
    assert resp["data"]["conversations"][0]["title"] == "张三"


def test_keyword_category_p2p_chat_uses_partner_name():
    """关键词搜索无 chat-type 过滤，p2p 消息会落进 KEYWORD 分类：
    chat_type=p2p 且无群名时应走对端名解析，而不是「群聊(尾号)」。"""
    m = _make_msg("m1", "oc_pkw", "ou_partner", "李四", "部署完成了", matched_keyword="部署")
    m["chat_type"] = "p2p"
    messages = {
        MessageCategory.P2P: [], MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [m], MessageCategory.AT_ALL: [],
    }
    resp = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)
    c = resp["data"]["conversations"][0]
    assert c["category"] == "keyword"
    assert c["title"] == "李四"
