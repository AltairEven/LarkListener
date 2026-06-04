# Bot 修改配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户通过给 Lark bot 发自然语言消息来查看和修改 `config.yaml`（`ai`/`notify` 除外），先确认再写入，下次轮询自动生效。

**Architecture:** 新增 `config_editor.py`（配置编辑领域逻辑，ruamel.yaml round-trip 保留注释 + 原子写）和 `intent.py`（统一 AI 意图识别，取代 `main._parse_trigger_with_ai`）；`main.py` 做调度并持有内存待确认状态，队列改传 `(content, sender_id)`，配置类操作仅 `notify.user_id` 本人可用。

**Tech Stack:** Python 3.9+, ruamel.yaml（新增）, pyyaml, pytest, 现有 AI provider 调用（claude/openai/ollama）。

参考 spec：`docs/superpowers/specs/2026-06-04-bot-config-edit-design.md`

---

### Task 1: 引入 ruamel.yaml 依赖

**Files:**
- Modify: `requirements.txt`
- Modify: `pyproject.toml:6-10`

- [ ] **Step 1: 在 requirements.txt 增加依赖**

把 `requirements.txt` 改为：

```
pyyaml>=6.0
anthropic>=0.30.0
openai>=1.30.0
ruamel.yaml>=0.18
```

- [ ] **Step 2: 在 pyproject.toml 的 dependencies 增加依赖**

把 `pyproject.toml` 的 `dependencies` 数组改为：

```toml
dependencies = [
    "pyyaml>=6.0",
    "anthropic>=0.30.0",
    "openai>=1.30.0",
    "ruamel.yaml>=0.18",
]
```

- [ ] **Step 3: 安装依赖并验证导入**

Run: `pip install -e . && python -c "from ruamel.yaml import YAML; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt pyproject.toml
git commit -m "build: add ruamel.yaml dependency for comment-preserving config writes"
```

---

### Task 2: config_editor — round-trip 读写（保留注释 + 原子写）

**Files:**
- Create: `lark_listener/config_editor.py`
- Test: `tests/test_config_editor.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_config_editor.py`：

```python
from lark_listener import config_editor

SAMPLE = """\
# 轮询间隔
poll_interval: 300
keywords:
  - 部署   # 关注部署
ai:
  provider: claude
notify:
  user_id: ou_test
"""


def test_roundtrip_preserves_comments(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")

    data = config_editor.load_roundtrip(str(p))
    data["poll_interval"] = 600
    config_editor.dump_roundtrip(str(p), data)

    text = p.read_text(encoding="utf-8")
    assert "# 轮询间隔" in text        # 顶部注释保留
    assert "# 关注部署" in text        # 行内注释保留
    assert "poll_interval: 600" in text


def test_dump_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    data = config_editor.load_roundtrip(str(p))
    config_editor.dump_roundtrip(str(p), data)
    assert not (tmp_path / "config.yaml.tmp").exists()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_config_editor.py -v`
Expected: FAIL（`module 'lark_listener.config_editor' has no attribute ...` 或 ImportError）

- [ ] **Step 3: 实现 round-trip 读写**

创建 `lark_listener/config_editor.py`：

```python
from __future__ import annotations

import os
from pathlib import Path

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    return y


def load_roundtrip(path: str):
    """Load YAML preserving comments and key order (ruamel CommentedMap)."""
    with open(path, "r", encoding="utf-8") as f:
        return _yaml().load(f)


def dump_roundtrip(path: str, data) -> None:
    """Atomically write the round-trip document back, preserving comments."""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _yaml().dump(data, f)
    os.replace(tmp, p)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_config_editor.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add lark_listener/config_editor.py tests/test_config_editor.py
git commit -m "feat: add comment-preserving round-trip config read/write"
```

---

### Task 3: config_editor — 变更校验、diff 与应用

**Files:**
- Modify: `lark_listener/config_editor.py`
- Test: `tests/test_config_editor.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_config_editor.py` 末尾追加：

```python
EFFECTIVE = {
    "poll_interval": 300,
    "include_at_all": True,
    "context_messages": 20,
    "keywords": ["部署", "故障"],
    "ai": {"provider": "claude", "api_key": "secret"},
    "notify": {"user_id": "ou_test"},
}


def test_protected_field_rejected():
    diff, err = config_editor.compute_diff(
        [{"field": "ai", "op": "set", "value": {}}], EFFECTIVE)
    assert diff is None
    assert "受保护" in err


def test_scalar_set_coerces_string_to_int():
    diff, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": "600"}], EFFECTIVE)
    assert err is None
    assert "300" in diff and "600" in diff


def test_poll_interval_rejects_non_positive():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": 0}], EFFECTIVE)
    assert "正整数" in err


def test_poll_interval_rejects_non_number():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "set", "value": "abc"}], EFFECTIVE)
    assert "整数" in err


def test_bool_coercion():
    diff, err = config_editor.compute_diff(
        [{"field": "include_at_all", "op": "set", "value": "false"}], EFFECTIVE)
    assert err is None
    assert "False" in diff


def test_list_add_dedupes():
    diff, err = config_editor.compute_diff(
        [{"field": "keywords", "op": "add", "value": "部署"}], EFFECTIVE)
    assert err is None
    # 已存在，结果列表不变
    assert "'部署', '故障'" in diff.replace('"', "'")


def test_list_remove():
    diff, err = config_editor.compute_diff(
        [{"field": "keywords", "op": "remove", "value": "故障"}], EFFECTIVE)
    assert err is None
    assert "故障" not in diff.split("→")[1]


def test_scalar_field_rejects_list_op():
    _, err = config_editor.compute_diff(
        [{"field": "poll_interval", "op": "add", "value": 1}], EFFECTIVE)
    assert "列表" in err


def test_apply_changes_writes_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    result = config_editor.apply_changes(
        str(p), [{"field": "poll_interval", "op": "set", "value": 600}], EFFECTIVE)
    assert result.ok
    assert "poll_interval: 600" in p.read_text(encoding="utf-8")


def test_apply_changes_adds_missing_key(tmp_path):
    # context_messages 不在文件里（靠默认值），set 时应新增
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    result = config_editor.apply_changes(
        str(p), [{"field": "context_messages", "op": "set", "value": 5}], EFFECTIVE)
    assert result.ok
    assert "context_messages: 5" in p.read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_config_editor.py -v`
Expected: FAIL（`has no attribute 'compute_diff'`）

- [ ] **Step 3: 实现校验、diff、应用**

在 `lark_listener/config_editor.py` 顶部 import 后追加 `dataclass` 导入，并在文件末尾追加实现：

把文件顶部的 import 段改为：

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

PROTECTED = {"ai", "notify"}
```

在文件末尾追加：

```python
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
    items = [str(x) for x in current]
    if op == "set":
        new = value if isinstance(value, list) else [value]
        return [str(x) for x in new], None
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_config_editor.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: Commit**

```bash
git add lark_listener/config_editor.py tests/test_config_editor.py
git commit -m "feat: validate, diff, and apply config changes (protect ai/notify)"
```

---

### Task 4: config_editor — 渲染配置与帮助文本

**Files:**
- Modify: `lark_listener/config_editor.py`
- Test: `tests/test_config_editor.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_config_editor.py` 末尾追加：

```python
def test_render_config_hides_api_key():
    text = config_editor.render_config(EFFECTIVE)
    assert "secret" not in text
    assert "poll_interval" in text
    assert "300" in text


def test_render_help_mentions_protected():
    text = config_editor.render_help()
    assert "ai" in text and "notify" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_config_editor.py -k render -v`
Expected: FAIL（`has no attribute 'render_config'`）

- [ ] **Step 3: 实现渲染函数**

在 `lark_listener/config_editor.py` 末尾追加：

```python
def render_config(effective_config) -> str:
    lines = ["⚙️ 当前配置（ai / notify 不可改）："]
    for key, value in effective_config.items():
        if key == "ai" and isinstance(value, dict):
            safe = {k: v for k, v in value.items() if k != "api_key"}
            lines.append(f"  {key}: {safe}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_config_editor.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: Commit**

```bash
git add lark_listener/config_editor.py tests/test_config_editor.py
git commit -m "feat: render current config and help text for bot"
```

---

### Task 5: intent.py — 统一 AI 意图识别（含迁移 summary 测试）

**Files:**
- Create: `lark_listener/intent.py`
- Test: `tests/test_intent.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_intent.py`（前三个用例迁移自 `tests/test_main.py` 的 `_parse_trigger_with_ai` 测试，覆盖 summary 行为不回归）：

```python
import json as _json
from unittest.mock import patch, MagicMock

from lark_listener import intent

_CONFIG = {"ai": {"provider": "ollama", "model": "x", "api_key": "", "base_url": ""}}


def _mock_ollama(mock_urlopen, content_obj):
    resp = MagicMock()
    resp.read.return_value = _json.dumps(
        {"message": {"content": _json.dumps(content_obj)}}
    ).encode()
    mock_urlopen.return_value.__enter__.return_value = resp


@patch("urllib.request.urlopen")
def test_summary_invalid_start_time_still_summary(mock_urlopen):
    _mock_ollama(mock_urlopen, {"type": "summary", "start_time": "not-a-date"})
    result = intent.parse("汇总最近的消息", _CONFIG)
    assert result.type == "summary"
    assert result.start_time is None


@patch("urllib.request.urlopen")
def test_summary_valid_start_time_parsed(mock_urlopen):
    _mock_ollama(mock_urlopen, {"type": "summary", "start_time": "2026-06-03T10:00:00+08:00"})
    result = intent.parse("汇总今天上午", _CONFIG)
    assert result.type == "summary"
    assert result.start_time is not None
    assert result.start_time.hour == 10


@patch("urllib.request.urlopen")
def test_none_intent(mock_urlopen):
    _mock_ollama(mock_urlopen, {"type": "none"})
    result = intent.parse("你好", _CONFIG)
    assert result.type == "none"


@patch("urllib.request.urlopen")
def test_config_modify_changes_parsed(mock_urlopen):
    _mock_ollama(mock_urlopen, {
        "type": "config_modify",
        "changes": [{"field": "poll_interval", "op": "set", "value": 600}],
    })
    result = intent.parse("轮询间隔改成10分钟", _CONFIG)
    assert result.type == "config_modify"
    assert result.changes == [{"field": "poll_interval", "op": "set", "value": 600}]


@patch("urllib.request.urlopen")
def test_config_view_and_confirm(mock_urlopen):
    _mock_ollama(mock_urlopen, {"type": "config_view"})
    assert intent.parse("当前配置", _CONFIG).type == "config_view"
    _mock_ollama(mock_urlopen, {"type": "confirm"})
    assert intent.parse("确认", _CONFIG).type == "confirm"


@patch("urllib.request.urlopen")
def test_bad_json_falls_back_to_none(mock_urlopen):
    resp = MagicMock()
    resp.read.return_value = _json.dumps({"message": {"content": "not json"}}).encode()
    mock_urlopen.return_value.__enter__.return_value = resp
    assert intent.parse("???", _CONFIG).type == "none"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_intent.py -v`
Expected: FAIL（ImportError: cannot import name ...）

- [ ] **Step 3: 实现 intent.py**

创建 `lark_listener/intent.py`：

```python
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("lark_listener")
TZ = timezone(timedelta(hours=8))

INTENT_PROMPT = """\
当前时间：{now}
当前配置（其中 ai 与 notify 受保护，不可修改）：
{config_json}

你是消息助手的意图识别模块。用户给 Bot 发了一条消息，请判断意图，严格输出 JSON，不要输出其他内容。

type 取值：
- "summary"：用户想汇总/总结消息。可带 start_time（ISO 8601，带 +08:00 时区），未指定时间则为 null。
- "config_view"：用户想查看当前配置。
- "config_modify"：用户想修改配置。需输出 changes 数组，每项为
  {{"field": 字段名, "op": "set"|"add"|"remove", "value": 值}}。
  列表字段（如 keywords）用 add/remove/set；标量字段（如 poll_interval）用 set。
  注意：ai 和 notify 不可修改，若用户想改这两个，仍按 config_modify 输出，由后续逻辑拒绝。
- "config_help"：用户想了解能改什么、怎么用。
- "confirm"：用户确认（如"确认""是""好的"）。
- "cancel"：用户取消（如"取消""不要了"）。
- "none"：以上都不是。

用户消息："{message}"

示例：
- "汇总今天上午的消息" → {{"type": "summary", "start_time": "{today}T00:00:00+08:00"}}
- "总结一下" → {{"type": "summary", "start_time": null}}
- "轮询间隔改成10分钟" → {{"type": "config_modify", "changes": [{{"field": "poll_interval", "op": "set", "value": 600}}]}}
- "关注关键词 上线" → {{"type": "config_modify", "changes": [{{"field": "keywords", "op": "add", "value": "上线"}}]}}
- "当前配置" → {{"type": "config_view"}}
- "确认" → {{"type": "confirm"}}
- "你好" → {{"type": "none"}}"""


@dataclass
class Intent:
    type: str
    start_time: Optional[datetime] = None
    changes: Optional[list] = None


def _sanitized_config(config: dict) -> dict:
    c = copy.deepcopy(config)
    if isinstance(c.get("ai"), dict):
        c["ai"].pop("api_key", None)
    return c


def _call_ai(prompt: str, ai_cfg: dict) -> str:
    """Return the raw text response from the configured provider."""
    provider = ai_cfg.get("provider")
    api_key = ai_cfg.get("api_key", "")
    if provider == "openai":
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=ai_cfg.get("base_url") or None)
        resp = client.chat.completions.create(
            model=ai_cfg["model"], messages=[{"role": "user", "content": prompt}])
        return resp.choices[0].message.content
    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=ai_cfg["model"], max_tokens=512,
            messages=[{"role": "user", "content": prompt}])
        return resp.content[0].text
    if provider == "ollama":
        import urllib.request
        url = (ai_cfg.get("base_url") or "http://localhost:11434") + "/api/chat"
        payload = json.dumps({
            "model": ai_cfg["model"], "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["message"]["content"]
    raise ValueError(f"Unknown provider: {provider}")


def parse(message: str, config: dict) -> Intent:
    """Classify a bot message into an Intent. Falls back to type='none' on any error."""
    ai_cfg = config["ai"]
    now = datetime.now(TZ)
    prompt = INTENT_PROMPT.format(
        now=now.isoformat(),
        today=now.strftime("%Y-%m-%d"),
        config_json=json.dumps(_sanitized_config(config), ensure_ascii=False, indent=2),
        message=message,
    )
    try:
        result = json.loads(_call_ai(prompt, ai_cfg))
    except Exception:
        logger.exception("Failed to parse intent from message: %s", message)
        return Intent(type="none")

    itype = result.get("type", "none")
    if itype == "summary":
        start = None
        if result.get("start_time"):
            try:
                start = datetime.fromisoformat(result["start_time"])
            except (ValueError, TypeError):
                logger.warning("Invalid start_time from AI: %r", result.get("start_time"))
        return Intent(type="summary", start_time=start)
    if itype == "config_modify":
        return Intent(type="config_modify", changes=result.get("changes") or [])
    if itype in ("config_view", "config_help", "confirm", "cancel"):
        return Intent(type=itype)
    return Intent(type="none")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_intent.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: Commit**

```bash
git add lark_listener/intent.py tests/test_intent.py
git commit -m "feat: unified AI intent parser for summary and config commands"
```

---

### Task 6: main.py — 调度、待确认状态、sender 校验

**Files:**
- Modify: `lark_listener/main.py`（删除 `TRIGGER_PROMPT` 33-49、`_parse_trigger_with_ai` 52-109；改 `_bot_listener`、`poll_once` 不变、改 `main` 触发处理；新增 `_handle_message`）
- Modify: `tests/test_main.py:78-117`（删除迁移走的 `_parse_trigger_with_ai` 测试）
- Test: `tests/test_main.py`（新增 `_handle_message` 调度测试）

- [ ] **Step 1: 写失败测试**

先删除 `tests/test_main.py` 第 78-117 行（`# --- _parse_trigger_with_ai robustness ...` 起，到文件末尾的三个 trigger 测试），然后在 `tests/test_main.py` 末尾追加调度测试：

```python
# --- _handle_message dispatch ---

import lark_listener.main as main_mod
from lark_listener.intent import Intent
from lark_listener.config_editor import ApplyResult


def _write_config(tmp_path):
    config_path = str(tmp_path / "config.yaml")
    state_path = str(tmp_path / "state.json")
    with open(config_path, "w") as f:
        f.write(SAMPLE_CONFIG)
    return config_path, state_path


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.poll_once")
def test_dispatch_summary_calls_poll(mock_poll, mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_config(tmp_path)
    mock_parse.return_value = Intent(type="summary", start_time=None)
    main_mod._handle_message("汇总", "ou_anyone", config_path, state_path)
    mock_poll.assert_called_once()


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_config_rejects_non_owner(mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_config(tmp_path)
    mock_parse.return_value = Intent(type="config_view")
    main_mod._handle_message("当前配置", "ou_stranger", config_path, state_path)
    # 回复给陌生人本人的拒绝提示
    mock_reply.assert_called_once()
    assert "仅本人" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_config_modify_sets_pending(mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_config(tmp_path)
    main_mod._pending_change = None
    mock_parse.return_value = Intent(
        type="config_modify",
        changes=[{"field": "poll_interval", "op": "set", "value": 600}])
    main_mod._handle_message("轮询间隔改成10分钟", "ou_test", config_path, state_path)
    assert main_mod._pending_change is not None
    assert "确认" in mock_reply.call_args.args[1]


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
@patch("lark_listener.main.config_editor.apply_changes")
def test_dispatch_confirm_applies_pending(mock_apply, mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_config(tmp_path)
    main_mod._pending_change = {"changes": [{"field": "poll_interval", "op": "set", "value": 600}], "diff": "x"}
    mock_apply.return_value = ApplyResult(True, diff="poll_interval: 60 → 600")
    mock_parse.return_value = Intent(type="confirm")
    main_mod._handle_message("确认", "ou_test", config_path, state_path)
    mock_apply.assert_called_once()
    assert main_mod._pending_change is None


@patch("lark_listener.main._reply_bot")
@patch("lark_listener.main.intent.parse")
def test_dispatch_confirm_without_pending(mock_parse, mock_reply, tmp_path):
    config_path, state_path = _write_config(tmp_path)
    main_mod._pending_change = None
    mock_parse.return_value = Intent(type="confirm")
    main_mod._handle_message("确认", "ou_test", config_path, state_path)
    assert "没有待确认" in mock_reply.call_args.args[1]
```

注：`SAMPLE_CONFIG`（test_main.py 顶部已有）的 `notify.user_id` 为 `ou_test`，所以用 `ou_test` 作为本人、`ou_stranger` 作为陌生人。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_main.py -v`
Expected: FAIL（`module 'lark_listener.main' has no attribute '_handle_message'` / `_pending_change`）

- [ ] **Step 3: 改写 main.py**

3a. 删除 `lark_listener/main.py` 第 33-49 行的 `TRIGGER_PROMPT = """..."""` 整段，以及第 52-109 行的整个 `_parse_trigger_with_ai` 函数。

3b. 在 import 段（第 14-19 行附近）追加：

```python
from lark_listener import config_editor, intent
```

3c. 在模块级状态附近（`_trigger_queue` 定义之后）把队列类型注释改为携带 sender，并新增待确认状态：

```python
_trigger_queue: queue.Queue[Optional[tuple[str, str]]] = queue.Queue()
_pending_change: Optional[dict] = None
```

3d. 在 `_bot_listener` 中，把入队那段（原第 202-214 行附近）改为提取 sender 并入队元组：

```python
                    message = event.get("event", {}).get("message", {})
                    message_id = message.get("message_id", "")
                    if message_id:
                        _add_reaction(message_id)
                    sender = event.get("event", {}).get("sender", {})
                    sender_id = sender.get("sender_id", {}).get("open_id", "")
                    msg_content = message.get("content", "")
                    try:
                        content = json.loads(msg_content).get("text", "")
                    except (json.JSONDecodeError, AttributeError):
                        content = msg_content
                    content = content.strip()
                    if not content:
                        continue
                    logger.info("Bot received message: %s", content[:100])
                    _trigger_queue.put((content, sender_id))
```

3e. 在 `poll_once` 之后、`main` 之前新增 `_handle_message`：

```python
def _handle_message(content: str, sender_id: str, config_path: str, state_path: str):
    """Dispatch a bot message: summary trigger, or owner-only config operation."""
    global _pending_change
    config = load_config(config_path)
    my_user_id = config["notify"]["user_id"]
    parsed = intent.parse(content, config)

    if parsed.type == "summary":
        if parsed.start_time:
            logger.info("Trigger with custom start: %s", parsed.start_time.isoformat())
        else:
            logger.info("Trigger with default time range")
        poll_once(config_path, state_path, custom_start=parsed.start_time, is_manual=True)
        return

    if parsed.type == "none":
        logger.info("Message not actionable: %s", content[:50])
        return

    # Remaining types are config operations — owner only.
    if sender_id != my_user_id:
        if sender_id:
            _reply_bot(sender_id, "⚠️ 仅本人可查看或修改配置")
        else:
            logger.info("Config op from unknown sender ignored")
        return

    if parsed.type == "config_view":
        _reply_bot(my_user_id, config_editor.render_config(config))
        return

    if parsed.type == "config_help":
        _reply_bot(my_user_id, config_editor.render_help())
        return

    if parsed.type == "config_modify":
        diff, error = config_editor.compute_diff(parsed.changes or [], config)
        if error:
            _pending_change = None
            _reply_bot(my_user_id, f"⚠️ {error}")
            return
        if not diff:
            _reply_bot(my_user_id, "没有可修改的内容")
            return
        _pending_change = {"changes": parsed.changes, "diff": diff}
        _reply_bot(my_user_id, f"将修改：\n{diff}\n回复「确认」生效，「取消」放弃。")
        return

    if parsed.type == "confirm":
        if not _pending_change:
            _reply_bot(my_user_id, "当前没有待确认的修改")
            return
        result = config_editor.apply_changes(config_path, _pending_change["changes"], config)
        _pending_change = None
        if result.ok:
            _reply_bot(my_user_id, f"✅ 已更新，下次轮询生效：\n{result.diff}")
        else:
            _reply_bot(my_user_id, f"⚠️ 修改失败：{result.error}")
        return

    if parsed.type == "cancel":
        if _pending_change:
            _pending_change = None
            _reply_bot(my_user_id, "已取消修改")
        else:
            _reply_bot(my_user_id, "当前没有待确认的修改")
        return
```

3f. 在 `main()` 的 while 循环里，把"Wait for interval or trigger"之后的整段触发处理（原第 356-383 行，从 `try: trigger_msg = _trigger_queue.get(...)` 到 trigger 的 try/except 结束）替换为：

```python
        # Wait for interval or trigger
        try:
            item = _trigger_queue.get(timeout=interval)
        except queue.Empty:
            continue
        if item is None:
            break
        content, sender_id = item

        # A failure here (AI, network, lark-cli, bad config) must NOT crash the
        # service — otherwise launchd KeepAlive restarts it into a crash loop.
        try:
            _handle_message(content, sender_id, config_path, state_path)
        except Exception:
            logger.exception("Error handling message: %s", content[:50])
            _reply_bot(my_user_id, "⚠️ 处理请求时出错，请查看日志：\ntail -f ~/.lark_listener/logs/stderr.log")
```

- [ ] **Step 4: 运行全部测试确认通过**

Run: `pytest -v`
Expected: PASS（含 test_main、test_intent、test_config_editor，无 import 残留错误）

- [ ] **Step 5: Commit**

```bash
git add lark_listener/main.py tests/test_main.py
git commit -m "feat: dispatch bot messages to config ops with owner check and confirmation"
```

---

### Task 7: 可发现性 — 启动消息 + README

**Files:**
- Modify: `lark_listener/main.py`（启动消息，原第 333 行）
- Modify: `README.md`

- [ ] **Step 1: 更新启动消息**

把 `lark_listener/main.py` 中的启动通知（原第 333 行）：

```python
    _reply_bot(my_user_id, f"✅ LarkListener 已启动（轮询间隔 {interval} 秒）")
```

改为：

```python
    _reply_bot(my_user_id, f"✅ LarkListener 已启动（轮询间隔 {interval} 秒）。发「帮助」可查看或修改配置。")
```

- [ ] **Step 2: 更新 README**

在 `README.md` 的"## 主动触发"小节末尾追加一段：

```markdown
## 通过 Bot 修改配置

直接给 bot 发自然语言即可查看或修改配置（仅本人，且 `ai` / `notify` 受保护不可改）：

- 查看配置：发「当前配置」
- 修改：「轮询间隔改成 10 分钟」「关注关键词 上线」「不要关注 故障」
- 用法说明：发「帮助」

修改会先回复变更摘要，回复「确认」后写入 `config.yaml`，下次轮询自动生效（注释会被保留）。
```

- [ ] **Step 3: 验证服务可正常导入启动**

Run: `python -c "import lark_listener.main; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 4: Commit**

```bash
git add lark_listener/main.py README.md
git commit -m "docs: surface bot config editing in startup message and README"
```

---

## 验收

- `pytest -v` 全绿。
- 启动消息提示「发『帮助』」。
- 给 bot 发「当前配置」→ 收到配置（无 api_key）；发「轮询间隔改成 10 分钟」→ 收到待确认摘要；发「确认」→ 写入且 config.yaml 注释保留；非本人发配置指令 → 被拒绝；发「汇总」→ 照常触发汇总。
