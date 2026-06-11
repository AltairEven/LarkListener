from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from lark_listener.binaries import lark_cli
from lark_listener.chats import ChatClass

logger = logging.getLogger("lark_listener")


class MessageCategory(Enum):
    P2P = "p2p"
    AT_ME = "at_me"
    KEYWORD = "keyword"
    AT_ALL = "at_all"
    SPECIAL = "special"


def _chunked(seq: list, n: int) -> list:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


# 合并抓取的每批会话数：限制单次 --chat-id 长度与单调用分页预算。
_CHAT_BATCH = 10


# 机器人应用名的模块级成功缓存（app_id → app_name）：应用名基本不变，
# 守护进程生命周期内每个 app 只查一次。
_APP_NAME_CACHE: dict[str, str] = {}


class Fetcher:
    def __init__(self, keywords: Optional[list[str]] = None,
                 registry=None, special_max_messages: int = 20):
        self.keywords = keywords or []
        # registry=None（降级/兼容态）→ 群一律按勿扰、无特别关注抓取。
        self.registry = registry
        self.special_max_messages = special_max_messages
        # 实例级失败记录：权限未批（210508）等失败本轮不重试，下轮新实例再试。
        self._app_name_failed: set[str] = set()

    def fetch(
        self,
        start: datetime,
        end: datetime,
        processed_ids: set[str],
        exclude_chat_ids: Optional[set[str]] = None,
    ) -> dict[MessageCategory, list[dict[str, Any]]]:
        seen_ids: set[str] = set(processed_ids)
        _exclude = exclude_chat_ids or set()
        result = {cat: [] for cat in MessageCategory}

        # Priority order: P2P > AT_ME > AT_ALL > SPECIAL > KEYWORD
        # 1. Private messages
        p2p_msgs = self._search(start, end, chat_type="p2p")
        for msg in p2p_msgs:
            mid = msg["message_id"]
            if mid not in seen_ids and msg.get("chat_id") not in _exclude:
                result[MessageCategory.P2P].append(msg)
                seen_ids.add(mid)

        # 2. @me / @all messages in groups
        at_msgs = self._search(start, end, chat_type="group", is_at_me=True)
        for msg in at_msgs:
            mid = msg["message_id"]
            if mid in seen_ids or msg.get("chat_id") in _exclude:
                continue
            content = msg.get("content", "")
            # "@_all" 是飞书原始 content 的 @所有人 占位符（搜索 API 的
            # is_at_me 把 @所有人 也算「@我」返回）。
            is_at_all = ("@everyone" in content or "@所有人" in content
                         or "@all" in content or "@_all" in content)
            if is_at_all and self._classify(msg) is ChatClass.MUTED:
                # 勿扰群 @所有人：仅命中关键词才收——跳过且不标 seen，
                # 留给关键词搜索捞（命中即归关键词区）。
                continue
            cat = MessageCategory.AT_ALL if is_at_all else MessageCategory.AT_ME
            result[cat].append(msg)
            seen_ids.add(mid)

        # 3. Keyword matches
        for keyword in self.keywords:
            kw_msgs = self._search(start, end, query=keyword)
            for msg in kw_msgs:
                mid = msg["message_id"]
                if mid not in seen_ids and msg.get("chat_id") not in _exclude:
                    if self._classify(msg) is ChatClass.SPECIAL:
                        # 归类优先级：特别关注 > 关键词——特别关注群的命中
                        # 消息由下方全量抓取统一认领（不标 seen）。
                        continue
                    msg["matched_keyword"] = keyword
                    result[MessageCategory.KEYWORD].append(msg)
                    seen_ids.add(mid)

        # 4. 特别关注群全量抓取（合并调用：chat_id 逗号分隔，每批 _CHAT_BATCH 个）
        special_ids = [cid for cid in
                       (self.registry.special_chat_ids() if self.registry else [])
                       if cid not in _exclude]
        for chunk in _chunked(special_ids, _CHAT_BATCH):
            msgs = self._search(start, end, chat_id=",".join(chunk))
            by_chat: dict[str, list] = {}
            for m in msgs:
                if m["message_id"] in seen_ids or m.get("chat_id") in _exclude:
                    continue
                by_chat.setdefault(m.get("chat_id") or "unknown", []).append(m)
            for cid, chat_msgs in by_chat.items():
                chat_msgs.sort(key=lambda m: m.get("create_time", ""))
                dropped = len(chat_msgs) - self.special_max_messages
                if dropped > 0:
                    # no silent caps：截断必须留痕
                    logger.info("特别关注群 %s 本轮 %d 条超出上限 %d，丢弃最早 %d 条",
                                cid, len(chat_msgs), self.special_max_messages, dropped)
                    chat_msgs = chat_msgs[-self.special_max_messages:]
                for m in chat_msgs:
                    result[MessageCategory.SPECIAL].append(m)
                    seen_ids.add(m["message_id"])

        # Fill in missing chat names for group messages
        self._fill_chat_names(result)
        # Fill in bot app names for app senders (p2p bot chats have no other name source)
        self._fill_app_sender_names(result)

        return result

    def fetch_context(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch surrounding messages for each chat to provide AI context.

        合并抓取：所有目标会话 chat_id 逗号分隔、每批 _CHAT_BATCH 个一次调用，
        拉回后本地按 chat 分组、各截最近 limit 条。特别关注群跳过——其窗口
        全量已在 SPECIAL 类别里，再拉一遍纯属浪费。

        已知权衡：合并调用共享单次 --page-all 分页预算——批内某个话痨群消息
        极多时，会挤占同批安静群的上下文（静默变少/为空）。上下文是 AI 的
        辅助信息（非主消息），且每批仅 10 群，可接受；如需隔离回退逐群调用。"""
        special_chats = {m.get("chat_id")
                         for m in categorized.get(MessageCategory.SPECIAL, [])}
        chat_matched_ids: dict[str, set[str]] = {}
        for msgs in categorized.values():
            for msg in msgs:
                chat_id = msg.get("chat_id", "")
                if chat_id and chat_id not in special_chats:
                    chat_matched_ids.setdefault(chat_id, set()).add(msg["message_id"])

        context: dict[str, list[dict[str, Any]]] = {}
        # sorted：分块组合确定，单测可断言每批的 chat_id 组成
        for chunk in _chunked(sorted(chat_matched_ids), _CHAT_BATCH):
            all_msgs = self._search(start, end, chat_id=",".join(chunk))
            by_chat: dict[str, list[dict[str, Any]]] = {}
            for m in all_msgs:
                by_chat.setdefault(m.get("chat_id", ""), []).append(m)
            for chat_id in chunk:
                ctx_msgs = [m for m in by_chat.get(chat_id, [])
                            if m["message_id"] not in chat_matched_ids[chat_id]]
                ctx_msgs.sort(key=lambda m: m.get("create_time", ""))
                ctx_msgs = ctx_msgs[-limit:]
                if ctx_msgs:
                    context[chat_id] = ctx_msgs
        return context

    def _classify(self, msg: dict) -> ChatClass:
        if self.registry is None:
            return (ChatClass.MUTED if msg.get("chat_type") == "group"
                    else ChatClass.NORMAL)
        return self.registry.classify(msg.get("chat_id") or "",
                                      msg.get("chat_type", ""))

    def _fill_chat_names(self, result: dict[MessageCategory, list[dict[str, Any]]]):
        """Look up chat names for group messages missing chat_name."""
        missing_ids: set[str] = set()
        for cat in (MessageCategory.AT_ME, MessageCategory.AT_ALL,
                    MessageCategory.KEYWORD, MessageCategory.SPECIAL):
            for msg in result[cat]:
                if not msg.get("chat_name") and msg.get("chat_id"):
                    missing_ids.add(msg["chat_id"])

        if not missing_ids:
            return

        # Batch lookup chat names
        name_map: dict[str, str] = {}
        for chat_id in missing_ids:
            name = self._get_chat_name(chat_id)
            if name:
                name_map[chat_id] = name

        # Apply names back
        for cat in (MessageCategory.AT_ME, MessageCategory.AT_ALL,
                    MessageCategory.KEYWORD, MessageCategory.SPECIAL):
            for msg in result[cat]:
                if not msg.get("chat_name") and msg.get("chat_id") in name_map:
                    msg["chat_name"] = name_map[msg["chat_id"]]

    def _fill_app_sender_names(self, result: dict[MessageCategory, list[dict[str, Any]]]):
        """给机器人（app）发送者补名字：app 发送者的消息天然没有 sender.name，
        p2p 机器人会话的标题会因此退化。真名只存在于应用信息 API；
        未授权/失败时静默跳过（notifier 有可读回退），best-effort 不抛。"""
        for msgs in result.values():
            for m in msgs:
                sender = m.get("sender", {})
                if sender.get("name"):
                    continue
                if sender.get("sender_type") != "app" and sender.get("id_type") != "app_id":
                    continue
                app_id = sender.get("id", "")
                if not app_id:
                    continue
                name = self._get_app_name(app_id)
                if name:
                    sender["name"] = name

    def _get_app_name(self, app_id: str) -> Optional[str]:
        """查机器人应用名（应用信息 API，bot 身份，需 admin:app.info:readonly）。

        该接口仅收 tenant_access_token（user token 报 99991668）；lang 为必填
        query 参数，须经 --params 传递（lark-cli ≥1.0.50，直接拼 ?lang= 会被丢弃）。
        成功进模块级缓存；失败记实例级缓存（本轮不重试），绝不抛。"""
        if app_id in _APP_NAME_CACHE:
            return _APP_NAME_CACHE[app_id]
        if app_id in self._app_name_failed:
            return None
        try:
            proc = subprocess.run(
                lark_cli("api", "get",
                         f"/open-apis/application/v6/applications/{app_id}",
                         "--params", '{"lang":"zh_cn"}',
                         "--as", "bot",
                         "--jq", ".data.app.app_name"),
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                name = proc.stdout.strip().strip('"')
                if name and name != "null":
                    _APP_NAME_CACHE[app_id] = name
                    return name
        except Exception:
            pass
        self._app_name_failed.add(app_id)
        return None

    def _get_chat_name(self, chat_id: str) -> Optional[str]:
        """Get chat name via lark-cli, trying user then bot identity."""
        for identity in ("user", "bot"):
            try:
                proc = subprocess.run(
                    lark_cli("im", "chats", "get",
                             "--params", json.dumps({"chat_id": chat_id}),
                             "--as", identity,
                             "--jq", ".data.name"),
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    name = proc.stdout.strip().strip('"')
                    if name and name != "null":
                        return name
            except Exception:
                continue
        return None

    def _search(
        self,
        start: datetime,
        end: datetime,
        chat_type: Optional[str] = None,
        is_at_me: bool = False,
        query: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        args = [
            "im", "+messages-search",
            "--start", start.replace(microsecond=0).isoformat(),
            "--end", end.replace(microsecond=0).isoformat(),
            "--format", "json",
            "--page-all",
        ]
        if chat_type:
            args.extend(["--chat-type", chat_type])
        if is_at_me:
            args.append("--is-at-me")
        if query:
            args.extend(["--query", query])
        if chat_id:
            args.extend(["--chat-id", chat_id])
        cmd = lark_cli(*args)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError):
            return []
        if proc.returncode != 0:
            return []

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            # lark-cli succeeded but emitted non-JSON (warning, empty, etc.)
            return []
        if not data.get("ok"):
            return []

        # lark-cli wraps results in data.messages or data.items
        # `or {}` 而非 .get 默认值：真实响应见过 `"data": null`。
        inner = data.get("data") or {}
        if not isinstance(inner, dict):
            return []
        msgs = inner.get("messages", inner.get("items", []))
        if not isinstance(msgs, list):
            return []
        # 入口统一过滤缺 message_id / 非 dict 的脏消息：下游 fetch/analyzer/
        # poll_once 多处裸取 message_id，且都发生在 state 推进之前——一条脏
        # 消息会让 last_poll_time 冻结、同窗每轮重拉（毒消息循环）。
        return [m for m in msgs if isinstance(m, dict) and m.get("message_id")]
