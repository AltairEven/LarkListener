from __future__ import annotations

import json
import subprocess
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from lark_listener.binaries import resolve_executable


class MessageCategory(Enum):
    P2P = "p2p"
    AT_ME = "at_me"
    KEYWORD = "keyword"
    AT_ALL = "at_all"


class Fetcher:
    def __init__(self, keywords: Optional[list[str]] = None, include_at_all: bool = True):
        self.keywords = keywords or []
        self.include_at_all = include_at_all

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

    def _get_chat_name(self, chat_id: str) -> Optional[str]:
        """Get chat name via lark-cli, trying user then bot identity."""
        for identity in ("user", "bot"):
            try:
                proc = subprocess.run(
                    [resolve_executable("lark-cli"), "im", "chats", "get",
                     "--params", json.dumps({"chat_id": chat_id}),
                     "--as", identity,
                     "--jq", ".data.name"],
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
        cmd = [
            resolve_executable("lark-cli"), "im", "+messages-search",
            "--start", start.replace(microsecond=0).isoformat(),
            "--end", end.replace(microsecond=0).isoformat(),
            "--format", "json",
            "--page-all",
        ]
        if chat_type:
            cmd.extend(["--chat-type", chat_type])
        if is_at_me:
            cmd.append("--is-at-me")
        if query:
            cmd.extend(["--query", query])
        if chat_id:
            cmd.extend(["--chat-id", chat_id])

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
        inner = data.get("data", {})
        return inner.get("messages", inner.get("items", []))
