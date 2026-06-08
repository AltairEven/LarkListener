from __future__ import annotations

import copy
import os
import yaml
from pathlib import Path
from typing import Any, Optional

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


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """Load config from YAML file, applying defaults for missing fields."""
    if path is None:
        _home = os.environ.get("LARK_LISTENER_HOME")
        base = Path(_home).expanduser() if _home else Path.home() / ".lark_listener"
        path = str(base / "config.yaml")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}

    config = _deep_merge(DEFAULTS, user_config)
    _validate(config)
    return config
