from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from lark_listener.fetcher import MessageCategory

SYSTEM_PROMPT = "你是消息分析助手。请严格输出 JSON 数组，不要输出其他内容。"

USER_PROMPT_TEMPLATE = """\
用户关注的关键词：{keywords}

以下是按会话分组的消息。标记为 [我] 的是用户自己发的消息，仅作为理解上下文使用。
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
    "interactive": "[卡片消息]",
}


def format_msg_content(msg: dict[str, Any], for_display: bool = False) -> str:
    """Format message content. When for_display=True, hide card/rich content."""
    msg_type = msg.get("msg_type", "text")
    if msg_type in MSG_TYPE_LABELS:
        return MSG_TYPE_LABELS[msg_type]
    content = msg.get("content", "")
    # Card messages: show label for display, keep content for AI analysis
    is_card = msg_type == "interactive" or (content and content.lstrip().startswith("<card"))
    if is_card and for_display:
        return "[卡片消息]"
    content = msg.get("content", "")
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
    ) -> dict[str, ConversationAnalysis]:
        """Analyze messages grouped by conversation (chat_id)."""
        # Group all messages by chat_id
        conversations: dict[str, list[dict[str, Any]]] = {}
        for msgs in categorized.values():
            for msg in msgs:
                chat_id = msg.get("chat_id", "unknown")
                conversations.setdefault(chat_id, []).append(msg)

        if not conversations:
            return {}

        # Build prompt with conversations
        conv_texts = []
        for chat_id, msgs in conversations.items():
            # Sort by create_time
            msgs_sorted = sorted(msgs, key=lambda m: m.get("create_time", ""))
            lines = [f"--- conversation_id: {chat_id} ---"]
            for msg in msgs_sorted:
                sender = msg.get("sender", {}).get("name", "未知")
                sender_id = msg.get("sender", {}).get("id", "")
                is_me = sender_id == my_user_id
                prefix = "[我] " if is_me else ""
                content = format_msg_content(msg)
                lines.append(f"[{msg['message_id']}] {prefix}{sender}: {content}")
            conv_texts.append("\n".join(lines))

        user_prompt = USER_PROMPT_TEMPLATE.format(
            keywords="、".join(self.keywords),
            conversations="\n\n".join(conv_texts),
        )

        raw_results = self._call_ai(user_prompt)

        results = {}
        for item in raw_results:
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

    def _call_claude(self, user_prompt: str) -> list[dict]:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return json.loads(response.content[0].text)

    def _call_openai(self, user_prompt: str) -> list[dict]:
        import openai

        client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url or None,
        )
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return json.loads(response.choices[0].message.content)

    def _call_ollama(self, user_prompt: str) -> list[dict]:
        url = (self.base_url or "http://localhost:11434") + "/api/chat"
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return json.loads(data["message"]["content"])
