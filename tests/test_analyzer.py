import json
from unittest.mock import patch, MagicMock
from lark_listener.analyzer import Analyzer, AnalysisResult
from lark_listener.fetcher import MessageCategory

SAMPLE_AI_RESPONSE = json.dumps([
    {
        "message_id": "msg_001",
        "relevance": "high",
        "urgency": "urgent",
        "summary": "线上服务故障求助",
    },
    {
        "message_id": "msg_002",
        "relevance": "medium",
        "urgency": "normal",
        "summary": "会议时间变更",
    },
])

SAMPLE_MESSAGES = {
    MessageCategory.P2P: [
        {"message_id": "msg_001", "sender": {"name": "张三"}, "body": {"content": "线上服务挂了"}},
    ],
    MessageCategory.AT_ME: [
        {"message_id": "msg_002", "sender": {"name": "李四"}, "body": {"content": "明天开会改时间"}},
    ],
    MessageCategory.KEYWORD: [],
}


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_returns_results(mock_call_ai):
    mock_call_ai.return_value = json.loads(SAMPLE_AI_RESPONSE)

    analyzer = Analyzer(
        provider="claude",
        model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="",
        keywords=["故障"],
    )
    results = analyzer.analyze(SAMPLE_MESSAGES)

    assert "msg_001" in results
    assert results["msg_001"].urgency == "urgent"
    assert results["msg_001"].summary == "线上服务故障求助"
    assert "msg_002" in results


def test_analyze_empty_messages():
    analyzer = Analyzer(
        provider="claude",
        model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="",
        keywords=["故障"],
    )
    empty = {cat: [] for cat in MessageCategory}
    results = analyzer.analyze(empty)
    assert results == {}
