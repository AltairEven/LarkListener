import json as _json
import sys
from unittest.mock import patch, MagicMock

from lark_listener import intent

_CONFIG = {"ai": {"provider": "ollama", "model": "x", "api_key": "", "base_url": ""}}


def _mock_ollama(mock_urlopen, content_obj):
    resp = MagicMock()
    resp.read.return_value = _json.dumps(
        {"message": {"content": _json.dumps(content_obj)}}
    ).encode()
    mock_urlopen.return_value.__enter__.return_value = resp


@patch("urllib.request.urlopen")
def test_summary_invalid_start_time_still_summary(mock_urlopen):
    _mock_ollama(mock_urlopen, {"type": "summary", "start_time": "not-a-date"})
    result = intent.parse("汇总最近的消息", _CONFIG)
    assert result.type == "summary"
    assert result.start_time is None


@patch("urllib.request.urlopen")
def test_summary_valid_start_time_parsed(mock_urlopen):
    _mock_ollama(mock_urlopen, {"type": "summary", "start_time": "2026-06-03T10:00:00+08:00"})
    result = intent.parse("汇总今天上午", _CONFIG)
    assert result.type == "summary"
    assert result.start_time is not None
    assert result.start_time.hour == 10


@patch("urllib.request.urlopen")
def test_none_intent(mock_urlopen):
    _mock_ollama(mock_urlopen, {"type": "none"})
    result = intent.parse("你好", _CONFIG)
    assert result.type == "none"


@patch("urllib.request.urlopen")
def test_config_modify_changes_parsed(mock_urlopen):
    _mock_ollama(mock_urlopen, {
        "type": "config_modify",
        "changes": [{"field": "poll_interval", "op": "set", "value": 600}],
    })
    result = intent.parse("轮询间隔改成10分钟", _CONFIG)
    assert result.type == "config_modify"
    assert result.changes == [{"field": "poll_interval", "op": "set", "value": 600}]


@patch("urllib.request.urlopen")
def test_config_view_and_confirm(mock_urlopen):
    _mock_ollama(mock_urlopen, {"type": "config_view"})
    assert intent.parse("当前配置", _CONFIG).type == "config_view"
    _mock_ollama(mock_urlopen, {"type": "confirm"})
    assert intent.parse("确认", _CONFIG).type == "confirm"


@patch("urllib.request.urlopen")
def test_bad_json_returns_error(mock_urlopen):
    resp = MagicMock()
    resp.read.return_value = _json.dumps({"message": {"content": "not json"}}).encode()
    mock_urlopen.return_value.__enter__.return_value = resp
    assert intent.parse("???", _CONFIG).type == "error"


@patch("urllib.request.urlopen")
def test_parse_tolerates_markdown_fenced_json(mock_urlopen):
    """Models (esp. local ollama) often wrap JSON in ```json fences. Intent must
    tolerate that like the analyzer does, instead of falling back to 'error'."""
    resp = MagicMock()
    fenced = '```json\n{"type": "config_view"}\n```'
    resp.read.return_value = _json.dumps({"message": {"content": fenced}}).encode()
    mock_urlopen.return_value.__enter__.return_value = resp
    assert intent.parse("当前配置", _CONFIG).type == "config_view"


@patch("urllib.request.urlopen")
def test_parse_tolerates_json_with_surrounding_prose(mock_urlopen):
    resp = MagicMock()
    content = '好的：\n{"type": "confirm"}\n以上'
    resp.read.return_value = _json.dumps({"message": {"content": content}}).encode()
    mock_urlopen.return_value.__enter__.return_value = resp
    assert intent.parse("确认", _CONFIG).type == "confirm"


def test_intent_call_claude_passes_timeout():
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.return_value.content = [MagicMock(text='{"type":"none"}')]
    cfg = {"ai": {"provider": "claude", "model": "m", "api_key": "k", "base_url": ""}}
    with patch.dict(sys.modules, {"anthropic": fake}):
        intent.parse("你好", cfg)
    kwargs = fake.Anthropic.return_value.messages.create.call_args.kwargs
    assert kwargs.get("timeout") == intent.INTENT_TIMEOUT


def test_intent_call_openai_passes_timeout():
    fake = MagicMock()
    fake.OpenAI.return_value.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content='{"type":"none"}'))
    ]
    cfg = {"ai": {"provider": "openai", "model": "m", "api_key": "k", "base_url": ""}}
    with patch.dict(sys.modules, {"openai": fake}):
        intent.parse("你好", cfg)
    kwargs = fake.OpenAI.return_value.chat.completions.create.call_args.kwargs
    assert kwargs.get("timeout") == intent.INTENT_TIMEOUT


@patch("urllib.request.urlopen")
def test_summary_naive_start_time_gets_local_tz(mock_urlopen):
    """A naive start_time from the AI (local models often omit the offset) must be
    pinned to +08:00, else it mixes with the aware `end` and skews the search window."""
    _mock_ollama(mock_urlopen, {"type": "summary", "start_time": "2026-06-08T10:00:00"})
    result = intent.parse("汇总今天上午", _CONFIG)
    assert result.type == "summary"
    assert result.start_time is not None
    assert result.start_time.utcoffset() is not None
    assert result.start_time.utcoffset().total_seconds() == 8 * 3600


@patch("urllib.request.urlopen")
def test_non_dict_result_returns_none(mock_urlopen):
    """If the model returns a JSON array/scalar instead of an object, classify as
    none instead of crashing on `.get`."""
    resp = MagicMock()
    resp.read.return_value = _json.dumps({"message": {"content": "[1, 2, 3]"}}).encode()
    mock_urlopen.return_value.__enter__.return_value = resp
    assert intent.parse("你好", _CONFIG).type == "none"


def test_editable_config_strips_protected():
    cfg = {
        "poll_interval": 300,
        "keywords": ["x"],
        "ai": {"api_key": "secret", "provider": "claude"},
        "notify": {"user_id": "ou_1"},
    }
    out = intent._editable_config(cfg)
    assert "ai" not in out and "notify" not in out
    assert out["poll_interval"] == 300 and out["keywords"] == ["x"]


@patch("lark_listener.intent._call_ai")
def test_prompt_excludes_protected_blocks(mock_call):
    mock_call.return_value = '{"type": "none"}'
    cfg = {
        "poll_interval": 300,
        "keywords": ["上线"],
        "ai": {"api_key": "secret", "provider": "claude", "model": "m"},
        "notify": {"user_id": "ou_secret", "bot_chat_id": "oc_secret"},
    }
    intent.parse("你好", cfg)
    prompt = mock_call.call_args.args[0]
    assert "secret" not in prompt
    assert "ou_secret" not in prompt
    assert "oc_secret" not in prompt
    assert "poll_interval" in prompt and "上线" in prompt


# --- 二轮 review：claude base_url / ollama 超时 ---

import json


def test_intent_claude_passes_base_url():
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.return_value.content = [MagicMock(text='{"type":"none"}')]
    cfg = {"ai": {"provider": "claude", "model": "m", "api_key": "k",
                  "base_url": "https://proxy.example"}}
    with patch.dict(sys.modules, {"anthropic": fake}):
        intent.parse("你好", {**_CONFIG, **cfg})
    assert fake.Anthropic.call_args.kwargs["base_url"] == "https://proxy.example"


def test_intent_ollama_uses_intent_timeout():
    """ollama 分支超时应与 INTENT_TIMEOUT 一致：本地小模型吃满 config JSON
    的 prompt 时 30s 很容易超，导致意图识别频繁「没太听懂」。"""
    captured = {}

    class _Resp:
        def read(self):
            return json.dumps({"message": {"content": '{"type":"none"}'}}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _Resp()

    import urllib.request as _ur
    with patch.object(_ur, "urlopen", fake_urlopen):
        intent.parse("你好", _CONFIG)
    assert captured["timeout"] == intent.INTENT_TIMEOUT
