from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

MAX_PROCESSED_IDS = 1000
from lark_listener.common import TZ, listener_home

logger = logging.getLogger("lark_listener")


class State:
    def __init__(self, path: Optional[str] = None):
        if path is None:
            # 经 common.listener_home() 推导：尊重 LARK_LISTENER_HOME（dev 隔离），
            # 此前硬编码 ~/.lark_listener 会让裸 State() 写穿生产。
            path = str(listener_home() / "state.json")
        self._path = Path(path)
        self.last_poll_time: Optional[datetime] = None
        self.processed_message_ids: set[str] = set()
        self._ordered_ids: list[str] = []
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"state.json 顶层应为对象，实际 {type(data).__name__}")
            if data.get("last_poll_time"):
                dt = datetime.fromisoformat(data["last_poll_time"])
                # 归一到 +08:00：旧版/手写的 naive 串若与 aware 的 now 相减/比较会
                # TypeError。正常落盘的是 aware，这里只兜底 naive。
                self.last_poll_time = dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt
            ids = data.get("processed_message_ids", [])
            self._ordered_ids = list(ids)
            self.processed_message_ids = set(ids)
        except Exception as e:  # noqa: BLE001
            # Corrupt or unreadable state must not crash startup — start fresh.
            # 宽捕获是有意的：State 每轮 poll 都会构造，任何形状的坏文件（顶层非
            # 对象 → AttributeError、last_poll_time 为数字 → TypeError）若逃逸，
            # 每一轮都会失败、窗口永不推进，比丢状态严重得多。
            logger.warning("State file unreadable (%s), starting fresh: %s", self._path, e)
            self.last_poll_time = None
            self._ordered_ids = []
            self.processed_message_ids = set()

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        capped = self._ordered_ids[-MAX_PROCESSED_IDS:]
        data = {
            "last_poll_time": self.last_poll_time.isoformat() if self.last_poll_time else None,
            "processed_message_ids": capped,
        }
        # Atomic write: write to a temp file then replace, so a crash mid-write
        # can never leave a half-written (corrupt) state.json behind.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)
        # Sync in-memory set AND list with the capped view: leaving _ordered_ids
        # untruncated grows without bound, and an id evicted from the set would
        # be appended a second time on its next add_processed_ids.
        self.processed_message_ids = set(capped)
        self._ordered_ids = list(capped)

    def add_processed_ids(self, ids: list[str]):
        for msg_id in ids:
            if msg_id not in self.processed_message_ids:
                self._ordered_ids.append(msg_id)
                self.processed_message_ids.add(msg_id)

    def is_processed(self, msg_id: str) -> bool:
        return msg_id in self.processed_message_ids
