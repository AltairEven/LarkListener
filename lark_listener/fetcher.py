from __future__ import annotations

import json
import subprocess
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from lark_listener.binaries import lark_cli


class MessageCategory(Enum):
    P2P = "p2p"
    AT_ME = "at_me"
    KEYWORD = "keyword"
    AT_ALL = "at_all"


# 机器人应用名的模块级成功缓存（app_id → app_name）：应用名基本不变，
# 守护进程生命周期内每个 app 只查一次。
_APP_NAME_CACHE: dict[str, str] = {}


class Fetcher:
    def __init__(self, keywords: Optional[list[str]] = None, include_at_all: bool = True):
        self.keywords = keywords or []
        self.include_at_all = include_at_all
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

        # Priority order: P2P > AT_ME > KEYWORD
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
            if mid not in seen_ids and msg.get("chat_id") not in _exclude:
                content = msg.get("content", "")
                is_at_all = "@everyone" in content or "@所有人" in content or "@all" in content
                if is_at_all and not self.include_at_all:
                    # Skip AT_ALL but don't mark as seen,
                    # so keyword search can still pick it up
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
                    msg["matched_keyword"] = keyword
                    result[MessageCategory.KEYWORD].append(msg)
                    seen_ids.add(mid)

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
        """Fetch surrounding messages for each chat to provide AI context."""
        # Collect chat_ids and their matched message_ids
        chat_matched_ids: dict[str, set[str]] = {}
        for msgs in categorized.values():
            for msg in msgs:
                chat_id = msg.get("chat_id", "")
                if chat_id:
                    chat_matched_ids.setdefault(chat_id, set()).add(msg["message_id"])

        context: dict[str, list[dict[str, Any]]] = {}
        for chat_id, matched_ids in chat_matched_ids.items():
            all_msgs = self._search(start, end, chat_id=chat_id)
            # Keep up to `limit` most recent messages, excluding already matched
            # ones. Sort by time before truncating so we keep the latest, not
            # whatever order lark-cli happened to return.
            ctx_msgs = [m for m in all_msgs if m["message_id"] not in matched_ids]
            ctx_msgs.sort(key=lambda m: m.get("create_time", ""))
            ctx_msgs = ctx_msgs[-limit:]
            if ctx_msgs:
                context[chat_id] = ctx_msgs

        return context

    def _fill_chat_names(self, result: dict[MessageCategory, list[dict[str, Any]]]):
        """Look up chat names for group messages missing chat_name."""
        missing_ids: set[str] = set()
        for cat in (MessageCategory.AT_ME, MessageCategory.AT_ALL, MessageCategory.KEYWORD):
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
        for cat in (MessageCategory.AT_ME, MessageCategory.AT_ALL, MessageCategory.KEYWORD):
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
