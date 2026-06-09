from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Optional

import yaml

from lark_listener import config as config_mod

MASK = "***"


def _config_path(path: Optional[str | Path] = None) -> Path:
    if path:
        return Path(path)
    home = os.environ.get("LARK_LISTENER_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".lark_listener"
    return base / "config.yaml"


def _mask(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    ai = out.get("ai")
    if isinstance(ai, dict) and ai.get("api_key"):
        ai["api_key"] = MASK
    return out


def _walk(data, dotted: str):
    """返回 (value, error)。点号路径取值。"""
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, f"未知配置项：{dotted}"
        cur = cur[part]
    return cur, None


def config_get(key: Optional[str] = None, as_json: bool = False,
               path: Optional[str | Path] = None) -> int:
    try:
        cfg = config_mod.load_config(str(_config_path(path)))
    except Exception as e:  # noqa: BLE001
        print(f"❌ 读取配置失败：{e}")
        return 1
    masked = _mask(cfg)
    if key:
        val, err = _walk(masked, key)
        if err:
            print(f"❌ {err}")
            return 1
        out = val
    else:
        out = masked
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif isinstance(out, (dict, list)):
        print(yaml.safe_dump(out, allow_unicode=True, sort_keys=False).rstrip())
    else:
        print(out)
    return 0
