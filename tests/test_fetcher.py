import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from lark_listener.fetcher import Fetcher, MessageCategory

TZ = timezone(timedelta(hours=8))

SAMPLE_SEARCH_RESULT = json.dumps({
    "ok": True,
    "items": [
        {
            "message_id": "msg_001",
            "chat_id": "oc_chat1",
            "chat_name": "技术群",
            "sender": {"name": "张三"},
            "body": {"content": "线上服务挂了"},
            "create_time": "1716796800",
        },
        {
            "message_id": "msg_002",
            "chat_id": "oc_chat2",
            "chat_name": "",
            "sender": {"name": "李四"},
            "body": {"content": "明天开会"},
            "create_time": "1716796900",
        },
    ]
})

EMPTY_RESULT = json.dumps({"ok": True, "items": []})


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


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_returns_three_categories(mock_run):
    mock_run.side_effect = _mock_run([
        SAMPLE_SEARCH_RESULT,  # p2p
        EMPTY_RESULT,          # at_me
        EMPTY_RESULT,          # keyword "部署"
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids=set())

    assert len(result[MessageCategory.P2P]) == 2
    assert len(result[MessageCategory.AT_ME]) == 0
    assert len(result[MessageCategory.KEYWORD]) == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_dedup_across_categories(mock_run):
    """Same message_id in p2p and at_me — p2p wins."""
    mock_run.side_effect = _mock_run([
        SAMPLE_SEARCH_RESULT,  # p2p: msg_001, msg_002
        SAMPLE_SEARCH_RESULT,  # at_me: msg_001, msg_002 (same)
        EMPTY_RESULT,          # keyword
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids=set())

    assert len(result[MessageCategory.P2P]) == 2
    assert len(result[MessageCategory.AT_ME]) == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_skip_processed_ids(mock_run):
    mock_run.side_effect = _mock_run([
        SAMPLE_SEARCH_RESULT,
        EMPTY_RESULT,
        EMPTY_RESULT,
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids={"msg_001"})

    assert len(result[MessageCategory.P2P]) == 1
    assert result[MessageCategory.P2P][0]["message_id"] == "msg_002"
