from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

# Infrastructure fields the bot must not change via chat: ai/notify hold secrets
# and ids; lark_cli_appid picks which bot carries the service and only takes
# effect on restart, so it stays a manual, file-only edit.
PROTECTED = {"ai", "notify", "lark_cli_appid"}


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    return y


def load_roundtrip(path: str | Path) -> CommentedMap:
    """Load YAML preserving comments and key order (ruamel CommentedMap)."""
    with open(path, "r", encoding="utf-8") as f:
        return _yaml().load(f)


def dump_roundtrip(path: str | Path, data) -> None:
    """Atomically write the round-trip document back, preserving comments.

    Write to a temp file then os.replace, so a crash mid-write can't leave a
    half-written config behind (mirrors state.py).
    """
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            _yaml().dump(data, f)
        os.replace(tmp, p)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


@dataclass
class ApplyResult:
    ok: bool
    diff: str = ""
    error: str = ""


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "是", "开", "on", "yes"):
            return True
        if v in ("false", "0", "否", "关", "off", "no"):
            return False
    return None


def _coerce_scalar(field, value, current):
    """Coerce an AI-provided value to the field's type. Returns (value, error)."""
    # bool must be checked before int (bool is a subclass of int)
    if isinstance(current, bool) or field == "include_at_all":
        b = _to_bool(value)
        if b is None:
            return None, f"{field} 需要 true/false"
        return b, None
    if isinstance(current, int) or field in ("poll_interval", "context_messages"):
        if isinstance(value, bool):
            return None, f"{field} 需要整数"
        try:
            n = int(value)
        except (ValueError, TypeError):
            return None, f"{field} 需要整数"
        if field == "poll_interval" and n <= 0:
            return None, "poll_interval 需为正整数"
        if field == "context_messages" and n < 0:
            return None, "context_messages 需为非负整数"
        return n, None
    return ("" if value is None else str(value)), None


def _apply_list_op(current, op, value):
    """Returns (new_list, error)."""
    if value is None:
        return None, "列表操作需要提供值"
    items = [str(x) for x in current]
    if op == "set":
        new = value if isinstance(value, list) else [value]
        deduped: list[str] = []
        for x in new:
            s = str(x)
            if s not in deduped:  # 去重保序：避免重复关键词触发重复 lark-cli 搜索
                deduped.append(s)
        return deduped, None
    if op == "add":
        v = str(value)
        if v not in items:
            items.append(v)
        return items, None
    if op == "remove":
        v = str(value)
        return [x for x in items if x != v], None
    return None, f"未知操作 {op}"


def _plan_changes(changes, effective_config):
    """Validate + resolve changes. Returns (resolved, error) where resolved is
    a list of (field, new_value, diff_line)."""
    resolved = []
    for ch in changes:
        field = ch.get("field")
        op = ch.get("op", "set")
        value = ch.get("value")
        if field in PROTECTED:
            return None, f"{field} 配置受保护，无法通过 bot 修改"
        if field is None or field not in effective_config:
            return None, f"未知配置项：{field}"
        current = effective_config.get(field)
        if isinstance(current, list):
            new_value, err = _apply_list_op(current, op, value)
        else:
            if op != "set":
                return None, f"{field} 不是列表，只能整体设置（set）"
            new_value, err = _coerce_scalar(field, value, current)
        if err:
            return None, err
        if new_value == current:
            continue  # no-op: value unchanged, nothing to do
        resolved.append((field, new_value, f"{field}: {current!r} → {new_value!r}"))
    return resolved, None


def compute_diff(changes, effective_config):
    """Plan changes without writing. Returns (diff_text, error)."""
    resolved, error = _plan_changes(changes, effective_config)
    if error:
        return None, error
    return "\n".join(line for _, _, line in resolved), None


def apply_changes(path, changes, effective_config) -> ApplyResult:
    resolved, error = _plan_changes(changes, effective_config)
    if error:
        return ApplyResult(False, error=error)
    data = load_roundtrip(path)
    for field, new_value, _ in resolved:
        data[field] = new_value
    dump_roundtrip(path, data)
    return ApplyResult(True, diff="\n".join(line for _, _, line in resolved))


def render_config(effective_config) -> str:
    """Render the editable config as YAML in a code block.

    Protected blocks (ai / notify) are configured manually in the file and are
    excluded — both to keep the view focused on what the bot can change and so
    their values (api_key, ids) are never echoed back.
    """
    editable = {k: v for k, v in effective_config.items() if k not in PROTECTED}
    body = yaml.safe_dump(
        editable, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    return "⚙️ 当前可修改的配置（ai / notify 需手动改配置文件）：\n```yaml\n" + body + "```"


def render_help() -> str:
    return (
        "🛠️ 可以发消息让我帮你查看 / 修改配置（ai、notify 受保护，不可改）：\n"
        "  • 查看配置：发「当前配置」\n"
        "  • 改轮询间隔：「轮询间隔改成 10 分钟」\n"
        "  • 加关键词：「关注关键词 上线」\n"
        "  • 删关键词：「不要关注 故障」\n"
        "  • 汇总消息：「汇总今天的消息」\n"
        "修改会先让你确认，回复「确认」后生效，下次轮询自动应用。"
    )
