from __future__ import annotations

import json
import subprocess
from typing import Any, Optional

from lark_listener.analyzer import ConversationAnalysis, format_msg_content
from lark_listener.fetcher import MessageCategory

URGENCY_ICON = {
    "urgent": "🔴",
    "normal": "",
    "low": "",
}


def _chat_link(chat_id: str) -> str:
    return f"https://applink.feishu.cn/client/chat/open?openChatId={chat_id}"


def _group_by_chat(
    categorized: dict[MessageCategory, list[dict[str, Any]]],
) -> dict[str, dict]:
    """Group messages by chat_id, preserving category info."""
    groups: dict[str, dict] = {}
    for cat, msgs in categorized.items():
        for msg in msgs:
            chat_id = msg.get("chat_id", "unknown")
            if chat_id not in groups:
                groups[chat_id] = {
                    "chat_id": chat_id,
                    "category": cat,
                    "messages": [],
                    "chat_name": "",
                    "partner_name": "",
                    "matched_keyword": msg.get("matched_keyword", ""),
                }
            groups[chat_id]["messages"].append(msg)
            # Try to get chat/partner name
            if cat == MessageCategory.P2P:
                sender = msg.get("sender", {})
                if sender.get("id") != msg.get("_my_user_id"):
                    groups[chat_id]["partner_name"] = sender.get("name", "")
            else:
                if msg.get("chat_name"):
                    groups[chat_id]["chat_name"] = msg["chat_name"]
    return groups


def _format_conversation(
    group: dict,
    analysis: Optional[ConversationAnalysis],
    my_user_id: str,
) -> str:
    """Format a single conversation group as markdown."""
    cat = group["category"]
    chat_id = group["chat_id"]
    msgs = sorted(group["messages"], key=lambda m: m.get("create_time", ""))
    link = _chat_link(chat_id)

    # Header: person name (p2p) or group name
    if cat == MessageCategory.P2P:
        # Find the other person's name
        partner = ""
        for m in msgs:
            sender_id = m.get("sender", {}).get("id", "")
            if sender_id != my_user_id:
                partner = m.get("sender", {}).get("name", "未知")
                break
        title = partner or "私聊"
    else:
        title = group.get("chat_name") or f"群聊({chat_id[-8:]})"
        if cat == MessageCategory.KEYWORD and group.get("matched_keyword"):
            title += f"（命中：{group['matched_keyword']}）"

    urgency_icon = ""
    if analysis and analysis.urgency == "urgent":
        urgency_icon = "🔴 "

    # Find the most relevant message (from AI), fallback to last non-self message
    display_content = ""
    if analysis and analysis.relevant_message_id:
        for m in msgs:
            if m.get("message_id") == analysis.relevant_message_id:
                display_content = format_msg_content(m, for_display=True)
                break
    if not display_content:
        for m in reversed(msgs):
            sender_id = m.get("sender", {}).get("id", "")
            if sender_id != my_user_id:
                display_content = format_msg_content(m, for_display=True)
                break
    if len(display_content) > 80:
        display_content = display_content[:80] + "..."

    # First line: name + quoted message + link
    header = f"**{urgency_icon}{title}**：**\u201c{display_content}\u201d** [查看原文]({link})"

    # AI analysis in italic
    ai_line = ""
    if analysis and analysis.summary:
        ai_line = f"*💡 {analysis.summary}*"

    parts = [header]
    if ai_line:
        parts.append(ai_line)

    return "\n".join(parts)


def build_summary_text(
    categorized: dict[MessageCategory, list[dict[str, Any]]],
    analysis: dict[str, ConversationAnalysis],
    start_time: str,
    end_time: str,
    my_user_id: str = "",
) -> str:
    groups = _group_by_chat(categorized)
    if not groups:
        return ""

    # Check if there are any non-self messages
    has_others = False
    for g in groups.values():
        for m in g["messages"]:
            if m.get("sender", {}).get("id", "") != my_user_id:
                has_others = True
                break
        if has_others:
            break
    if not has_others:
        return ""

    sections = []
    sections.append(f"📬 **LarkListener 消息汇总（{start_time} - {end_time}）**\n")

    category_config = [
        (MessageCategory.P2P, "私聊消息"),
        (MessageCategory.AT_ME, "@我 / @所有人"),
        (MessageCategory.KEYWORD, "关键词命中"),
    ]

    for cat, label in category_config:
        cat_groups = [g for g in groups.values() if g["category"] == cat]
        if not cat_groups:
            continue

        # Sort: urgent conversations first
        cat_groups.sort(
            key=lambda g: 0 if analysis.get(g["chat_id"]) and analysis[g["chat_id"]].urgency == "urgent" else 1,
        )

        sections.append(f"━━ {label}（{len(cat_groups)} 个会话）━━")
        for idx, group in enumerate(cat_groups):
            ar = analysis.get(group["chat_id"])
            sections.append(_format_conversation(group, ar, my_user_id))
            if idx < len(cat_groups) - 1:
                sections.append("---")
        sections.append("")

    return "\n".join(sections).strip()


class Notifier:
    def __init__(self, user_id: str, bot_chat_id: str):
        self.user_id = user_id
        self.bot_chat_id = bot_chat_id

    def notify(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
        analysis: dict[str, ConversationAnalysis],
        start_time: str,
        end_time: str,
        my_user_id: str = "",
    ):
        text = build_summary_text(categorized, analysis, start_time, end_time, my_user_id)
        if not text:
            return

        self._send_bot_message(text)
        self._send_macos_notification(categorized, my_user_id)

    def _send_bot_message(self, markdown: str):
        cmd = [
            "lark-cli", "im", "+messages-send",
            "--user-id", self.user_id,
            "--markdown", markdown,
            "--as", "bot",
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    def _send_macos_notification(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
        my_user_id: str = "",
    ):
        # Count conversations, not messages
        groups = _group_by_chat(categorized)
        p2p = sum(1 for g in groups.values() if g["category"] == MessageCategory.P2P)
        at_me = sum(1 for g in groups.values() if g["category"] == MessageCategory.AT_ME)
        kw = sum(1 for g in groups.values() if g["category"] == MessageCategory.KEYWORD)

        counts = []
        if p2p:
            counts.append(f"{p2p}个私聊")
        if at_me:
            counts.append(f"{at_me}个@我")
        if kw:
            counts.append(f"{kw}个关键词命中")
        message = "、".join(counts)

        open_url = f"https://applink.feishu.cn/client/chat/open?openChatId={self.bot_chat_id}"

        cmd = [
            "terminal-notifier",
            "-title", "LarkListener",
            "-subtitle", "有新消息汇总",
            "-message", message,
            "-open", open_url,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)
