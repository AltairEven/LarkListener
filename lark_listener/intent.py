from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from lark_listener.config_editor import PROTECTED

logger = logging.getLogger("lark_listener")
TZ = timezone(timedelta(hours=8))

INTENT_PROMPT = """\
当前时间：{now}
当前可修改的配置（ai 与 notify 受保护，需手动配置，不在此列出、也不可通过 bot 修改）：
{config_json}

你是消息助手的意图识别模块。用户给 Bot 发了一条消息，请判断意图，严格输出 JSON，不要输出其他内容。

type 取值：
- "summary"：用户想汇总/总结消息。可带 start_time（ISO 8601，带 +08:00 时区），未指定时间则为 null。
- "config_view"：用户想查看当前配置。
- "config_modify"：用户想修改配置。需输出 changes 数组，每项为
  {{"field": 字段名, "op": "set"|"add"|"remove", "value": 值}}。
  列表字段（如 keywords）用 add/remove/set；标量字段（如 poll_interval）用 set。
  注意：ai 和 notify 不可修改，若用户想改这两个，仍按 config_modify 输出，由后续逻辑拒绝。
- "config_help"：用户想了解能改什么、怎么用。
- "confirm"：用户确认（如"确认""是""好的"）。
- "cancel"：用户取消（如"取消""不要了"）。
- "none"：以上都不是。

用户消息："{message}"

示例：
- "汇总今天上午的消息" → {{"type": "summary", "start_time": "{today}T00:00:00+08:00"}}
- "总结一下" → {{"type": "summary", "start_time": null}}
- "轮询间隔改成10分钟" → {{"type": "config_modify", "changes": [{{"field": "poll_interval", "op": "set", "value": 600}}]}}
- "关注关键词 上线" → {{"type": "config_modify", "changes": [{{"field": "keywords", "op": "add", "value": "上线"}}]}}
- "当前配置" → {{"type": "config_view"}}
- "确认" → {{"type": "confirm"}}
- "你好" → {{"type": "none"}}"""


@dataclass
class Intent:
    type: str
    start_time: Optional[datetime] = None
    changes: Optional[list] = None


def _editable_config(config: dict) -> dict:
    """Only the editable (non-protected) fields are sent to the AI.

    ai / notify must be configured manually — they are never parsed or set via
    the bot, so their values (api_key, user_id, bot_chat_id) never leave for the
    AI provider.
    """
    return {k: v for k, v in config.items() if k not in PROTECTED}


def _call_ai(prompt: str, ai_cfg: dict) -> str:
    """Return the raw text response from the configured provider."""
    provider = ai_cfg.get("provider")
    api_key = ai_cfg.get("api_key", "")
    if provider == "openai":
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=ai_cfg.get("base_url") or None)
        resp = client.chat.completions.create(
            model=ai_cfg["model"], messages=[{"role": "user", "content": prompt}])
        return resp.choices[0].message.content
    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=ai_cfg["model"], max_tokens=512,
            messages=[{"role": "user", "content": prompt}])
        return resp.content[0].text
    if provider == "ollama":
        import urllib.request
        url = (ai_cfg.get("base_url") or "http://localhost:11434") + "/api/chat"
        payload = json.dumps({
            "model": ai_cfg["model"], "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["message"]["content"]
    raise ValueError(f"Unknown provider: {provider}")


def parse(message: str, config: dict) -> Intent:
    """Classify a bot message into an Intent. Falls back to type='none' on any error."""
    ai_cfg = config["ai"]
    now = datetime.now(TZ)
    prompt = INTENT_PROMPT.format(
        now=now.isoformat(),
        today=now.strftime("%Y-%m-%d"),
        config_json=json.dumps(_editable_config(config), ensure_ascii=False, indent=2),
        message=message,
    )
    try:
        result = json.loads(_call_ai(prompt, ai_cfg))
    except Exception:
        logger.exception("Failed to parse intent from message: %s", message)
        return Intent(type="error")

    itype = result.get("type", "none")
    if itype == "summary":
        start = None
        if result.get("start_time"):
            try:
                start = datetime.fromisoformat(result["start_time"])
            except (ValueError, TypeError):
                logger.warning("Invalid start_time from AI: %r", result.get("start_time"))
        return Intent(type="summary", start_time=start)
    if itype == "config_modify":
        return Intent(type="config_modify", changes=result.get("changes") or [])
    if itype in ("config_view", "config_help", "confirm", "cancel"):
        return Intent(type=itype)
    return Intent(type="none")
