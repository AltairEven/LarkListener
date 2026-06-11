from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Optional

from lark_listener import providers
from lark_listener.fetcher import MessageCategory
# 实现已移至 providers.extract_json；别名保留（intent 曾跨模块引用私有名、
# tests/test_analyzer 直接 import _extract_json）。
from lark_listener.providers import extract_json as _extract_json

logger = logging.getLogger("lark_listener")

SYSTEM_PROMPT = "你是消息分析助手。请严格输出 JSON 数组，不要输出其他内容。"

# AI 调用上限（秒）。SDK 默认 600s，在主轮询线程里会让服务对触发/关停长时间无响应；
# 设为略大于 estimate_ai_seconds 的上限（180），超时则按异常降级为无 AI 汇总。
AI_TIMEOUT = 180

USER_PROMPT_TEMPLATE = """\
用户关注的关键词：{keywords}

以下是按会话分组的消息。标记为 [我] 的是用户自己发的消息，标记为 [上下文] 的是前后相关消息，两者仅作为理解上下文使用。
请对每个会话（conversation_id）进行整体分析，输出：
1. conversation_id: 会话 ID
2. relevance: 该会话与关键词的语义相关度（high/medium/low）
3. urgency: 紧急度（urgent/normal/low）
4. summary: 用一两句话概括该会话的核心内容和要点
5. relevant_message_id: 该会话中与关键词最相关的那条消息的 ID（不要选 [我] 的消息），如果都不相关则选最后一条非 [我] 的消息

输出格式为 JSON 数组：
[{{"conversation_id": "...", "relevance": "...", "urgency": "...", "summary": "...", "relevant_message_id": "..."}}]

会话列表：
{conversations}"""

MSG_TYPE_LABELS = {
    "image": "[图片]",
    "file": "[文件]",
    "video": "[视频]",
    "audio": "[语音]",
    "media": "[媒体]",
    "sticker": "[表情]",
    "share_chat": "[群名片]",
    "share_user": "[个人名片]",
    "location": "[位置]",
    "merge_forward": "[合并转发]",
}


def estimate_ai_seconds(num_messages: int) -> int:
    """Rough estimate of AI analysis time in seconds.

    Linear fit from measurements (~10s for 1 message, ~55s for 74), capped at
    180s to avoid absurd estimates for huge batches.
    """
    return min(180, round(10 + 0.6 * num_messages))


def format_duration(seconds: int) -> str:
    """Human-readable duration. Caller adds the '约' prefix where needed."""
    if seconds < 60:
        return f"{seconds} 秒"
    return f"{math.ceil(seconds / 60)} 分钟"


def _parse_card(content: str) -> tuple[str, str]:
    """Extract title and body text from <card title="...">body</card> format.

    Returns (title, body). Either may be empty.
    """
    title = ""
    body = content
    m = re.match(r'<card\s+title="([^"]*)"[^>]*>(.*)</card>', content, re.DOTALL)
    if m:
        title = m.group(1).strip()
        body = m.group(2).strip()
    return title, body


def format_msg_content(msg: dict[str, Any], for_display: bool = False) -> str:
    """Format message content. When for_display=True, hide card/rich content."""
    msg_type = msg.get("msg_type", "text")
    if msg_type in MSG_TYPE_LABELS:
        return MSG_TYPE_LABELS[msg_type]
    content = msg.get("content", "")
    # Card messages
    is_card = msg_type == "interactive" or (content and content.lstrip().startswith("<card"))
    if is_card:
        title, body = _parse_card(content)
        if for_display:
            return f"[卡片] {title}" if title else "[卡片消息]"
        # For AI analysis: return clean text
        return f"[卡片: {title}]\n{body}" if title else body or content
    # Detect inline links/urls in text
    if msg_type == "text" and content:
        return content
    return content or "[未知消息类型]"


@dataclass
class ConversationAnalysis:
    conversation_id: str
    relevance: str
    urgency: str
    summary: str
    relevant_message_id: str = ""


class Analyzer:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        keywords: list[str],
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.keywords = keywords

    def analyze(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
        my_user_id: str = "",
        context: Optional[dict[str, list[dict[str, Any]]]] = None,
    ) -> dict[str, ConversationAnalysis]:
        """Analyze messages grouped by conversation (chat_id)."""
        # Group all messages by chat_id
        matched_ids: set[str] = set()
        conversations: dict[str, list[dict[str, Any]]] = {}
        for msgs in categorized.values():
            for msg in msgs:
                chat_id = msg.get("chat_id", "unknown")
                conversations.setdefault(chat_id, []).append(msg)
                matched_ids.add(msg["message_id"])

        if not conversations:
            return {}

        # Build prompt with conversations
        conv_texts = []
        for chat_id, msgs in conversations.items():
            # Merge matched messages with context messages
            all_msgs = list(msgs)
            if context and chat_id in context:
                for ctx_msg in context[chat_id]:
                    if ctx_msg["message_id"] not in matched_ids:
                        all_msgs.append(ctx_msg)
            # Sort by create_time
            msgs_sorted = sorted(all_msgs, key=lambda m: m.get("create_time", ""))
            lines = [f"--- conversation_id: {chat_id} ---"]
            for msg in msgs_sorted:
                sender = msg.get("sender", {}).get("name", "未知")
                sender_id = msg.get("sender", {}).get("id", "")
                is_me = sender_id == my_user_id
                is_ctx = msg["message_id"] not in matched_ids
                if is_me:
                    prefix = "[我] "
                elif is_ctx:
                    prefix = "[上下文] "
                else:
                    prefix = ""
                content = format_msg_content(msg)
                lines.append(f"[{msg['message_id']}] {prefix}{sender}: {content}")
            conv_texts.append("\n".join(lines))

        user_prompt = USER_PROMPT_TEMPLATE.format(
            keywords="、".join(self.keywords),
            conversations="\n\n".join(conv_texts),
        )

        try:
            raw_results = self._call_ai(user_prompt)
        except Exception:
            # AI failure (network, malformed JSON, etc.) must not drop the whole
            # summary — degrade gracefully to no per-conversation analysis.
            logger.exception("AI analysis failed, sending summary without it")
            return {}

        # 模型本应返回数组，但可能给出 {"results": [...]} 包裹、单个会话对象，或
        # 夹带非 dict 元素。逐种归一后再逐项跳过坏数据，避免一处脏数据丢整批。
        if isinstance(raw_results, dict):
            if isinstance(raw_results.get("results"), list):
                raw_results = raw_results["results"]
            elif raw_results.get("conversation_id"):
                raw_results = [raw_results]  # 裸的单会话对象 → 当作单元素数组
            else:
                raw_results = []
        if not isinstance(raw_results, list):
            logger.warning("AI returned non-list analysis (%s), skipping", type(raw_results).__name__)
            return {}

        results = {}
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            cid = item.get("conversation_id", "")
            results[cid] = ConversationAnalysis(
                conversation_id=cid,
                relevance=item.get("relevance", "medium"),
                urgency=item.get("urgency", "normal"),
                summary=item.get("summary", ""),
                relevant_message_id=item.get("relevant_message_id", ""),
            )
        return results

    def _call_ai(self, user_prompt: str) -> list[dict]:
        if self.provider == "claude":
            return self._call_claude(user_prompt)
        elif self.provider == "openai":
            return self._call_openai(user_prompt)
        elif self.provider == "ollama":
            return self._call_ollama(user_prompt)
        else:
            raise ValueError(f"Unknown AI provider: {self.provider}")

    # 三个 _call_* 保留原名原签名（单测直测它们），实现委托 providers——
    # 新增后端只改 providers.py。max_tokens=8192：4096 在大批量会话时输出
    # JSON 会被截断 → _extract_json 拼不出合法 JSON → 整批分析静默降级为 {}。
    def _call_claude(self, user_prompt: str) -> list[dict]:
        return _extract_json(providers.complete(
            "claude", model=self.model, api_key=self.api_key, base_url=self.base_url,
            user_prompt=user_prompt, system=SYSTEM_PROMPT,
            max_tokens=8192, timeout=AI_TIMEOUT))

    def _call_openai(self, user_prompt: str) -> list[dict]:
        return _extract_json(providers.complete(
            "openai", model=self.model, api_key=self.api_key, base_url=self.base_url,
            user_prompt=user_prompt, system=SYSTEM_PROMPT, timeout=AI_TIMEOUT))

    def _call_ollama(self, user_prompt: str) -> list[dict]:
        return _extract_json(providers.complete(
            "ollama", model=self.model, api_key=self.api_key, base_url=self.base_url,
            user_prompt=user_prompt, system=SYSTEM_PROMPT, timeout=AI_TIMEOUT))
