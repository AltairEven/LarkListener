from __future__ import annotations

import json
import logging
import subprocess
from enum import Enum
from typing import Optional

from lark_listener.binaries import lark_cli

logger = logging.getLogger("lark_listener")

# 翻页保险丝：100/页 × 20 = 2000 个未免打扰群封顶，防 API 异常时无限翻页。
_MAX_PAGES = 20


class ChatClass(Enum):
    MUTED = "muted"        # 已免打扰的群：@所有人 仅命中关键词才收
    NORMAL = "normal"      # 未免打扰的群（特别关注关闭时）与 p2p：@所有人 全收
    SPECIAL = "special"    # 特别关注群：窗口内全量收


def classify_chat(
    chat_id: str,
    chat_type: str,
    unmuted_group_ids: Optional[set],
    special_enabled: bool,
) -> ChatClass:
    """分类纯函数核。unmuted_group_ids=None 表示从未成功拉到未免打扰列表
    （首刷失败的降级态）：群一律按勿扰处理（宁可少收不误收）。
    p2p 恒 NORMAL——mute 不影响私聊行为（spec §1）。"""
    if chat_type != "group":
        return ChatClass.NORMAL
    if not unmuted_group_ids or chat_id not in unmuted_group_ids:
        return ChatClass.MUTED
    return ChatClass.SPECIAL if special_enabled else ChatClass.NORMAL


class ChatRegistry:
    """未免打扰群注册表：每轮产出汇总前 refresh 一次（产出时刷新＝等效实时，
    spec §2）。免打扰是用户维度设置，消息搜索与 chats get 均不携带，
    `chat-list --exclude-muted` 是唯一数据源。"""

    def __init__(self, special_enabled: bool = False):
        self.special_enabled = special_enabled
        # chat_id -> name。None 表示从未成功刷新（降级：全按勿扰）。
        self._unmuted: Optional[dict] = None

    def refresh(self) -> bool:
        """拉取未免打扰群列表（带翻页）。失败保留上一轮结果并返回 False。"""
        chats: dict[str, str] = {}
        page_token = ""
        for _ in range(_MAX_PAGES):
            args = ["im", "+chat-list", "--exclude-muted",
                    "--page-size", "100", "--format", "json"]
            if page_token:
                args += ["--page-token", page_token]
            try:
                proc = subprocess.run(lark_cli(*args), capture_output=True,
                                      text=True, timeout=30)
            except Exception:  # noqa: BLE001 — best-effort：失败沿用旧结果
                logger.warning("chat-list --exclude-muted 调用失败，沿用上一轮 mute 状态")
                return False
            if proc.returncode != 0:
                logger.warning("chat-list --exclude-muted 返回失败（rc=%s），沿用上一轮 mute 状态",
                               proc.returncode)
                return False
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                logger.warning("chat-list --exclude-muted 输出非 JSON，沿用上一轮 mute 状态")
                return False
            if not data.get("ok"):
                logger.warning("chat-list --exclude-muted 返回 ok=false，沿用上一轮 mute 状态")
                return False
            inner = data.get("data") or {}
            for c in inner.get("chats") or []:
                if isinstance(c, dict) and c.get("chat_id"):
                    chats[c["chat_id"]] = str(c.get("name") or "")
            page_token = inner.get("page_token") or ""
            if not inner.get("has_more") or not page_token:
                break
        self._unmuted = chats
        return True

    def classify(self, chat_id: str, chat_type: str) -> ChatClass:
        unmuted = set(self._unmuted) if self._unmuted is not None else None
        return classify_chat(chat_id, chat_type, unmuted, self.special_enabled)

    def special_chat_ids(self) -> list:
        """特别关注群 id 列表（开关关闭或无数据时为空）。"""
        if not self.special_enabled or not self._unmuted:
            return []
        return list(self._unmuted)

    def name_of(self, chat_id: str) -> str:
        """群名解析（供配置补名）：优先未免打扰列表，勿扰群回落单群查询。"""
        if self._unmuted and chat_id in self._unmuted:
            return self._unmuted[chat_id]
        try:
            proc = subprocess.run(
                lark_cli("im", "chats", "get", "--params",
                         json.dumps({"chat_id": chat_id}), "--format", "json"),
                capture_output=True, text=True, timeout=30)
            data = json.loads(proc.stdout)
            if proc.returncode == 0 and data.get("ok"):
                return str((data.get("data") or {}).get("name") or "")
        except Exception:  # noqa: BLE001 — 补名失败留空下轮再试
            pass
        return ""
