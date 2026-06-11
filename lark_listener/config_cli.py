from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Optional

import yaml

from lark_listener import config as config_mod
from lark_listener.common import listener_home
from lark_listener.config_editor import (
    PROTECTED, load_roundtrip, dump_roundtrip, _coerce_scalar, _apply_list_op,
    removes_bot_chat,
)

MASK = "***"


def _config_path(path: Optional[str | Path] = None) -> Path:
    if path:
        return Path(path)
    return listener_home() / "config.yaml"


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
    # 手编 `keywords:`（值留空 = null）很常见：已知列表字段（DEFAULTS 中为
    # list）按空列表处理，否则 --add 被误拒、普通 set 会把列表字段写成字符串。
    if current is None and isinstance(config_mod.DEFAULTS.get(leaf), list):
        current = []

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

    # 防自反馈守卫（判断与 bot 路径共用 removes_bot_chat）。CLI 是 owner 的
    # deliberate 操作，--force 保留逃生口。
    if leaf == "exclude_chat_ids" and not force:
        try:
            bot_chat = (data.get("notify") or {}).get("bot_chat_id")
        except Exception:  # noqa: BLE001
            bot_chat = ""
        if removes_bot_chat(bot_chat, current, new_value):
            print("❌ exclude_chat_ids 中的 bot 会话不可移除（防汇总自反馈循环）；"
                  "确需移除请加 --force")
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

    if leaf == "api_key":
        # 与 config_get 的脱敏一致：绝不把密钥明文回显到终端 / AI transcript
        print(f"✓ {key}: 已更新（值已隐藏，下次轮询生效）")
    else:
        print(f"✓ {key}: {old!r} → {new_value!r}（下次轮询生效）")
    return 0
