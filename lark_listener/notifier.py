from __future__ import annotations

import json
import logging
import subprocess
from typing import Any, Optional

from lark_listener.analyzer import ConversationAnalysis, format_msg_content
from lark_listener.binaries import lark_cli, resolve_executable
from lark_listener.fetcher import MessageCategory

logger = logging.getLogger("lark_listener")

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
                    "matched_keyword": msg.get("matched_keyword", ""),
                }
            groups[chat_id]["messages"].append(msg)
            # Capture the group name (p2p partner name is resolved later in
            # _format_conversation directly from the messages).
            if cat != MessageCategory.P2P and msg.get("chat_name"):
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

    # Title bold, content not bold, keyword hint outside bold
    if cat == MessageCategory.KEYWORD and group.get("matched_keyword"):
        name_part = f"{urgency_icon}**{title}**（命中：{group['matched_keyword']}）"
    else:
        name_part = f"{urgency_icon}**{title}**"

    header = f"{name_part}：\u201c{display_content}\u201d [查看原文]({link})"

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
        (MessageCategory.AT_ME, "@我"),
        (MessageCategory.KEYWORD, "关键词命中"),
        (MessageCategory.AT_ALL, "@所有人"),
    ]

    rendered_sections = []
    for cat, label in category_config:
        cat_groups = [g for g in groups.values() if g["category"] == cat]
        if not cat_groups:
            continue

        # Sort: urgent conversations first
        cat_groups.sort(
            key=lambda g: 0 if analysis.get(g["chat_id"]) and analysis[g["chat_id"]].urgency == "urgent" else 1,
        )

        lines = [f"**━━ {label}（{len(cat_groups)} 个会话）━━**"]
        for group in cat_groups:
            ar = analysis.get(group["chat_id"])
            lines.append(_format_conversation(group, ar, my_user_id))
        rendered_sections.append("\n\n".join(lines))

    sections.append("\n\n---\n\n".join(rendered_sections))

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
        cmd = lark_cli(
            "im", "+messages-send",
            "--user-id", self.user_id,
            "--markdown", markdown,
            "--as", "bot",
        )
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
        at_all = sum(1 for g in groups.values() if g["category"] == MessageCategory.AT_ALL)

        counts = []
        if p2p:
            counts.append(f"{p2p}个私聊")
        if at_me:
            counts.append(f"{at_me}个@我")
        if kw:
            counts.append(f"{kw}个关键词命中")
        if at_all:
            counts.append(f"{at_all}个@所有人")
        message = "、".join(counts)

        open_url = f"https://applink.feishu.cn/client/chat/open?openChatId={self.bot_chat_id}"

        cmd = [
            resolve_executable("terminal-notifier"),
            "-title", "LarkListener",
            "-subtitle", "有新消息汇总",
            "-message", message,
            "-open", open_url,
        ]
        # Best-effort: the desktop toast is a secondary channel (the bot message
        # is the primary delivery and was already sent above). A missing
        # terminal-notifier or any subprocess failure must NOT propagate — it
        # would abort the poll cycle before the caller advances last_poll_time,
        # freezing the summary start time across restarts.
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except Exception as e:
            logger.warning(
                "Desktop notification skipped (%s). "
                "Install it with: brew install terminal-notifier",
                e,
            )
