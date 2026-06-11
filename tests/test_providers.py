"""providers.py：AI provider 注册表（complete / deep_probe / SDK 映射）。"""
import json
import sys
from unittest.mock import patch, MagicMock

import pytest

from lark_listener import providers


def test_get_unknown_provider_raises():
    with pytest.raises(ValueError):
        providers.get("nope")


def test_sdk_and_pip_mappings():
    assert providers.sdk_import_for("claude") == "anthropic"
    assert providers.sdk_import_for("openai") == "openai"
    assert providers.sdk_import_for("ollama") is None
    assert providers.pip_packages_for("claude") == ["anthropic>=0.30.0"]
    assert providers.pip_packages_for("openai") == ["openai>=1.30.0"]
    assert providers.pip_packages_for("ollama") == []
    assert providers.pip_packages_for("unknown") == []


def test_claude_complete_passes_system_max_tokens_base_url():
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.return_value.content = [MagicMock(text="hi")]
    with patch.dict(sys.modules, {"anthropic": fake}):
        out = providers.complete("claude", model="m", api_key="k",
                                 base_url="https://proxy", user_prompt="u",
                                 system="SYS", max_tokens=512, timeout=60)
    assert out == "hi"
    assert fake.Anthropic.call_args.kwargs["base_url"] == "https://proxy"
    kwargs = fake.Anthropic.return_value.messages.create.call_args.kwargs
    assert kwargs["system"] == "SYS" and kwargs["max_tokens"] == 512 and kwargs["timeout"] == 60


def test_claude_complete_empty_base_url_is_none_and_no_system_kw():
    """base_url 为空传 None 走官方默认；无 system 时不传 system 关键字
    （与 intent 既有行为一致）。"""
    fake = MagicMock()
    fake.Anthropic.return_value.messages.create.return_value.content = [MagicMock(text="x")]
    with patch.dict(sys.modules, {"anthropic": fake}):
        providers.complete("claude", model="m", api_key="k", base_url="",
                           user_prompt="u", timeout=60)
    assert fake.Anthropic.call_args.kwargs["base_url"] is None
    assert "system" not in fake.Anthropic.return_value.messages.create.call_args.kwargs


def test_openai_complete_system_optional():
    fake = MagicMock()
    fake.OpenAI.return_value.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="r"))]
    with patch.dict(sys.modules, {"openai": fake}):
        providers.complete("openai", model="m", api_key="k", base_url="",
                           user_prompt="u", system="SYS", timeout=60)
        msgs = fake.OpenAI.return_value.chat.completions.create.call_args.kwargs["messages"]
        assert msgs[0] == {"role": "system", "content": "SYS"}
        providers.complete("openai", model="m", api_key="k", base_url="",
                           user_prompt="u", timeout=60)
        msgs = fake.OpenAI.return_value.chat.completions.create.call_args.kwargs["messages"]
        assert msgs == [{"role": "user", "content": "u"}]


def test_ollama_complete_default_endpoint_and_timeout():
    captured = {}

    class _Resp:
        def read(self):
            return json.dumps({"message": {"content": "r"}}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return _Resp()

    import urllib.request as _ur
    with patch.object(_ur, "urlopen", fake_urlopen):
        out = providers.complete("ollama", model="m", api_key="", base_url="",
                                 user_prompt="u", timeout=42)
    assert out == "r"
    assert captured["url"] == providers.OLLAMA_DEFAULT_BASE + "/api/chat"
    assert captured["timeout"] == 42
