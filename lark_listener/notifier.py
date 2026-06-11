from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any, Optional

from lark_listener.analyzer import ConversationAnalysis, format_msg_content
from lark_listener.binaries import lark_cli, resolve_executable
from lark_listener.fetcher import MessageCategory

logger = logging.getLogger("lark_listener")


def _applescript_escape(s: str) -> str:
    """Escape a string for embedding in an AppleScript double-quoted literal.

    Backslash must be escaped first, otherwise the backslashes we add for quotes
    would themselves be re-escaped.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _chat_link(chat_id: str) -> str:
    return f"https://applink.feishu.cn/client/chat/open?openChatId={chat_id}"


def _group_by_chat(
    categorized: dict[MessageCategory, list[dict[str, Any]]],
) -> dict[str, dict]:
    """Group messages by chat_id, preserving category info."""
    groups: dict[str, dict] = {}
    for cat, msgs in categorized.items():
        for msg in msgs:
            # `or` 而非 .get 默认值：真实数据见过 chat_id 显式为 null，
            # None 流进 _conversation_row 的 chat_id[-8:] 会 TypeError。
            chat_id = msg.get("chat_id") or "unknown"
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
            # _conversation_row directly from the messages).
            if cat != MessageCategory.P2P and msg.get("chat_name"):
                groups[chat_id]["chat_name"] = msg["chat_name"]
    return groups


# Category render order, shared by the unified response, the card, and the
# Markdown fallback so every consumer sees the same sectioning.
_CATEGORY_ORDER = [
    (MessageCategory.P2P, "私聊消息"),
    (MessageCategory.AT_ME, "@我"),
    (MessageCategory.KEYWORD, "关键词命中"),
    (MessageCategory.AT_ALL, "@所有人"),
]

# macOS 通知摘要的短名（顺序同 _CATEGORY_ORDER；通知空间有限，
# 「私聊」比卡片分类名「私聊消息」更短）。
_CATEGORY_SHORT = [
    (MessageCategory.P2P, "私聊"),
    (MessageCategory.AT_ME, "@我"),
    (MessageCategory.KEYWORD, "关键词命中"),
    (MessageCategory.AT_ALL, "@所有人"),
]

# 卡片表头用彩色方块区分分类（飞书 table 表头背景实测只支持 none/grey，
# blue/green 等色值 API 收下但静默回退灰色，故用 emoji 实现颜色区分）。
_CATEGORY_EMOJI = {
    "p2p": "🟦",
    "at_me": "🟩",
    "keyword": "🟧",
    "at_all": "🟥",
}


def _partner_title(msgs: list[dict[str, Any]], my_user_id: str) -> str:
    """p2p 会话标题＝对方名字。优先取**有名字**的非我发送者——机器人（app）
    发送者的消息天然无 sender.name（由 fetcher 经应用信息 API 尽力补齐，
    需 admin:app.info:readonly 权限）；全员无名时 app 对端给可读回退
    「机器人(尾号)」，而不是「未知」。"""
    fallback = ""
    for m in msgs:
        s = m.get("sender", {})
        if s.get("id", "") == my_user_id:
            continue
        if s.get("name"):
            return s["name"]
        if not fallback:
            if s.get("sender_type") == "app" or s.get("id_type") == "app_id":
                sid = s.get("id", "")
                fallback = f"机器人({sid[-8:]})" if sid else "机器人"
            else:
                fallback = "未知"
    return fallback or "私聊"


def _conversation_row(
    group: dict,
    label: str,
    analysis: dict[str, ConversationAnalysis],
    my_user_id: str,
) -> dict[str, Any]:
    """Distill one chat group into a plain, JSON-serializable conversation row.

    This is where the per-conversation logic lives (partner-name resolution,
    relevant-message selection, snippet truncation). Card / Markdown / stdout
    all consume these rows so they never diverge.
    """
    cat = group["category"]
    chat_id = group["chat_id"]
    msgs = sorted(group["messages"], key=lambda m: m.get("create_time", ""))

    # p2p 会话（含关键词搜索捞进非 P2P 分类的 p2p 消息——关键词搜索没有
    # chat-type 过滤）一律走对端名解析；p2p 会话对象本身无名称属性，
    # 「群聊(尾号)」对它永远是错的。
    is_p2p_chat = cat == MessageCategory.P2P or (
        not group.get("chat_name") and msgs and msgs[0].get("chat_type") == "p2p"
    )
    if is_p2p_chat:
        title = _partner_title(msgs, my_user_id)
    else:
        title = group.get("chat_name") or f"群聊({chat_id[-8:]})"

    ar = analysis.get(chat_id)

    # Snippet: AI's most-relevant message, else last non-self message.
    snippet = ""
    if ar and ar.relevant_message_id:
        for m in msgs:
            if m.get("message_id") == ar.relevant_message_id:
                snippet = format_msg_content(m, for_display=True)
                break
    if not snippet:
        for m in reversed(msgs):
            if m.get("sender", {}).get("id", "") != my_user_id:
                snippet = format_msg_content(m, for_display=True)
                break
    if len(snippet) > 80:
        snippet = snippet[:80] + "..."

    return {
        "category": cat.value,
        "label": label,
        "title": title,
        "chat_id": chat_id,
        # chat_id 缺失（归并为 unknown 组）时不给链接：openChatId=unknown 是死链。
        "link": _chat_link(chat_id) if chat_id != "unknown" else "",
        "urgency": ar.urgency if ar else "normal",
        "relevance": ar.relevance if ar else "medium",
        "matched_keyword": group.get("matched_keyword", "") if cat == MessageCategory.KEYWORD else "",
        "summary": (ar.summary if ar and ar.summary else ""),
        "snippet": snippet,
        "count": len(group["messages"]),
    }


def build_summary_response(
    categorized: dict[MessageCategory, list[dict[str, Any]]],
    analysis: dict[str, ConversationAnalysis],
    start_time: str,
    end_time: str,
    my_user_id: str = "",
) -> dict[str, Any]:
    """Unified response envelope `{code, errorMsg, data}` — the single source of
    truth every output (stdout JSON, bot card, Markdown fallback) derives from.

    Success is always code 0; an empty/all-self window yields conversations: [].
    """
    groups = _group_by_chat(categorized)
    has_others = any(
        m.get("sender", {}).get("id", "") != my_user_id
        for g in groups.values()
        for m in g["messages"]
    )

    conversations: list[dict[str, Any]] = []
    if has_others:
        for cat, label in _CATEGORY_ORDER:
            cat_groups = [g for g in groups.values() if g["category"] == cat]
            # Urgent conversations first within each category.
            cat_groups.sort(
                key=lambda g: 0 if analysis.get(g["chat_id"]) and analysis[g["chat_id"]].urgency == "urgent" else 1,
            )
            for group in cat_groups:
                conversations.append(_conversation_row(group, label, analysis, my_user_id))

    return {
        "code": 0,
        "errorMsg": "",
        "data": {
            "period": {"start": start_time, "end": end_time},
            "conversations": conversations,
        },
    }


def error_response(msg: str, code: int = 1) -> dict[str, Any]:
    """Error envelope mirroring the command exit code."""
    return {"code": code, "errorMsg": msg, "data": None}


def _title_md(c: dict[str, Any], icon: bool = True) -> str:
    """会话标题（加粗+命中提示，可选紧急图标）。卡片与 Markdown 回退共用。
    卡片定稿（2026-06-10 与用户逐版确认）会话列不带 emoji（icon=False），
    紧急只体现在类内排序；Markdown 回退保留 🔴（既有样式）。
    matched_keyword 由 _conversation_row 保证仅 keyword 类别非空，无需再查类别。"""
    prefix = "🔴 " if icon and c["urgency"] == "urgent" else ""
    kw = f"（命中：{c['matched_keyword']}）" if c.get("matched_keyword") else ""
    return f"{prefix}**{c['title']}**{kw}"


def _envelope_sections(resp: Optional[dict[str, Any]]):
    """解析封套并按 _CATEGORY_ORDER 切段——卡片与 Markdown 回退共用的唯一入口。
    错误/空封套 → None；否则 (period, [(label, rows), …])，rows 非空。"""
    if not resp or resp.get("code") != 0:
        return None
    data = resp.get("data") or {}
    conversations = data.get("conversations") or []
    if not conversations:
        return None
    sections = []
    for cat, label in _CATEGORY_ORDER:
        rows = [c for c in conversations if c["category"] == cat.value]
        if rows:
            sections.append((label, rows))
    return data.get("period", {}), sections


def _render_conversation_md(c: dict[str, Any]) -> str:
    header = f"{_title_md(c)}：“{c['snippet']}”"
    if c["link"]:
        header += f" [查看原文]({c['link']})"
    parts = [header]
    if c["summary"]:
        parts.append(f"*💡 {c['summary']}*")
    return "\n".join(parts)


def build_summary_text(resp: dict[str, Any]) -> str:
    """Markdown fallback rendered from the unified envelope (used when the card
    send fails). Empty/error envelope -> empty string."""
    parsed = _envelope_sections(resp)
    if parsed is None:
        return ""
    period, section_rows = parsed
    sections = [f"📬 **LarkListener 消息汇总（{period.get('start', '')} - {period.get('end', '')}）**\n"]

    rendered_sections = []
    for label, rows in section_rows:
        lines = [f"**━━ {label}（{len(rows)} 个会话）━━**"]
        for c in rows:
            lines.append(_render_conversation_md(c))
        rendered_sections.append("\n\n".join(lines))

    sections.append("\n\n---\n\n".join(rendered_sections))
    return "\n".join(sections).strip()


def _short_snippet(snippet: str, keyword: str = "", limit: int = 20) -> str:
    """卡片用短原文：最多 limit 字。命中关键词时截取含关键词的窗口（关键词
    居中），否则取开头；被截掉的一侧补 ...。只影响卡片展示，封套
    snippet（80 字上限）不变。

    已知局限：matched_keyword 匹配的是原始消息，关键词若已被封套的 80 字
    截断截掉，这里 find 不到，回退取开头 limit 字；原文自身以 "..." 结尾
    （未经封套截断）也会被当作截断标记剥掉——输出视觉等价，可接受。"""
    # 封套截断会带尾部 "..."：先剥掉再开窗，否则窗口腰斩省略号会产生
    # 「正文.....」残渣；截断信息由下方 head/tail 重新表达。
    truncated = snippet.endswith("...")
    base = snippet[:-3] if truncated else snippet
    if len(base) <= limit:
        return base + ("..." if truncated else "")
    idx = base.find(keyword) if keyword else -1
    if idx < 0:
        return base[:limit] + "..."
    start = max(0, min(idx - (limit - len(keyword)) // 2, len(base) - limit))
    end = start + limit
    head = "..." if start > 0 else ""
    tail = "..." if (end < len(base) or truncated) else ""
    return head + base[start:end] + tail


def build_summary_card(resp: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Feishu interactive card (schema 2.0) rendered from the envelope.

    Returns None when there is nothing to show (error envelope or no
    conversations) so the caller simply skips sending.
    """
    parsed = _envelope_sections(resp)
    if parsed is None:
        return None
    period, section_rows = parsed

    # 样式定稿（2026-06-10 测试1-6 逐版与用户确认）：每分类一个表格、无分隔行；
    # 分类标题（emoji+数量）放进「会话」列表头；摘要列只放 AI 摘要（无则 —）；
    # 仅两列：原文片段并入会话列（38% 宽），名称冒号后直接接“原文”，片段
    # 经 _short_snippet 缩到 20 字内、文字本身即跳转链接；row_height auto 换行。
    elements: list[dict[str, Any]] = []
    for label, rows_src in section_rows:
        emoji = _CATEGORY_EMOJI.get(rows_src[0]["category"], "")
        table_rows = []
        for c in rows_src:
            short = _short_snippet(c["snippet"], c.get("matched_keyword", ""))
            if not c["link"]:
                orig = f"“{short}”" if short else "—"
            elif short:
                orig = f"[“{short}”]({c['link']})"
            else:
                orig = f"[查看]({c['link']})"
            table_rows.append({
                "conv": f"{_title_md(c, icon=False)}：{orig}",
                "summ": c["summary"] or "—",
            })
        elements.append({
            "tag": "table",
            "page_size": 10,
            "row_height": "auto",
            "header_style": {"text_align": "left", "background_style": "grey", "bold": True},
            "columns": [
                {"name": "conv", "display_name": f"{emoji} {label}（{len(rows_src)}）",
                 "data_type": "lark_md", "width": "38%"},
                {"name": "summ", "display_name": "摘要", "data_type": "lark_md", "width": "auto"},
            ],
            "rows": table_rows,
        })

    return {
        "schema": "2.0",
        # spike 实测（2026-06-10 真发 DM）：2.0 + wide_screen_mode + table/lark_md
        # 这套组合被 API 接受且渲染正常，改 schema 前需重新真发验证。
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"📬 消息汇总（{period.get('start', '')} - {period.get('end', '')}）"}},
        "body": {"elements": elements},
    }


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
        resp: Optional[dict[str, Any]] = None,
    ):
        # 调用方已持有封套时直接传入（cmd_summarize），保证 stdout 与推送同源、
        # 不重建；poll_once 仍按原签名调用，这里兜底构建。
        if resp is None:
            resp = build_summary_response(categorized, analysis, start_time, end_time, my_user_id)
        card = build_summary_card(resp)
        if card is None:
            return  # nothing worth sending

        # Primary: interactive card with table. Fallback: Markdown text — some
        # tenants may not render the table component; a readable message beats none.
        if not self._send_bot_card(card):
            self._send_bot_message(build_summary_text(resp))
        self._send_macos_notification(categorized)

    def _send_im(self, *payload: str) -> bool:
        """Shared bot-DM send pipeline. Returns True on success.

        Best-effort: a failed send (lark-cli missing, timeout, network, rc!=0)
        must NOT propagate — it would abort poll_once before the caller advances
        last_poll_time, freezing the start time and re-pushing the same summary
        every cycle. Losing one message is better than a duplicate loop."""
        cmd = lark_cli(
            "im", "+messages-send",
            "--user-id", self.user_id,
            *payload,
            "--as", "bot",
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as e:
            logger.warning("Bot message failed to send (%s).", e)
            return False
        if result.returncode != 0:
            logger.warning("Bot message rejected (rc=%s): %s",
                           result.returncode, (result.stderr or "").strip()[:200])
            return False
        return True

    def _send_bot_card(self, card: dict[str, Any]) -> bool:
        """Send the summary as an interactive card; False → caller falls back to Markdown."""
        return self._send_im("--content", json.dumps(card, ensure_ascii=False),
                             "--msg-type", "interactive")

    def _send_bot_message(self, markdown: str):
        self._send_im("--markdown", markdown)

    def _send_macos_notification(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
    ):
        # Count conversations, not messages
        groups = _group_by_chat(categorized)
        # 查表循环：新增消息类别时这里曾是最易漏的硬编码点。
        counts = []
        for cat, short in _CATEGORY_SHORT:
            n = sum(1 for g in groups.values() if g["category"] == cat)
            if n:
                counts.append(f"{n}个{short}")
        message = "、".join(counts)

        open_url = f"https://applink.feishu.cn/client/chat/open?openChatId={self.bot_chat_id}"

        # 优先 terminal-notifier（解析到绝对路径才算装了），它支持点击跳转飞书会话；
        # 否则退回系统原生 osascript（零依赖，但点击不可跳转）。
        tn = resolve_executable("terminal-notifier")
        if os.path.isabs(tn):
            cmd = [
                tn,
                "-title", "LarkListener",
                "-subtitle", "有新消息汇总",
                "-message", message,
                "-open", open_url,
            ]
        else:
            title = _applescript_escape("LarkListener")
            body = _applescript_escape(message)
            cmd = [
                resolve_executable("osascript"),
                "-e", f'display notification "{body}" with title "{title}"',
            ]
        # Best-effort: the desktop toast is a secondary channel (the bot message
        # is the primary delivery and was already sent above). A missing
        # terminal-notifier or any subprocess failure must NOT propagate — it
        # would abort the poll cycle before the caller advances last_poll_time,
        # freezing the summary start time across restarts.
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except Exception as e:
            logger.warning("Desktop notification skipped (%s).", e)
