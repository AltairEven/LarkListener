from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Optional

import yaml

from lark_listener import config as config_mod
from lark_listener.config_editor import (
    PROTECTED, load_roundtrip, dump_roundtrip, _coerce_scalar, _apply_list_op,
)

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


def config_set(key: str, value: str, add: bool = False, remove: bool = False,
               force: bool = False, path: Optional[str | Path] = None) -> int:
    cfg_path = _config_path(path)
    top = key.split(".")[0]
    if top in PROTECTED and not force:
        print(f"❌ {top} 受保护，需加 --force 才能通过 CLI 修改")
        return 1
    if add and remove:
        print("❌ --add 与 --remove 不能同时使用")
        return 1
    try:
        data = load_roundtrip(cfg_path)
    except Exception as e:  # noqa: BLE001
        print(f"❌ 读取配置失败：{e}")
        return 1

    parts = key.split(".")
    container = data
    for part in parts[:-1]:
        if not isinstance(container, dict) or part not in container or not isinstance(container[part], dict):
            print(f"❌ 未知配置路径：{key}")
            return 1
        container = container[part]
    leaf = parts[-1]
    if not isinstance(container, dict) or leaf not in container:
        print(f"❌ 未知配置项：{key}")
        return 1
    current = container[leaf]
    old = current

    if add or remove:
        if not isinstance(current, list):
            print(f"❌ {key} 不是列表，--add/--remove 不适用")
            return 1
        new_value, err = _apply_list_op(current, "add" if add else "remove", value)
    elif isinstance(current, list):
        items = [v.strip() for v in value.split(",") if v.strip()]
        new_value, err = _apply_list_op(current, "set", items)
    else:
        new_value, err = _coerce_scalar(leaf, value, current)
    if err:
        print(f"❌ {err}")
        return 1

    container[leaf] = new_value
    try:
        dump_roundtrip(cfg_path, data)
    except Exception as e:  # noqa: BLE001
        print(f"❌ 写入失败：{e}")
        return 1

    # 写后复验：非法则回滚
    try:
        config_mod.load_config(str(cfg_path))
    except Exception as e:  # noqa: BLE001
        container[leaf] = old
        try:
            dump_roundtrip(cfg_path, data)
        except Exception:  # noqa: BLE001
            pass
        print(f"❌ 校验失败，已回滚：{e}")
        return 1

    print(f"✓ {key}: {old!r} → {new_value!r}（下次轮询生效）")
    return 0
