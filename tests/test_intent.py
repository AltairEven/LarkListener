import json as _json
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
