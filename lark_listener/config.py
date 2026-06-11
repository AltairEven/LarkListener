from __future__ import annotations

import copy
import logging
import yaml
from pathlib import Path
from typing import Any, Optional

from lark_listener.common import listener_home

logger = logging.getLogger("lark_listener")

DEFAULTS = {
    "poll_interval": 300,
    "include_at_all": True,
    "context_messages": 20,
    # Optional list fields default to [] so they're always present in the
    # effective config — this lets the bot add the first entry instead of
    # rejecting them as unknown fields.
    "keywords": [],
    "exclude_chat_ids": [],
    "ai": {
        "base_url": "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate(config: dict) -> None:
    """Raise a clear error for missing required fields instead of a later KeyError."""
    notify = config.get("notify")
    if not isinstance(notify, dict) or not notify.get("user_id"):
        raise ValueError(
            "配置缺少必需字段 notify.user_id，请检查 ~/.lark_listener/config.yaml"
        )
    if not notify.get("bot_chat_id"):
        # poll_once 推送时用下标取 bot_chat_id（main.py），缺失会在抓取+分析之后才
        # KeyError 崩溃，使 last_poll_time 不前进、陷入重复轮询。启动期就拦下。
        raise ValueError(
            "配置缺少必需字段 notify.bot_chat_id，请检查 ~/.lark_listener/config.yaml"
        )
    ai = config.get("ai")
    if not isinstance(ai, dict) or not ai.get("provider") or not ai.get("model"):
        raise ValueError(
            "配置缺少必需字段 ai.provider / ai.model，请检查 ~/.lark_listener/config.yaml"
        )
    if not config.get("lark_cli_appid"):
        raise ValueError(
            "配置缺少必需字段 lark_cli_appid（承载服务的 lark-cli bot appId，"
            "见 `lark-cli profile list`），请检查 ~/.lark_listener/config.yaml"
        )


def _normalize_poll_interval(value: Any) -> int:
    """钳制 poll_interval 为非负 int（0=关闭自动轮询）。

    run 循环 / _poll_wait_timeout / _startup_message / doctor / poll_once 窗口
    计算都直接对它做数值比较——手编 config.yaml 留下 null/字符串/负数时，与其
    抛 TypeError 把服务打进 launchd KeepAlive 崩溃重启循环，不如在这个所有
    消费点共同经过的咽喉钳制并告警。"""
    if isinstance(value, bool):  # bool 是 int 子类，true/false 视为非法
        logger.warning("poll_interval 配置非法（%r），已回退默认 %s", value, DEFAULTS["poll_interval"])
        return DEFAULTS["poll_interval"]
    try:
        n = int(value)
    except (TypeError, ValueError):
        logger.warning("poll_interval 配置非法（%r），已回退默认 %s", value, DEFAULTS["poll_interval"])
        return DEFAULTS["poll_interval"]
    if n < 0:
        logger.warning("poll_interval 为负（%r），按 0（关闭自动轮询）处理", value)
        return 0
    return n


def _normalize_nonneg_int(value: Any, name: str, default: int) -> int:
    """钳制为非负 int，坏值回退默认（与 _normalize_poll_interval 同因：手编
    config 留下 null/字符串时，消费点的数值比较会 TypeError 崩掉每轮 poll）。"""
    if isinstance(value, bool):
        logger.warning("%s 配置非法（%r），已回退默认 %s", name, value, default)
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        logger.warning("%s 配置非法（%r），已回退默认 %s", name, value, default)
        return default
    if n < 0:
        # 负数按 0 处理（与 poll_interval 的负数语义一致）：用户写 -1 的
        # 意图显然是「关掉」，回退默认 20 反而违背意图。
        logger.warning("%s 为负（%r），按 0 处理", name, value)
        return 0
    return n


def _normalize_str_list(value: Any, name: str) -> list[str]:
    """钳制为 list[str]。null → []；标量 → 单元素列表（用户写 `keywords: 上线`
    的意图是一个关键词，绝不能被消费点逐字符迭代）；列表内 None 丢弃、其余转 str。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if x is not None]
    logger.warning("%s 配置应为列表（实际 %r），已按单元素列表处理", name, value)
    return [str(value)]


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """Load config from YAML file, applying defaults for missing fields."""
    if path is None:
        path = str(listener_home() / "config.yaml")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}

    config = _deep_merge(DEFAULTS, user_config)
    config["poll_interval"] = _normalize_poll_interval(config.get("poll_interval"))
    config["context_messages"] = _normalize_nonneg_int(
        config.get("context_messages"), "context_messages", DEFAULTS["context_messages"])
    config["keywords"] = _normalize_str_list(config.get("keywords"), "keywords")
    config["exclude_chat_ids"] = _normalize_str_list(
        config.get("exclude_chat_ids"), "exclude_chat_ids")
    _validate(config)
    return config
