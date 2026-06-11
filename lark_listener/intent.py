from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from lark_listener import providers
from lark_listener.common import TZ
from lark_listener.providers import extract_json as _extract_json
from lark_listener.config_editor import PROTECTED

logger = logging.getLogger("lark_listener")

# 意图分类是轻量调用（max_tokens 512），但仍需上限避免 SDK 默认 600s 卡死轮询线程。
INTENT_TIMEOUT = 60

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
  exclude_chats 的 add/remove 值为会话 chat_id（如 "oc_xxx"）；
  special_focus.enabled / special_focus.max_messages 是嵌套标量，field 写点号路径；
  special_focus.chats 不可经 bot 修改（请直接编辑配置文件）。
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
    the bot, so their secrets (api_key, user_id) never leave for the AI
    provider. Note: exclude_chats IS editable and may contain the bot chat's
    oc_xxx (setup puts it there) — a chat id alone is low-sensitivity and this
    is accepted.
    """
    return {k: v for k, v in config.items() if k not in PROTECTED}


def _call_ai(prompt: str, ai_cfg: dict) -> str:
    """Return the raw text response from the configured provider.

    分发与各后端实现统一在 providers.py；意图分类是轻量调用——claude 限
    max_tokens=512（openai/ollama 不限，与既有行为一致），无 system prompt。"""
    return providers.complete(
        ai_cfg.get("provider"),
        model=ai_cfg["model"], api_key=ai_cfg.get("api_key", ""),
        base_url=ai_cfg.get("base_url") or "", user_prompt=prompt,
        max_tokens=512, timeout=INTENT_TIMEOUT)


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
        # 复用 analyzer 的容错解析：容忍 ```json 围栏与前后说明文字（本地小模型常见），
        # 否则会误判为 error 回「没太听懂」。
        result = _extract_json(_call_ai(prompt, ai_cfg))
    except Exception:
        logger.exception("Failed to parse intent from message: %s", message)
        return Intent(type="error")

    if not isinstance(result, dict):
        # 模型回了数组/标量而非对象 → 当作无法识别，避免下面 .get 抛异常。
        return Intent(type="none")

    itype = result.get("type", "none")
    if itype == "summary":
        start = None
        if result.get("start_time"):
            try:
                start = datetime.fromisoformat(result["start_time"])
                # 本地模型常给不带时区的串；统一钉到 +08:00，否则与 aware 的 end
                # 混用会让 lark-cli 搜索区间整体偏移 8 小时。
                if start.tzinfo is None:
                    start = start.replace(tzinfo=TZ)
            except (ValueError, TypeError):
                logger.warning("Invalid start_time from AI: %r", result.get("start_time"))
        return Intent(type="summary", start_time=start)
    if itype == "config_modify":
        return Intent(type="config_modify", changes=result.get("changes") or [])
    if itype in ("config_view", "config_help", "confirm", "cancel"):
        return Intent(type=itype)
    return Intent(type="none")
