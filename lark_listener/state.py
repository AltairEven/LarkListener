from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

MAX_PROCESSED_IDS = 1000

logger = logging.getLogger("lark_listener")


class State:
    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = str(Path.home() / ".lark_listener" / "state.json")
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
            if data.get("last_poll_time"):
                self.last_poll_time = datetime.fromisoformat(data["last_poll_time"])
            ids = data.get("processed_message_ids", [])
            self._ordered_ids = list(ids)
            self.processed_message_ids = set(ids)
        except (json.JSONDecodeError, ValueError, OSError) as e:
            # Corrupt or unreadable state must not crash startup — start fresh.
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
        # Sync in-memory set with capped list
        self.processed_message_ids = set(capped)

    def add_processed_ids(self, ids: list[str]):
        for msg_id in ids:
            if msg_id not in self.processed_message_ids:
                self._ordered_ids.append(msg_id)
                self.processed_message_ids.add(msg_id)

    def is_processed(self, msg_id: str) -> bool:
        return msg_id in self.processed_message_ids
