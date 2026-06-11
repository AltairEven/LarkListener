"""AI provider 注册表——「新增一个 AI 后端」的主要改动点。

（仍需各加一行的外围：analyzer._call_ai 的分发、doctor.check_ai_backend 的
provider 白名单、setup 向导的选择菜单。）

收口此前散布四处、历史上漂移过的 provider 知识：analyzer / intent 各一份
三后端调用、doctor 一份探活分发、setup_wizard 与 doctor 各一份 SDK 包名
映射、三份 ollama 默认端点。

约定：
- SDK 一律在方法体内延迟 import（核心依赖仅 pyyaml+ruamel，AI SDK 是按
  所选后端 extras 选装的，import 失败要发生在调用时而非进程启动时）。
- complete() 返回模型原始文本；JSON 解析（extract_json）由调用方按各自
  期望（analyzer 要数组 / intent 要对象）处理。
- deep_probe() 是独立的探活语义（openai 用 models.list、claude 发
  max_tokens=1 最小消息、ollama 打 /api/tags），不要并进 complete。
  成功返回 (True, "")，失败直接抛——由 doctor 统一兜成 (False, detail)。
- openai / ollama 的 complete 忽略 max_tokens（保持既有行为：不限制）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

OLLAMA_DEFAULT_BASE = "http://localhost:11434"


def extract_json(text: str) -> Any:
    """Parse JSON from an LLM response, tolerating markdown fences and prose.

    Models often wrap output in ```json ... ``` or add explanatory text, which
    breaks a bare json.loads. Strip common fences first, then fall back to the
    first JSON array/object substring.
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        raise


class _Claude:
    sdk_import: Optional[str] = "anthropic"
    pip_packages = ["anthropic>=0.30.0"]

    def complete(self, *, model: str, api_key: str, base_url: str = "",
                 user_prompt: str, system: Optional[str] = None,
                 max_tokens: int = 8192, timeout: float) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None)
        kwargs = {"system": system} if system is not None else {}
        resp = client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=timeout, **kwargs)
        return resp.content[0].text

    def deep_probe(self, *, model: str, api_key: str, base_url: str = ""):
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None, timeout=30)
        client.messages.create(model=model, max_tokens=1,
                               messages=[{"role": "user", "content": "ping"}])
        return True, ""


class _OpenAI:
    sdk_import: Optional[str] = "openai"
    pip_packages = ["openai>=1.30.0"]

    def complete(self, *, model: str, api_key: str, base_url: str = "",
                 user_prompt: str, system: Optional[str] = None,
                 max_tokens: Optional[int] = None, timeout: float) -> str:
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=base_url or None)
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": user_prompt})
        resp = client.chat.completions.create(model=model, messages=messages, timeout=timeout)
        return resp.choices[0].message.content

    def deep_probe(self, *, model: str, api_key: str, base_url: str = ""):
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=base_url or None, timeout=30)
        client.models.list()
        return True, ""


class _Ollama:
    sdk_import: Optional[str] = None  # 标准库 urllib 直连，无需 SDK
    pip_packages: list[str] = []

    def complete(self, *, model: str, api_key: str = "", base_url: str = "",
                 user_prompt: str, system: Optional[str] = None,
                 max_tokens: Optional[int] = None, timeout: float) -> str:
        import urllib.request
        url = (base_url or OLLAMA_DEFAULT_BASE).rstrip("/") + "/api/chat"
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": user_prompt})
        payload = json.dumps({"model": model, "stream": False,
                              "messages": messages}).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())["message"]["content"]

    def deep_probe(self, *, model: str, api_key: str = "", base_url: str = ""):
        import urllib.request
        url = (base_url or OLLAMA_DEFAULT_BASE).rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=10) as resp:
            resp.read()
        return True, ""


PROVIDERS = {
    "claude": _Claude(),
    "openai": _OpenAI(),
    "ollama": _Ollama(),
}


def get(name: Optional[str]):
    p = PROVIDERS.get(name or "")
    if p is None:
        raise ValueError(f"Unknown AI provider: {name}")
    return p


def complete(provider: str, **kwargs) -> str:
    """按 provider 名分发一次对话调用，返回原始文本。"""
    return get(provider).complete(**kwargs)


def sdk_import_for(provider: str) -> Optional[str]:
    """provider → 运行时 import 的 SDK 模块名（ollama 等无 SDK 则 None）。"""
    p = PROVIDERS.get(provider)
    return p.sdk_import if p else None


def pip_packages_for(provider: str) -> list[str]:
    """provider → setup 需 pip 安装的包（未知后端返回空，不擅自装包）。"""
    p = PROVIDERS.get(provider)
    return list(p.pip_packages) if p else []
