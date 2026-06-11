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
    # config 含明文 api_key：保持原文件权限（用户可能已 chmod 600），新建默认
    # 0600——否则 os.replace 会让 tmp 的 umask 权限（0644）静默取代原 mode。
    try:
        mode = p.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    try:
        # tmp 以 0600 创建（而非先 umask 0644 写完密钥再 chmod——那留有
        # 毫秒级全文可读窗口）；replace 前再对齐目标 mode。
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _yaml().dump(data, f)
        os.chmod(tmp, mode)
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
    if isinstance(current, bool):
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
        if field == "poll_interval" and n < 0:
            return None, "poll_interval 需为非负整数（0=关闭自动轮询）"
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


def _chat_id_set(value) -> set:
    ids = set()
    for item in value or []:
        if isinstance(item, dict) and item.get("chat_id"):
            ids.add(str(item["chat_id"]))
        elif isinstance(item, str):
            ids.add(item)
    return ids


def removes_bot_chat(bot_chat_id, current, new_value) -> bool:
    """exclude_chats 的变更是否会把 bot 会话移出——防自反馈守卫的共享判断
    （bot 指令路径 _plan_changes 与 CLI 路径 config_cli.config_set 共用）。
    兼容新（{chat_id,name} 条目）旧（纯 str）两种形态。"""
    bot = str(bot_chat_id or "")
    return bool(bot and bot in _chat_id_set(current)
                and bot not in _chat_id_set(new_value))


def _apply_chat_list_op(current, op, value):
    """exclude_chats 专用：条目为 {chat_id, name}；add/remove 的 value 是
    chat_id 字符串（name 留空由轮询自动补全）。Returns (new_list, error)。"""
    if value is None:
        return None, "列表操作需要提供值"
    items = []
    for x in current or []:
        if isinstance(x, dict) and x.get("chat_id"):
            items.append({"chat_id": str(x["chat_id"]), "name": str(x.get("name") or "")})
        elif isinstance(x, str):
            items.append({"chat_id": x, "name": ""})

    def _cid(v):
        return str(v.get("chat_id", "")) if isinstance(v, dict) else str(v)

    if op == "set":
        new = value if isinstance(value, list) else [value]
        out, seen = [], set()
        for x in new:
            cid = _cid(x)
            if cid and cid not in seen:
                seen.add(cid)
                name = str(x.get("name") or "") if isinstance(x, dict) else ""
                out.append({"chat_id": cid, "name": name})
        return out, None
    if op == "add":
        cid = _cid(value)
        if not cid:
            return None, "exclude_chats 条目需要 chat_id"
        if cid not in {i["chat_id"] for i in items}:
            items.append({"chat_id": cid, "name": ""})
        return items, None
    if op == "remove":
        cid = _cid(value)
        return [i for i in items if i["chat_id"] != cid], None
    return None, f"未知操作 {op}"


def _resolve_field(effective_config, field):
    """返回 (current, ok)。支持一层点号嵌套标量（special_focus.enabled 等）。"""
    if "." in field:
        root, leaf = field.split(".", 1)
        parent = effective_config.get(root)
        if isinstance(parent, dict) and leaf in parent:
            return parent[leaf], True
        return None, False
    if field in effective_config:
        return effective_config[field], True
    return None, False


def _plan_changes(changes, effective_config):
    """Validate + resolve changes. Returns (resolved, error) where resolved is
    a list of (field, new_value, diff_line)."""
    resolved = []
    for ch in changes:
        field = ch.get("field")
        op = ch.get("op", "set")
        value = ch.get("value")
        root = (field or "").split(".", 1)[0]
        if root in PROTECTED:
            return None, f"{root} 配置受保护，无法通过 bot 修改"
        if field is None:
            return None, "未知配置项：None"
        current, ok = _resolve_field(effective_config, field)
        if not ok:
            return None, f"未知配置项：{field}"
        if field == "exclude_chats":
            new_value, err = _apply_chat_list_op(current, op, value)
        elif isinstance(current, dict):
            return None, (f"{field} 为嵌套配置，请指定具体字段"
                          f"（如 special_focus.enabled / special_focus.max_messages）")
        elif isinstance(current, list):
            if field.endswith(".chats") or field == "special_focus.chats":
                return None, "special_focus.chats 结构含每群关键词，请直接编辑 config.yaml"
            new_value, err = _apply_list_op(current, op, value)
        else:
            if op != "set":
                return None, f"{field} 不是列表，只能整体设置（set）"
            new_value, err = _coerce_scalar(field, value, current)
        if err:
            return None, err
        bot_chat_id = (effective_config.get("notify") or {}).get("bot_chat_id")
        if field == "exclude_chats" and removes_bot_chat(bot_chat_id, current, new_value):
            return None, "exclude_chats 中的 bot 会话不可移除（防止汇总消息被自身轮询命中形成自反馈）"
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
    # 空文件 load_roundtrip 返回 None；按空映射处理，不能 TypeError。
    data = load_roundtrip(path) or CommentedMap()
    for field, new_value, _ in resolved:
        if "." in field:
            root, leaf = field.split(".", 1)
            parent = data.get(root)
            if not isinstance(parent, dict):
                parent = CommentedMap()
                data[root] = parent
            parent[leaf] = new_value
        else:
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


def autofill_chat_names(path: str | Path, name_of) -> bool:
    """为 exclude_chats / special_focus.chats 中缺 name 的条目补名，并把
    旧键迁移为新格式（exclude_chat_ids → exclude_chats、删除废键
    include_at_all）。有变化才原子回写（保注释、保 mode）。

    name_of: chat_id -> str，查不到返回空串（留空下轮再试）。
    返回是否发生了回写。调用方（poll_once）必须 best-effort 包裹。"""
    data = load_roundtrip(path)
    if not isinstance(data, dict):
        return False
    changed = False
    if "exclude_chat_ids" in data and "exclude_chats" not in data:
        entries = []
        for x in (data.get("exclude_chat_ids") or []):
            if isinstance(x, dict) and x.get("chat_id"):
                entries.append({"chat_id": str(x["chat_id"]),
                                "name": str(x.get("name") or "")})
            elif x is not None:
                entries.append({"chat_id": str(x), "name": ""})
        data["exclude_chats"] = entries
        del data["exclude_chat_ids"]
        changed = True
    if "include_at_all" in data:
        del data["include_at_all"]
        changed = True

    def _fill(entries):
        nonlocal changed
        for e in entries or []:
            if isinstance(e, dict) and e.get("chat_id") and not e.get("name"):
                name = name_of(str(e["chat_id"])) or ""
                if name:
                    e["name"] = name
                    changed = True

    _fill(data.get("exclude_chats"))
    sf = data.get("special_focus")
    if isinstance(sf, dict):
        _fill(sf.get("chats"))
    if changed:
        dump_roundtrip(path, data)
    return changed
