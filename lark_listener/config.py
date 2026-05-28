from __future__ import annotations

import copy
import yaml
from pathlib import Path
from typing import Any, Optional

DEFAULTS = {
    "poll_interval": 300,
    "include_at_all": True,
    "context_messages": 20,
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


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """Load config from YAML file, applying defaults for missing fields."""
    if path is None:
        path = str(Path.home() / ".lark_listener" / "config.yaml")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}

    return _deep_merge(DEFAULTS, user_config)
