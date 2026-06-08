import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from lark_listener.fetcher import Fetcher, MessageCategory

TZ = timezone(timedelta(hours=8))


def _make_search_result(messages):
    """Build lark-cli search result JSON."""
    return json.dumps({"ok": True, "data": {"messages": messages}})


def _empty_result():
    return _make_search_result([])


SAMPLE_MSGS = [
    {
        "message_id": "msg_001",
        "chat_id": "oc_chat1",
        "chat_name": "技术群",
        "sender": {"id": "ou_zhangsan", "name": "张三"},
        "msg_type": "text",
        "content": "线上服务挂了",
        "create_time": "1716796800",
    },
    {
        "message_id": "msg_002",
        "chat_id": "oc_chat2",
        "chat_name": "",
        "sender": {"id": "ou_lisi", "name": "李四"},
        "msg_type": "text",
        "content": "明天开会",
        "create_time": "1716796900",
    },
]


def _mock_run(results: list):
    """Return a side_effect function that yields results in order."""
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(results) - 1)
        call_count += 1
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = results[idx]
        return mock

    return side_effect


# --- Basic fetch tests ---


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_returns_three_categories(mock_run):
    mock_run.side_effect = _mock_run([
        _make_search_result(SAMPLE_MSGS),  # p2p
        _empty_result(),                    # at_me
        _empty_result(),                    # keyword "部署"
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids=set())

    assert len(result[MessageCategory.P2P]) == 2
    assert len(result[MessageCategory.AT_ME]) == 0
    assert len(result[MessageCategory.KEYWORD]) == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_at_me_messages(mock_run):
    at_me_msgs = [
        {
            "message_id": "msg_at",
            "chat_id": "oc_group",
            "chat_name": "技术群",
            "sender": {"id": "ou_a", "name": "A"},
            "msg_type": "text",
            "content": "@你 看一下",
            "create_time": "1716796800",
        },
    ]
    mock_run.side_effect = _mock_run([
        _empty_result(),                       # p2p
        _make_search_result(at_me_msgs),       # at_me
        _empty_result(),                       # keyword
    ])
    fetcher = Fetcher(keywords=["测试"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids=set())

    assert len(result[MessageCategory.AT_ME]) == 1
    assert result[MessageCategory.AT_ME][0]["message_id"] == "msg_at"


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_at_all_messages(mock_run):
    """Messages containing @everyone should be classified as AT_ALL."""
    at_all_msgs = [
        {
            "message_id": "msg_all",
            "chat_id": "oc_group",
            "chat_name": "全员群",
            "sender": {"id": "ou_bot", "name": "Bot"},
            "msg_type": "text",
            "content": "全员通知 @everyone 请查看",
            "create_time": "1716796800",
        },
    ]
    mock_run.side_effect = _mock_run([
        _empty_result(),                        # p2p
        _make_search_result(at_all_msgs),       # at_me (includes @all)
        _empty_result(),                        # keyword
    ])
    fetcher = Fetcher(keywords=["测试"])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )

    assert len(result[MessageCategory.AT_ME]) == 0
    assert len(result[MessageCategory.AT_ALL]) == 1
    assert result[MessageCategory.AT_ALL][0]["message_id"] == "msg_all"


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_at_all_disabled_skips_at_all(mock_run):
    """When include_at_all=False, @everyone messages should be skipped."""
    at_all_msgs = [
        {
            "message_id": "msg_all",
            "chat_id": "oc_group",
            "chat_name": "全员群",
            "sender": {"id": "ou_bot", "name": "Bot"},
            "msg_type": "text",
            "content": "全员通知 @everyone 请查看",
            "create_time": "1716796800",
        },
    ]
    mock_run.side_effect = _mock_run([
        _empty_result(),                        # p2p
        _make_search_result(at_all_msgs),       # at_me (includes @all)
        _empty_result(),                        # keyword
    ])
    fetcher = Fetcher(keywords=["测试"], include_at_all=False)
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )

    assert len(result[MessageCategory.AT_ALL]) == 0
    assert len(result[MessageCategory.AT_ME]) == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_at_all_disabled_but_keyword_matches(mock_run):
    """When include_at_all=False, @everyone messages can still be found by keyword search."""
    at_all_msg = {
        "message_id": "msg_all",
        "chat_id": "oc_group",
        "chat_name": "全员群",
        "sender": {"id": "ou_bot", "name": "Bot"},
        "msg_type": "text",
        "content": "部署完成 @everyone 请验证",
        "create_time": "1716796800",
    }
    mock_run.side_effect = _mock_run([
        _empty_result(),                         # p2p
        _make_search_result([at_all_msg]),        # at_me (includes @all)
        _make_search_result([at_all_msg]),        # keyword "部署" finds same msg
    ])
    fetcher = Fetcher(keywords=["部署"], include_at_all=False)
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )

    assert len(result[MessageCategory.AT_ALL]) == 0
    assert len(result[MessageCategory.KEYWORD]) == 1
    assert result[MessageCategory.KEYWORD][0]["matched_keyword"] == "部署"


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_keyword_messages(mock_run):
    kw_msgs = [
        {
            "message_id": "msg_kw",
            "chat_id": "oc_group",
            "chat_name": "运维群",
            "sender": {"id": "ou_b", "name": "B"},
            "msg_type": "text",
            "content": "部署完成",
            "create_time": "1716796800",
        },
    ]
    mock_run.side_effect = _mock_run([
        _empty_result(),                    # p2p
        _empty_result(),                    # at_me
        _make_search_result(kw_msgs),       # keyword "部署"
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids=set())

    assert len(result[MessageCategory.KEYWORD]) == 1
    assert result[MessageCategory.KEYWORD][0]["matched_keyword"] == "部署"


# --- Dedup tests ---


@patch("lark_listener.fetcher.subprocess.run")
def test_dedup_across_categories(mock_run):
    """Same message_id in p2p and at_me -- p2p wins (higher priority)."""
    mock_run.side_effect = _mock_run([
        _make_search_result(SAMPLE_MSGS),  # p2p: msg_001, msg_002
        _make_search_result(SAMPLE_MSGS),  # at_me: same messages
        _empty_result(),                    # keyword
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids=set())

    assert len(result[MessageCategory.P2P]) == 2
    assert len(result[MessageCategory.AT_ME]) == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_dedup_at_me_vs_keyword(mock_run):
    """Same message in at_me and keyword -- at_me wins."""
    shared_msg = [{
        "message_id": "msg_shared",
        "chat_id": "oc_g",
        "chat_name": "群",
        "sender": {"id": "ou_x", "name": "X"},
        "msg_type": "text",
        "content": "部署 @你",
        "create_time": "1716796800",
    }]
    mock_run.side_effect = _mock_run([
        _empty_result(),                      # p2p
        _make_search_result(shared_msg),      # at_me
        _make_search_result(shared_msg),      # keyword
    ])
    fetcher = Fetcher(keywords=["部署"])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )

    assert len(result[MessageCategory.AT_ME]) == 1
    assert len(result[MessageCategory.KEYWORD]) == 0


# --- Processed IDs tests ---


@patch("lark_listener.fetcher.subprocess.run")
def test_skip_processed_ids(mock_run):
    mock_run.side_effect = _mock_run([
        _make_search_result(SAMPLE_MSGS),
        _empty_result(),
        _empty_result(),
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids={"msg_001"})

    assert len(result[MessageCategory.P2P]) == 1
    assert result[MessageCategory.P2P][0]["message_id"] == "msg_002"


@patch("lark_listener.fetcher.subprocess.run")
def test_skip_all_processed(mock_run):
    """When all messages are already processed, result should be empty."""
    mock_run.side_effect = _mock_run([
        _make_search_result(SAMPLE_MSGS),
        _empty_result(),
        _empty_result(),
    ])
    fetcher = Fetcher(keywords=["部署"])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids={"msg_001", "msg_002"},
    )

    assert len(result[MessageCategory.P2P]) == 0


# --- Exclude chat IDs tests ---


@patch("lark_listener.fetcher.subprocess.run")
def test_exclude_chat_ids(mock_run):
    """Messages from excluded chat_ids should be filtered out."""
    mock_run.side_effect = _mock_run([
        _make_search_result(SAMPLE_MSGS),
        _empty_result(),
        _empty_result(),
    ])
    fetcher = Fetcher(keywords=["部署"])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
        exclude_chat_ids={"oc_chat1"},
    )

    assert len(result[MessageCategory.P2P]) == 1
    assert result[MessageCategory.P2P][0]["chat_id"] == "oc_chat2"


@patch("lark_listener.fetcher.subprocess.run")
def test_exclude_all_chats(mock_run):
    mock_run.side_effect = _mock_run([
        _make_search_result(SAMPLE_MSGS),
        _empty_result(),
        _empty_result(),
    ])
    fetcher = Fetcher(keywords=[])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
        exclude_chat_ids={"oc_chat1", "oc_chat2"},
    )

    assert len(result[MessageCategory.P2P]) == 0


# --- Error handling tests ---


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_handles_subprocess_error(mock_run):
    """Non-zero return code should produce empty results."""
    mock = MagicMock()
    mock.returncode = 1
    mock.stdout = ""
    mock_run.return_value = mock

    fetcher = Fetcher(keywords=["部署"])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )

    assert all(len(msgs) == 0 for msgs in result.values())


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_handles_invalid_json(mock_run):
    """Invalid JSON output should produce empty results."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "not json"
    mock_run.return_value = mock

    fetcher = Fetcher(keywords=[])
    # Must NOT raise — invalid JSON is treated as empty results (best-effort).
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )
    assert all(len(msgs) == 0 for msgs in result.values())


# --- Multiple keywords tests ---


@patch("lark_listener.fetcher.subprocess.run")
def test_multiple_keywords(mock_run):
    """Each keyword triggers a separate search."""
    kw1_msg = [{
        "message_id": "msg_kw1", "chat_id": "oc_g1", "chat_name": "群1",
        "sender": {"id": "ou_a", "name": "A"}, "msg_type": "text",
        "content": "部署成功", "create_time": "1716796800",
    }]
    kw2_msg = [{
        "message_id": "msg_kw2", "chat_id": "oc_g2", "chat_name": "群2",
        "sender": {"id": "ou_b", "name": "B"}, "msg_type": "text",
        "content": "发布上线", "create_time": "1716796900",
    }]
    mock_run.side_effect = _mock_run([
        _empty_result(),                    # p2p
        _empty_result(),                    # at_me
        _make_search_result(kw1_msg),       # keyword "部署"
        _make_search_result(kw2_msg),       # keyword "发布"
    ])
    fetcher = Fetcher(keywords=["部署", "发布"])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )

    assert len(result[MessageCategory.KEYWORD]) == 2
    keywords_found = {m["matched_keyword"] for m in result[MessageCategory.KEYWORD]}
    assert keywords_found == {"部署", "发布"}


# --- Fill chat names tests ---


@patch("lark_listener.fetcher.subprocess.run")
def test_fill_chat_names(mock_run):
    """Messages with empty chat_name should get names filled via lookup."""
    msg_no_name = [{
        "message_id": "msg_noname", "chat_id": "oc_unnamed", "chat_name": "",
        "sender": {"id": "ou_a", "name": "A"}, "msg_type": "text",
        "content": "@你 看下", "create_time": "1716796800",
    }]

    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        mock = MagicMock()
        cmd = args[0]
        if "+messages-search" in cmd:
            if call_count[0] == 1:
                # p2p
                mock.returncode = 0
                mock.stdout = _empty_result()
            elif call_count[0] == 2:
                # at_me
                mock.returncode = 0
                mock.stdout = _make_search_result(msg_no_name)
            else:
                mock.returncode = 0
                mock.stdout = _empty_result()
        elif "chats" in cmd and "get" in cmd:
            # Chat name lookup
            mock.returncode = 0
            mock.stdout = '"研发群"'
        else:
            mock.returncode = 0
            mock.stdout = _empty_result()
        return mock

    mock_run.side_effect = side_effect
    fetcher = Fetcher(keywords=[])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )

    assert len(result[MessageCategory.AT_ME]) == 1
    assert result[MessageCategory.AT_ME][0]["chat_name"] == "研发群"


# --- No keywords tests ---


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_no_keywords(mock_run):
    """With no keywords, only p2p and at_me searches are performed."""
    mock_run.side_effect = _mock_run([
        _make_search_result(SAMPLE_MSGS),  # p2p
        _empty_result(),                    # at_me
    ])
    fetcher = Fetcher(keywords=[])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )

    assert len(result[MessageCategory.P2P]) == 2
    assert len(result[MessageCategory.KEYWORD]) == 0
    # Only 2 subprocess calls (p2p + at_me), no keyword search
    assert mock_run.call_count == 2


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_tolerates_non_json_output(mock_run):
    """lark-cli returns 0 but emits non-JSON: must not crash, treat as empty."""
    def side_effect(*args, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Warning: something happened\nnot json"
        return mock

    mock_run.side_effect = side_effect
    fetcher = Fetcher(keywords=["部署"])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )
    assert all(len(msgs) == 0 for msgs in result.values())


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_tolerates_subprocess_timeout(mock_run):
    """A lark-cli timeout must not crash the poll cycle."""
    import subprocess as _sp
    mock_run.side_effect = _sp.TimeoutExpired(cmd="lark-cli", timeout=60)
    fetcher = Fetcher(keywords=["部署"])
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )
    assert all(len(msgs) == 0 for msgs in result.values())


# --- fetch_context tests (#12) ---


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_context_keeps_latest_after_sorting(mock_run):
    """Context must be sorted by create_time before truncation, so the most
    recent messages are kept regardless of lark-cli's return order."""
    # lark-cli returns out-of-order messages (incl. the matched one)
    unordered = [
        {"message_id": "a", "chat_id": "oc_x", "create_time": "300",
         "sender": {"id": "ou_1", "name": "A"}, "msg_type": "text", "content": "a"},
        {"message_id": "m_match", "chat_id": "oc_x", "create_time": "500",
         "sender": {"id": "ou_1", "name": "A"}, "msg_type": "text", "content": "matched"},
        {"message_id": "c", "chat_id": "oc_x", "create_time": "900",
         "sender": {"id": "ou_1", "name": "A"}, "msg_type": "text", "content": "c"},
        {"message_id": "b", "chat_id": "oc_x", "create_time": "100",
         "sender": {"id": "ou_1", "name": "A"}, "msg_type": "text", "content": "b"},
    ]
    mock_run.side_effect = _mock_run([_make_search_result(unordered)])

    fetcher = Fetcher()
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.KEYWORD] = [
        {"message_id": "m_match", "chat_id": "oc_x", "create_time": "500"}
    ]
    context = fetcher.fetch_context(
        categorized,
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        limit=2,
    )
    # Excludes m_match; of [a(300), c(900), b(100)] the latest 2 are a and c
    ids = [m["message_id"] for m in context["oc_x"]]
    assert ids == ["a", "c"]
