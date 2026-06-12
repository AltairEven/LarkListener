# 会话分类（勿扰/普通/特别关注）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按飞书免打扰状态把会话分为勿扰/普通/特别关注三类，差异化处理 @所有人 与全量汇总，新增 special_focus 配置（含按群绑定关注词），并把上下文/特别关注抓取合并为常数次命令。

**Architecture:** 新模块 `chats.py` 承载 mute 探测（`chat-list --exclude-muted` 差集推导）与分类纯函数；`fetcher` 注入 registry 做 @all 分流与特别关注全量抓取；`config` 升级 `exclude_chats` 结构并新增 `special_focus` 节（旧格式兼容）；`analyzer`/`notifier` 增加特别关注标注与卡片新区。归类优先级与卡片顺序统一：私聊 > @我 > @所有人 > 特别关注 > 关键词命中。

**Tech Stack:** Python 标准库 + pyyaml/ruamel.yaml；测试 pytest + unittest.mock（mock subprocess，不真发飞书）。

**Spec:** `docs/superpowers/specs/2026-06-11-chat-classification-design.md`

**约定：**
- 工作分支 `feat/chat-classification`（Task 1 创建），每个 Task 一个 commit。
- 每个 Task 内严格 TDD：先写测试 → 跑出预期失败 → 最小实现 → 全绿 → commit。
- 全程不真跑 launchctl / 不真发飞书；`python3 -m pytest -q` 全绿才算任务完成。
- commit message 末尾带 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: chats.py — 分类纯函数与 ChatRegistry

**Files:**
- Create: `lark_listener/chats.py`
- Test: `tests/test_chats.py`

- [ ] **Step 1.0: 建分支**

```bash
git checkout main && git pull && git checkout -b feat/chat-classification
```

- [ ] **Step 1.1: 写失败测试**

新建 `tests/test_chats.py`：

```python
import json
from unittest.mock import patch, MagicMock

from lark_listener.chats import ChatClass, ChatRegistry, classify_chat


# --- classify_chat 纯函数 ---

def test_classify_p2p_always_normal():
    assert classify_chat("p2p", "oc_x", {"oc_x"}, True) is ChatClass.NORMAL
    assert classify_chat("p2p", "oc_x", None, True) is ChatClass.NORMAL


def test_classify_muted_group():
    assert classify_chat("group", "oc_a", {"oc_b"}, True) is ChatClass.MUTED
    assert classify_chat("group", "oc_a", set(), False) is ChatClass.MUTED


def test_classify_unmuted_group_normal_when_special_disabled():
    assert classify_chat("group", "oc_a", {"oc_a"}, False) is ChatClass.NORMAL


def test_classify_unmuted_group_special_when_enabled():
    assert classify_chat("group", "oc_a", {"oc_a"}, True) is ChatClass.SPECIAL


def test_classify_degraded_none_means_all_muted():
    """首刷失败（unmuted=None）→ 群一律按勿扰（宁可少收）。"""
    assert classify_chat("group", "oc_a", None, True) is ChatClass.MUTED


# --- ChatRegistry.refresh ---

def _chat_list_page(chats, has_more=False, page_token=""):
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps({"ok": True, "data": {
        "chats": chats, "has_more": has_more, "page_token": page_token}})
    return mock


@patch("lark_listener.chats.subprocess.run")
def test_refresh_collects_unmuted_groups(mock_run):
    mock_run.return_value = _chat_list_page(
        [{"chat_id": "oc_vip", "name": "VIP群"}])
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is True
    assert reg.classify("oc_vip", "group") is ChatClass.SPECIAL
    assert reg.classify("oc_other", "group") is ChatClass.MUTED
    assert reg.special_chat_ids() == ["oc_vip"]
    assert reg.name_of("oc_vip") == "VIP群"


@patch("lark_listener.chats.subprocess.run")
def test_refresh_paginates(mock_run):
    mock_run.side_effect = [
        _chat_list_page([{"chat_id": "oc_1", "name": "一"}], has_more=True, page_token="t2"),
        _chat_list_page([{"chat_id": "oc_2", "name": "二"}]),
    ]
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is True
    assert sorted(reg.special_chat_ids()) == ["oc_1", "oc_2"]
    # 第二次调用带 page-token
    second_args = mock_run.call_args_list[1][0][0]
    assert "--page-token" in second_args and "t2" in second_args


@patch("lark_listener.chats.subprocess.run")
def test_refresh_failure_keeps_last_result(mock_run):
    mock_run.return_value = _chat_list_page([{"chat_id": "oc_vip", "name": "VIP群"}])
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is True
    bad = MagicMock(); bad.returncode = 1; bad.stdout = ""
    mock_run.return_value = bad
    assert reg.refresh() is False
    # 沿用上一轮结果
    assert reg.classify("oc_vip", "group") is ChatClass.SPECIAL


@patch("lark_listener.chats.subprocess.run")
def test_refresh_first_failure_degrades_to_all_muted(mock_run):
    bad = MagicMock(); bad.returncode = 1; bad.stdout = ""
    mock_run.return_value = bad
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is False
    assert reg.classify("oc_any", "group") is ChatClass.MUTED
    assert reg.special_chat_ids() == []


def test_special_chat_ids_empty_when_disabled():
    reg = ChatRegistry(special_enabled=False)
    reg._unmuted = {"oc_vip": "VIP群"}
    assert reg.special_chat_ids() == []
    assert reg.classify("oc_vip", "group") is ChatClass.NORMAL


@patch("lark_listener.chats.subprocess.run")
def test_name_of_falls_back_to_chats_get(mock_run):
    """勿扰群不在未免打扰列表里，补名走单群查询。"""
    mock = MagicMock(); mock.returncode = 0
    mock.stdout = json.dumps({"ok": True, "data": {"name": "某勿扰群"}})
    mock_run.return_value = mock
    reg = ChatRegistry()
    reg._unmuted = {}
    assert reg.name_of("oc_muted") == "某勿扰群"


@patch("lark_listener.chats.subprocess.run")
def test_name_of_failure_returns_empty(mock_run):
    mock_run.side_effect = OSError("no cli")
    reg = ChatRegistry()
    reg._unmuted = {}
    assert reg.name_of("oc_x") == ""
```

- [ ] **Step 1.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_chats.py -q
```
预期：`ModuleNotFoundError: No module named 'lark_listener.chats'`（collection error 也算预期失败）。

- [ ] **Step 1.3: 实现 chats.py**

新建 `lark_listener/chats.py`：

```python
from __future__ import annotations

import json
import logging
import subprocess
from enum import Enum
from typing import Optional

from lark_listener.binaries import lark_cli

logger = logging.getLogger("lark_listener")

# 翻页保险丝：100/页 × 20 = 2000 个未免打扰群封顶，防 API 异常时无限翻页。
_MAX_PAGES = 20


class ChatClass(Enum):
    MUTED = "muted"        # 已免打扰的群：@所有人 仅命中关键词才收
    NORMAL = "normal"      # 未免打扰的群（特别关注关闭时）与 p2p：@所有人 全收
    SPECIAL = "special"    # 特别关注群：窗口内全量收


def classify_chat(
    chat_type: str,
    chat_id: str,
    unmuted_group_ids: Optional[set],
    special_enabled: bool,
) -> ChatClass:
    """分类纯函数核。unmuted_group_ids=None 表示从未成功拉到未免打扰列表
    （首刷失败的降级态）：群一律按勿扰处理（宁可少收不误收）。
    p2p 恒 NORMAL——mute 不影响私聊行为（spec §1）。"""
    if chat_type != "group":
        return ChatClass.NORMAL
    if not unmuted_group_ids or chat_id not in unmuted_group_ids:
        return ChatClass.MUTED
    return ChatClass.SPECIAL if special_enabled else ChatClass.NORMAL


class ChatRegistry:
    """未免打扰群注册表：每轮产出汇总前 refresh 一次（产出时刷新＝等效实时，
    spec §2）。免打扰是用户维度设置，消息搜索与 chats get 均不携带，
    `chat-list --exclude-muted` 是唯一数据源。"""

    def __init__(self, special_enabled: bool = False):
        self.special_enabled = special_enabled
        # chat_id -> name。None 表示从未成功刷新（降级：全按勿扰）。
        self._unmuted: Optional[dict] = None

    def refresh(self) -> bool:
        """拉取未免打扰群列表（带翻页）。失败保留上一轮结果并返回 False。"""
        chats: dict[str, str] = {}
        page_token = ""
        for _ in range(_MAX_PAGES):
            args = ["im", "+chat-list", "--exclude-muted",
                    "--page-size", "100", "--format", "json"]
            if page_token:
                args += ["--page-token", page_token]
            try:
                proc = subprocess.run(lark_cli(*args), capture_output=True,
                                      text=True, timeout=30)
                data = json.loads(proc.stdout)
            except Exception:  # noqa: BLE001 — best-effort：失败沿用旧结果
                logger.warning("chat-list --exclude-muted 调用失败，沿用上一轮 mute 状态")
                return False
            if proc.returncode != 0 or not data.get("ok"):
                logger.warning("chat-list --exclude-muted 返回失败（rc=%s），沿用上一轮 mute 状态",
                               proc.returncode)
                return False
            inner = data.get("data") or {}
            for c in inner.get("chats") or []:
                if isinstance(c, dict) and c.get("chat_id"):
                    chats[c["chat_id"]] = str(c.get("name") or "")
            page_token = inner.get("page_token") or ""
            if not inner.get("has_more") or not page_token:
                break
        self._unmuted = chats
        return True

    def classify(self, chat_id: str, chat_type: str) -> ChatClass:
        unmuted = set(self._unmuted) if self._unmuted is not None else None
        return classify_chat(chat_type, chat_id, unmuted, self.special_enabled)

    def special_chat_ids(self) -> list:
        """特别关注群 id 列表（开关关闭或无数据时为空）。"""
        if not self.special_enabled or not self._unmuted:
            return []
        return list(self._unmuted)

    def name_of(self, chat_id: str) -> str:
        """群名解析（供配置补名）：优先未免打扰列表，勿扰群回落单群查询。"""
        if self._unmuted and chat_id in self._unmuted:
            return self._unmuted[chat_id]
        try:
            proc = subprocess.run(
                lark_cli("im", "chats", "get", "--params",
                         json.dumps({"chat_id": chat_id}), "--format", "json"),
                capture_output=True, text=True, timeout=30)
            data = json.loads(proc.stdout)
            if proc.returncode == 0 and data.get("ok"):
                return str((data.get("data") or {}).get("name") or "")
        except Exception:  # noqa: BLE001 — 补名失败留空下轮再试
            pass
        return ""
```

- [ ] **Step 1.4: 跑测试确认通过**

```bash
python3 -m pytest tests/test_chats.py -q && python3 -m pytest -q
```
预期：全部 PASS。

- [ ] **Step 1.5: Commit**

```bash
git add lark_listener/chats.py tests/test_chats.py
git commit -m "feat: 新增 chats.py——免打扰探测与会话分类（勿扰/普通/特别关注）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: config.py — special_focus / exclude_chats 钳制与兼容

**Files:**
- Modify: `lark_listener/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 2.1: 写失败测试**

在 `tests/test_config.py` 末尾追加（沿用该文件现有的写临时 yaml 的测试风格；若文件中已有写 config 的 helper 则复用之，下面以独立 helper 呈现完整可运行代码）：

```python
import yaml as _yaml


def _write_cfg(tmp_path, extra: dict):
    base = {
        "lark_cli_appid": "cli_x",
        "ai": {"provider": "claude", "model": "m"},
        "notify": {"user_id": "ou_me", "bot_chat_id": "oc_bot"},
    }
    base.update(extra)
    p = tmp_path / "config.yaml"
    p.write_text(_yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    return str(p)


def test_special_focus_defaults(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {}))
    assert cfg["special_focus"] == {"enabled": False, "max_messages": 20, "chats": []}


def test_special_focus_clamps_bad_values(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"special_focus": {
        "enabled": "yes",                      # 非 bool → False
        "max_messages": "abc",                 # 非法 → 20
        "chats": [
            {"chat_id": "oc_a", "keywords": "扩容"},   # 标量 keywords → 单元素列表
            {"name": "缺id"},                          # 缺 chat_id → 丢弃
            "oc_bare",                                 # 非 dict → 丢弃
        ],
    }}))
    sf = cfg["special_focus"]
    assert sf["enabled"] is False
    assert sf["max_messages"] == 20
    assert sf["chats"] == [{"chat_id": "oc_a", "name": "", "keywords": ["扩容"]}]


def test_special_focus_non_dict_falls_back(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"special_focus": "on"}))
    assert cfg["special_focus"] == {"enabled": False, "max_messages": 20, "chats": []}


def test_exclude_chats_new_format(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"exclude_chats": [
        {"chat_id": "oc_a", "name": "A群"},
        {"chat_id": "oc_b"},
    ]}))
    assert cfg["exclude_chats"] == [
        {"chat_id": "oc_a", "name": "A群"},
        {"chat_id": "oc_b", "name": ""},
    ]
    assert exclude_chat_id_set(cfg) == {"oc_a", "oc_b"}


def test_exclude_chats_legacy_key_compat(tmp_path):
    """旧键 exclude_chat_ids（纯 id 列表）兼容读取为新结构。"""
    cfg = load_config(_write_cfg(tmp_path, {"exclude_chat_ids": ["oc_old"]}))
    assert cfg["exclude_chats"] == [{"chat_id": "oc_old", "name": ""}]
    assert "exclude_chat_ids" not in cfg


def test_exclude_chats_bad_entries_dropped(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"exclude_chats": [
        "oc_str",            # 纯 str 兼容为 chat_id
        {"name": "缺id"},    # 丢弃
        None,                # 丢弃
        123,                 # 丢弃（非 str 非 dict）
    ]}))
    assert cfg["exclude_chats"] == [{"chat_id": "oc_str", "name": ""}]


def test_include_at_all_removed_and_ignored(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, {"include_at_all": False}))
    assert "include_at_all" not in cfg
```

注意：文件顶部 import 行需补 `exclude_chat_id_set`：
`from lark_listener.config import load_config, exclude_chat_id_set`（按该文件现有 import 风格并入）。

- [ ] **Step 2.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_config.py -q
```
预期：新增用例 FAIL/ERROR（`exclude_chat_id_set` 不存在、`special_focus` 缺失等）。

- [ ] **Step 2.3: 实现 config.py 改动**

`DEFAULTS` 改为：

```python
DEFAULTS = {
    "poll_interval": 300,
    "context_messages": 20,
    # Optional list fields default to [] so they're always present in the
    # effective config — this lets the bot add the first entry instead of
    # rejecting them as unknown fields.
    "keywords": [],
    "exclude_chats": [],
    "special_focus": {
        "enabled": False,
        "max_messages": 20,
        "chats": [],
    },
    "ai": {
        "base_url": "",
    },
}
```

在 `_normalize_str_list` 之后新增两个归一函数与一个消费 helper：

```python
def _normalize_chat_entries(value: Any, name: str) -> list[dict]:
    """钳制为 [{chat_id: str, name: str}, ...]。兼容旧形态：纯 str 条目视为
    chat_id（exclude_chat_ids 旧格式）；缺 chat_id / 非法条目丢弃并告警。"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        logger.warning("%s 配置应为列表（实际 %r），已忽略", name, value)
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            out.append({"chat_id": item, "name": ""})
        elif isinstance(item, dict) and item.get("chat_id"):
            out.append({"chat_id": str(item["chat_id"]),
                        "name": str(item.get("name") or "")})
        else:
            logger.warning("%s 含非法条目（%r），已丢弃", name, item)
    return out


def _normalize_special_focus(value: Any) -> dict:
    """钳制 special_focus 节：enabled 非 bool → False；max_messages 非法 → 20；
    chats 条目须含 chat_id，keywords 强制 list[str]，坏条目丢弃。"""
    defaults = DEFAULTS["special_focus"]
    if not isinstance(value, dict):
        if value is not None:
            logger.warning("special_focus 配置应为映射（实际 %r），已回退默认", value)
        return copy.deepcopy(defaults)
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        if enabled is not None:
            logger.warning("special_focus.enabled 非 bool（%r），已按 false 处理", enabled)
        enabled = False
    max_messages = _normalize_nonneg_int(
        value.get("max_messages", defaults["max_messages"]),
        "special_focus.max_messages", defaults["max_messages"])
    chats = []
    raw_chats = value.get("chats")
    if raw_chats is not None and not isinstance(raw_chats, (list, tuple)):
        logger.warning("special_focus.chats 配置应为列表（实际 %r），已忽略", raw_chats)
        raw_chats = []
    for item in raw_chats or []:
        if isinstance(item, dict) and item.get("chat_id"):
            chats.append({
                "chat_id": str(item["chat_id"]),
                "name": str(item.get("name") or ""),
                "keywords": _normalize_str_list(
                    item.get("keywords"), "special_focus.chats[].keywords"),
            })
        else:
            logger.warning("special_focus.chats 含非法条目（%r），已丢弃", item)
    return {"enabled": enabled, "max_messages": max_messages, "chats": chats}


def exclude_chat_id_set(config: dict) -> set:
    """exclude_chats 的 chat_id 集合——抓取/守卫消费点的唯一取法。"""
    return {e["chat_id"] for e in config.get("exclude_chats", [])
            if isinstance(e, dict) and e.get("chat_id")}
```

`load_config` 末段（`_validate` 之前）改为：

```python
    config = _deep_merge(DEFAULTS, user_config)
    config["poll_interval"] = _normalize_poll_interval(config.get("poll_interval"))
    config["context_messages"] = _normalize_nonneg_int(
        config.get("context_messages"), "context_messages", DEFAULTS["context_messages"])
    config["keywords"] = _normalize_str_list(config.get("keywords"), "keywords")
    # 旧键兼容：exclude_chat_ids（纯 id 列表）→ exclude_chats（id+name）。
    # 新旧并存时以新键为准；空新键 + 有旧键则用旧键。
    legacy = config.pop("exclude_chat_ids", None)
    if legacy is not None:
        logger.info("检测到旧配置键 exclude_chat_ids，已按 exclude_chats 读取"
                    "（首次回写时自动迁移文件格式）")
    config["exclude_chats"] = _normalize_chat_entries(
        config.get("exclude_chats") or legacy, "exclude_chats")
    config["special_focus"] = _normalize_special_focus(config.get("special_focus"))
    if config.pop("include_at_all", None) is not None:
        logger.info("配置键 include_at_all 已废弃（@所有人 行为改由会话免打扰状态决定），已忽略")
    _validate(config)
    return config
```

- [ ] **Step 2.4: 跑测试**

```bash
python3 -m pytest tests/test_config.py -q && python3 -m pytest -q
```
预期：test_config 全绿。**全量跑会有失败**（main/setup/intent 等仍引用 `exclude_chat_ids`/`include_at_all`，consumers 在后续 Task 修）——记录失败清单，确认失败均属于后续 Task 范围，不属于则当场修。test_main 中 `_fetch_window` 读 `config.get("exclude_chat_ids")` 仍兼容（返回 None → 不排除），允许暂时全绿或少量失败，以后续任务消化为准。

- [ ] **Step 2.5: Commit**

```bash
git add lark_listener/config.py tests/test_config.py
git commit -m "feat: 配置层支持 special_focus 与 exclude_chats（旧键兼容、坏值钳制）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: fetcher.py — registry 注入、@all 分流、特别关注全量抓取

**Files:**
- Modify: `lark_listener/fetcher.py`
- Test: `tests/test_fetcher.py`

- [ ] **Step 3.1: 写失败测试**

`tests/test_fetcher.py` 顶部 import 增加：

```python
from lark_listener.chats import ChatRegistry
```

加 registry 构造 helper（放在 `_mock_run` 之后）：

```python
def _registry(unmuted: dict, special: bool = False) -> ChatRegistry:
    """直接预置未免打扰集合的 registry（不发 refresh 请求）。"""
    r = ChatRegistry(special_enabled=special)
    r._unmuted = dict(unmuted)
    return r
```

**改造既有 4 个 @all 用例**（Fetcher 不再有 include_at_all 参数）：

- `test_fetch_at_all_messages`：构造 `Fetcher(keywords=["测试"], registry=_registry({"oc_group": "全员群"}))`（未免打扰普通群 → @all 收），断言不变（AT_ALL 1 条 / AT_ME 0 条）。
- `test_fetch_at_all_disabled_skips_at_all` 改名 `test_fetch_at_all_muted_group_skipped`：`Fetcher(keywords=["测试"], registry=_registry({}))`（勿扰群），断言 AT_ALL/AT_ME 均 0。
- `test_fetch_at_all_placeholder_content`：同样改用 `registry=_registry({})`，断言不变。
- `test_fetch_at_all_disabled_but_keyword_matches` 改名 `test_fetch_at_all_muted_group_keyword_rescues`：`registry=_registry({})`，断言不变（KEYWORD 1 条，matched_keyword="部署"）。

其余既有用例构造 `Fetcher(keywords=[...])` 的不动（registry=None 默认 → 群按勿扰，对那些用例语义不变）。

**新增用例**（追加到 @all 用例后）：

```python
@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_special_sweep_collects_remaining(mock_run):
    """特别关注群：窗口内剩余消息（未被前面类别认领）整体进 SPECIAL。"""
    special_msgs = [
        {"message_id": "m1", "chat_id": "oc_vip", "chat_name": "VIP群",
         "sender": {"id": "ou_a", "name": "A"}, "msg_type": "text",
         "content": "随便聊聊", "create_time": "1716796800", "chat_type": "group"},
        {"message_id": "m2", "chat_id": "oc_vip", "chat_name": "VIP群",
         "sender": {"id": "ou_b", "name": "B"}, "msg_type": "text",
         "content": "继续聊", "create_time": "1716796900", "chat_type": "group"},
    ]
    mock_run.side_effect = _mock_run([
        _empty_result(),                       # p2p
        _empty_result(),                       # at_me
        _empty_result(),                       # keyword "测试"
        _make_search_result(special_msgs),     # special sweep（合并调用）
    ])
    fetcher = Fetcher(keywords=["测试"], registry=_registry({"oc_vip": "VIP群"}, special=True))
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )
    assert [m["message_id"] for m in result[MessageCategory.SPECIAL]] == ["m1", "m2"]


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_keyword_skips_special_chat(mock_run):
    """归类优先级：特别关注 > 关键词——特别关注群命中关键词的消息归 SPECIAL。"""
    kw_msg = {"message_id": "m_kw", "chat_id": "oc_vip", "chat_name": "VIP群",
              "sender": {"id": "ou_a", "name": "A"}, "msg_type": "text",
              "content": "部署完成", "create_time": "1716796800", "chat_type": "group"}
    mock_run.side_effect = _mock_run([
        _empty_result(),                       # p2p
        _empty_result(),                       # at_me
        _make_search_result([kw_msg]),         # keyword "部署" 命中特别关注群
        _make_search_result([kw_msg]),         # special sweep 捞回同一条
    ])
    fetcher = Fetcher(keywords=["部署"], registry=_registry({"oc_vip": "VIP群"}, special=True))
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )
    assert len(result[MessageCategory.KEYWORD]) == 0
    assert [m["message_id"] for m in result[MessageCategory.SPECIAL]] == ["m_kw"]


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_at_all_in_special_chat_stays_at_all(mock_run):
    """归类优先级：@所有人 > 特别关注——特别关注群的 @all 仍单列 AT_ALL。"""
    at_all = {"message_id": "m_all", "chat_id": "oc_vip", "chat_name": "VIP群",
              "sender": {"id": "ou_a", "name": "A"}, "msg_type": "text",
              "content": "@_all", "create_time": "1716796800", "chat_type": "group"}
    mock_run.side_effect = _mock_run([
        _empty_result(),                       # p2p
        _make_search_result([at_all]),         # at_me（含 @all）
        _empty_result(),                       # keyword
        _empty_result(),                       # special sweep
    ])
    fetcher = Fetcher(keywords=["测试"], registry=_registry({"oc_vip": "VIP群"}, special=True))
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )
    assert [m["message_id"] for m in result[MessageCategory.AT_ALL]] == ["m_all"]
    assert len(result[MessageCategory.SPECIAL]) == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_special_truncates_to_max_messages(mock_run):
    """每群截最近 special_max_messages 条（保最新）。"""
    msgs = [{"message_id": f"m{i}", "chat_id": "oc_vip", "chat_name": "VIP群",
             "sender": {"id": "ou_a", "name": "A"}, "msg_type": "text",
             "content": f"msg{i}", "create_time": str(1716796800 + i),
             "chat_type": "group"} for i in range(5)]
    mock_run.side_effect = _mock_run([
        _empty_result(), _empty_result(), _empty_result(),   # p2p / at_me / keyword
        _make_search_result(msgs),                            # special sweep
    ])
    fetcher = Fetcher(keywords=["测试"], special_max_messages=2,
                      registry=_registry({"oc_vip": "VIP群"}, special=True))
    result = fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )
    assert [m["message_id"] for m in result[MessageCategory.SPECIAL]] == ["m3", "m4"]


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_special_sweep_chunks_by_ten(mock_run):
    """>10 个特别关注群按每批 10 个分块合并调用。"""
    unmuted = {f"oc_{i:02d}": f"群{i}" for i in range(11)}
    mock_run.side_effect = _mock_run([_empty_result()])  # 所有调用都返回空
    fetcher = Fetcher(keywords=[], registry=_registry(unmuted, special=True))
    fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
    )
    # p2p + at_me + 2 个 special 分块 = 4 次调用（keywords 为空）
    assert mock_run.call_count == 4
    chunk_args = [c[0][0] for c in mock_run.call_args_list[2:]]
    assert "--chat-id" in chunk_args[0]
    first_ids = chunk_args[0][chunk_args[0].index("--chat-id") + 1]
    second_ids = chunk_args[1][chunk_args[1].index("--chat-id") + 1]
    assert len(first_ids.split(",")) == 10 and len(second_ids.split(",")) == 1


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_special_respects_exclude(mock_run):
    """排除优先：exclude 的群即使未免打扰也不做特别关注抓取。"""
    mock_run.side_effect = _mock_run([_empty_result()])
    fetcher = Fetcher(keywords=[], registry=_registry({"oc_vip": "VIP群"}, special=True))
    fetcher.fetch(
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        processed_ids=set(),
        exclude_chat_ids={"oc_vip"},
    )
    # 仅 p2p + at_me 两次调用，无 special 分块
    assert mock_run.call_count == 2
```

- [ ] **Step 3.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_fetcher.py -q
```
预期：新增用例 FAIL（`MessageCategory.SPECIAL` 不存在 / Fetcher 无 registry 参数），改造的 @all 用例 ERROR（include_at_all 参数没了之后才会通过——此刻它们仍按旧签名跑、且 Fetcher 还没改，FAIL/ERROR 均属预期）。

- [ ] **Step 3.3: 实现 fetcher.py**

`MessageCategory` 增加成员（保持现有成员不动）：

```python
class MessageCategory(Enum):
    P2P = "p2p"
    AT_ME = "at_me"
    KEYWORD = "keyword"
    AT_ALL = "at_all"
    SPECIAL = "special"
```

文件顶部 import 增加：

```python
from lark_listener.chats import ChatClass
```

模块级 helper（放在 `MessageCategory` 之后）：

```python
def _chunked(seq: list, n: int) -> list:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


# 合并抓取的每批会话数：限制单次 --chat-id 长度与单调用分页预算。
_CHAT_BATCH = 10
```

`Fetcher.__init__` 改签名：

```python
    def __init__(self, keywords: Optional[list[str]] = None,
                 registry=None, special_max_messages: int = 20):
        self.keywords = keywords or []
        # registry=None（降级/兼容态）→ 群一律按勿扰、无特别关注抓取。
        self.registry = registry
        self.special_max_messages = special_max_messages
        self._chat_name_cache: dict[str, Optional[str]] = {}
        self._app_name_failed: set[str] = set()
```

（注意保留原 `__init__` 中已有的缓存字段——以现文件为准，仅删除 `include_at_all`、新增 `registry`/`special_max_messages`。）

`fetch()` 中 @all 分流与关键词段改为，并在关键词段后追加 special sweep：

```python
    def _classify(self, msg: dict) -> ChatClass:
        if self.registry is None:
            return (ChatClass.MUTED if msg.get("chat_type") == "group"
                    else ChatClass.NORMAL)
        return self.registry.classify(msg.get("chat_id") or "",
                                      msg.get("chat_type", ""))
```

（`_classify` 作为 Fetcher 方法加入。）fetch 主体：

```python
        # 2. @me / @all messages in groups
        at_msgs = self._search(start, end, chat_type="group", is_at_me=True)
        for msg in at_msgs:
            mid = msg["message_id"]
            if mid in seen_ids or msg.get("chat_id") in _exclude:
                continue
            content = msg.get("content", "")
            # "@_all" 是飞书原始 content 的 @所有人 占位符（搜索 API 的
            # is_at_me 把 @所有人 也算「@我」返回）。
            is_at_all = ("@everyone" in content or "@所有人" in content
                         or "@all" in content or "@_all" in content)
            if is_at_all and self._classify(msg) is ChatClass.MUTED:
                # 勿扰群 @所有人：仅命中关键词才收——跳过且不标 seen，
                # 留给关键词搜索捞（命中即归关键词区）。
                continue
            cat = MessageCategory.AT_ALL if is_at_all else MessageCategory.AT_ME
            result[cat].append(msg)
            seen_ids.add(mid)

        # 3. Keyword matches
        for keyword in self.keywords:
            kw_msgs = self._search(start, end, query=keyword)
            for msg in kw_msgs:
                mid = msg["message_id"]
                if mid not in seen_ids and msg.get("chat_id") not in _exclude:
                    if self._classify(msg) is ChatClass.SPECIAL:
                        # 归类优先级：特别关注 > 关键词——特别关注群的命中
                        # 消息由下方全量抓取统一认领（不标 seen）。
                        continue
                    msg["matched_keyword"] = keyword
                    result[MessageCategory.KEYWORD].append(msg)
                    seen_ids.add(mid)

        # 4. 特别关注群全量抓取（合并调用：chat_id 逗号分隔，每批 _CHAT_BATCH 个）
        special_ids = [cid for cid in
                       (self.registry.special_chat_ids() if self.registry else [])
                       if cid not in _exclude]
        for chunk in _chunked(special_ids, _CHAT_BATCH):
            msgs = self._search(start, end, chat_id=",".join(chunk))
            by_chat: dict[str, list] = {}
            for m in msgs:
                if m["message_id"] in seen_ids or m.get("chat_id") in _exclude:
                    continue
                by_chat.setdefault(m.get("chat_id") or "unknown", []).append(m)
            for cid, chat_msgs in by_chat.items():
                chat_msgs.sort(key=lambda m: m.get("create_time", ""))
                dropped = len(chat_msgs) - self.special_max_messages
                if dropped > 0:
                    # no silent caps：截断必须留痕
                    logger.info("特别关注群 %s 本轮 %d 条超出上限 %d，丢弃最早 %d 条",
                                cid, len(chat_msgs), self.special_max_messages, dropped)
                    chat_msgs = chat_msgs[-self.special_max_messages:]
                for m in chat_msgs:
                    result[MessageCategory.SPECIAL].append(m)
                    seen_ids.add(m["message_id"])
```

（p2p 段与 `_fill_chat_names` / `_fill_app_sender_names` 调用保持不变；`_fill_chat_names` 遍历的类别元组增加 `MessageCategory.SPECIAL`。）

- [ ] **Step 3.4: 跑测试**

```bash
python3 -m pytest tests/test_fetcher.py -q && python3 -m pytest -q
```
预期：test_fetcher 全绿。全量可能有 test_main 失败（main 仍传 include_at_all）——属 Task 7 范围；其余失败当场修。

- [ ] **Step 3.5: Commit**

```bash
git add lark_listener/fetcher.py tests/test_fetcher.py
git commit -m "feat: fetcher 按会话分类分流 @所有人 并全量抓取特别关注群

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: fetcher.fetch_context — 合并抓取与特别关注跳过

**Files:**
- Modify: `lark_listener/fetcher.py`（`fetch_context`）
- Test: `tests/test_fetcher.py`

- [ ] **Step 4.1: 写失败测试**

```python
@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_context_merges_chats_into_one_call(mock_run):
    """多个命中会话的上下文合并为一次 --chat-id 逗号分隔调用，本地分组截断。"""
    ctx_msgs = [
        {"message_id": "c1", "chat_id": "oc_1", "sender": {"id": "ou_x", "name": "X"},
         "msg_type": "text", "content": "ctx1", "create_time": "1716796700"},
        {"message_id": "c2", "chat_id": "oc_2", "sender": {"id": "ou_y", "name": "Y"},
         "msg_type": "text", "content": "ctx2", "create_time": "1716796710"},
    ]
    mock_run.side_effect = _mock_run([_make_search_result(ctx_msgs)])
    fetcher = Fetcher(keywords=[])
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.P2P] = [
        {"message_id": "p1", "chat_id": "oc_1"},
        {"message_id": "p2", "chat_id": "oc_2"},
    ]
    context = fetcher.fetch_context(
        categorized,
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        limit=20,
    )
    assert mock_run.call_count == 1
    args = mock_run.call_args_list[0][0][0]
    chat_arg = args[args.index("--chat-id") + 1]
    assert sorted(chat_arg.split(",")) == ["oc_1", "oc_2"]
    assert [m["message_id"] for m in context["oc_1"]] == ["c1"]
    assert [m["message_id"] for m in context["oc_2"]] == ["c2"]


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_context_skips_special_chats(mock_run):
    """特别关注群的全量已在 SPECIAL 类别里，上下文抓取跳过它。"""
    mock_run.side_effect = _mock_run([_empty_result()])
    fetcher = Fetcher(keywords=[])
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.SPECIAL] = [{"message_id": "s1", "chat_id": "oc_vip"}]
    context = fetcher.fetch_context(
        categorized,
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
    )
    assert context == {}
    assert mock_run.call_count == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_context_per_chat_limit(mock_run):
    """合并拉回后每会话各自截最近 limit 条。"""
    ctx_msgs = [{"message_id": f"c{i}", "chat_id": "oc_1",
                 "sender": {"id": "ou_x", "name": "X"}, "msg_type": "text",
                 "content": f"m{i}", "create_time": str(1716796700 + i)}
                for i in range(5)]
    mock_run.side_effect = _mock_run([_make_search_result(ctx_msgs)])
    fetcher = Fetcher(keywords=[])
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.P2P] = [{"message_id": "p1", "chat_id": "oc_1"}]
    context = fetcher.fetch_context(
        categorized,
        datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ),
        datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ),
        limit=2,
    )
    assert [m["message_id"] for m in context["oc_1"]] == ["c3", "c4"]
```

既有的 fetch_context 用例若断言「每会话一次调用」需同步改为合并语义（按跑出的失败逐个对齐预期，保持被测行为与上述一致）。

- [ ] **Step 4.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_fetcher.py -k fetch_context -q
```
预期：新用例 FAIL（当前实现按 chat 逐个调用、不跳过 SPECIAL）。

- [ ] **Step 4.3: 实现 fetch_context**

整体替换为：

```python
    def fetch_context(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch surrounding messages for each chat to provide AI context.

        合并抓取：所有目标会话 chat_id 逗号分隔、每批 _CHAT_BATCH 个一次调用，
        拉回后本地按 chat 分组、各截最近 limit 条。特别关注群跳过——其窗口
        全量已在 SPECIAL 类别里，再拉一遍纯属浪费。"""
        special_chats = {m.get("chat_id")
                         for m in categorized.get(MessageCategory.SPECIAL, [])}
        chat_matched_ids: dict[str, set[str]] = {}
        for msgs in categorized.values():
            for msg in msgs:
                chat_id = msg.get("chat_id", "")
                if chat_id and chat_id not in special_chats:
                    chat_matched_ids.setdefault(chat_id, set()).add(msg["message_id"])

        context: dict[str, list[dict[str, Any]]] = {}
        # sorted：分块组合确定，单测可断言每批的 chat_id 组成
        for chunk in _chunked(sorted(chat_matched_ids), _CHAT_BATCH):
            all_msgs = self._search(start, end, chat_id=",".join(chunk))
            by_chat: dict[str, list[dict[str, Any]]] = {}
            for m in all_msgs:
                by_chat.setdefault(m.get("chat_id", ""), []).append(m)
            for chat_id in chunk:
                ctx_msgs = [m for m in by_chat.get(chat_id, [])
                            if m["message_id"] not in chat_matched_ids[chat_id]]
                ctx_msgs.sort(key=lambda m: m.get("create_time", ""))
                ctx_msgs = ctx_msgs[-limit:]
                if ctx_msgs:
                    context[chat_id] = ctx_msgs
        return context
```

- [ ] **Step 4.4: 跑测试**

```bash
python3 -m pytest tests/test_fetcher.py -q && python3 -m pytest -q
```

- [ ] **Step 4.5: Commit**

```bash
git add lark_listener/fetcher.py tests/test_fetcher.py
git commit -m "perf: 上下文抓取合并为常数次调用并跳过特别关注群

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: notifier.py — SPECIAL 类别与顺序统一

**Files:**
- Modify: `lark_listener/notifier.py`
- Test: `tests/test_notifier.py`

- [ ] **Step 5.1: 写失败测试**

```python
def test_category_order_matches_priority():
    """卡片/封套顺序与归类优先级一致：私聊 > @我 > @所有人 > 特别关注 > 关键词。"""
    messages = {
        MessageCategory.P2P: [_make_msg("m1", "oc_p", "ou_a", "甲", "hi")],
        MessageCategory.AT_ME: [_make_msg("m2", "oc_g1", "ou_b", "乙", "@你", chat_name="G1")],
        MessageCategory.AT_ALL: [_make_msg("m3", "oc_g2", "ou_c", "丙", "@_all", chat_name="G2")],
        MessageCategory.SPECIAL: [_make_msg("m4", "oc_vip", "ou_d", "丁", "聊", chat_name="VIP")],
        MessageCategory.KEYWORD: [_make_msg("m5", "oc_g3", "ou_e", "戊", "部署",
                                            chat_name="G3", matched_keyword="部署")],
    }
    convs = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)["data"]["conversations"]
    assert [c["category"] for c in convs] == ["p2p", "at_me", "at_all", "special", "keyword"]


def test_card_renders_special_section():
    messages = {cat: [] for cat in MessageCategory}
    messages[MessageCategory.SPECIAL] = [
        _make_msg("m1", "oc_vip", "ou_a", "甲", "聊天内容", chat_name="VIP群")]
    card = build_summary_card(build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID))
    headers = [col["display_name"] for el in card["body"]["elements"]
               for col in el["columns"]]
    assert any("特别关注" in h for h in headers)
    assert any("🟪" in h for h in headers)


def test_same_chat_splits_into_at_me_and_special_rows():
    """spec §1：特别关注群里 @我 的消息单独成行，剩余消息进特别关注行——
    同一个群两行（分组键必须含 category，否则 SPECIAL 余量会并进 @我 行）。"""
    messages = {cat: [] for cat in MessageCategory}
    messages[MessageCategory.AT_ME] = [
        _make_msg("m1", "oc_vip", "ou_a", "甲", "@你 看一下", chat_name="VIP群")]
    messages[MessageCategory.SPECIAL] = [
        _make_msg("m2", "oc_vip", "ou_b", "乙", "其余闲聊", chat_name="VIP群")]
    convs = build_summary_response(messages, {}, "15:00", "15:30", MY_USER_ID)["data"]["conversations"]
    assert [(c["category"], c["chat_id"]) for c in convs] == [
        ("at_me", "oc_vip"), ("special", "oc_vip")]
    # 两行各自只含本类别消息
    assert [c["count"] for c in convs] == [1, 1]
```

注意：`SAMPLE_MESSAGES` 等既有 fixture 已包含全部 `MessageCategory` 成员的键（用 dict comprehension 的不受影响；显式列出四类的 fixture 需补 `MessageCategory.SPECIAL: []`）。既有断言分区顺序的用例（如卡片表头顺序）按新顺序更新。

- [ ] **Step 5.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_notifier.py -q
```
预期：新用例 FAIL（无 SPECIAL 类别/顺序不符），部分既有用例因 fixture 缺 SPECIAL 键 KeyError——一并属预期。

- [ ] **Step 5.3: 实现 notifier.py**

三个常量更新：

```python
_CATEGORY_ORDER = [
    (MessageCategory.P2P, "私聊消息"),
    (MessageCategory.AT_ME, "@我"),
    (MessageCategory.AT_ALL, "@所有人"),
    (MessageCategory.SPECIAL, "特别关注"),
    (MessageCategory.KEYWORD, "关键词命中"),
]

_CATEGORY_SHORT = [
    (MessageCategory.P2P, "私聊"),
    (MessageCategory.AT_ME, "@我"),
    (MessageCategory.AT_ALL, "@所有人"),
    (MessageCategory.SPECIAL, "特别关注"),
    (MessageCategory.KEYWORD, "关键词命中"),
]

_CATEGORY_EMOJI = {
    "p2p": "🟦",
    "at_me": "🟩",
    "at_all": "🟥",
    "special": "🟪",
    "keyword": "🟧",
}
```

（顺序注释同步改为「与归类优先级一致：私聊 > @我 > @所有人 > 特别关注 > 关键词」。`_group_by_chat` 中 `cat != MessageCategory.P2P` 的群名捕获对 SPECIAL 自动生效。）

**`_group_by_chat` 分组键改为 (category, chat_id)**——否则同一个群跨类别的消息会并进首个类别的组，特别关注群的余量消息将混入 @我 行、特别关注行消失（违背 spec §1「@我 仍单列」）。完整替换：

```python
def _group_by_chat(
    categorized: dict[MessageCategory, list[dict[str, Any]]],
) -> dict[tuple, dict]:
    """Group messages by (category, chat_id).

    键含 category：同一个群可同时出现在 @我 行与特别关注行（spec §1，
    用户确认的「两行」语义）。同群多行共享同一段 AI 摘要（analysis 按
    chat_id 一份）——这是按类别拆行的自然代价。"""
    groups: dict[tuple, dict] = {}
    for cat, msgs in categorized.items():
        for msg in msgs:
            # `or` 而非 .get 默认值：真实数据见过 chat_id 显式为 null，
            # None 流进 _conversation_row 的 chat_id[-8:] 会 TypeError。
            chat_id = msg.get("chat_id") or "unknown"
            key = (cat.value, chat_id)
            if key not in groups:
                groups[key] = {
                    "chat_id": chat_id,
                    "category": cat,
                    "messages": [],
                    "chat_name": "",
                    "matched_keyword": msg.get("matched_keyword", ""),
                }
            groups[key]["messages"].append(msg)
            # Capture the group name (p2p partner name is resolved later in
            # _conversation_row directly from the messages).
            if cat != MessageCategory.P2P and msg.get("chat_name"):
                groups[key]["chat_name"] = msg["chat_name"]
    return groups
```

`build_summary_response` 消费侧无需改逻辑（`groups.values()` 遍历、按 `g["category"]` 过滤、纯自发组过滤均与键形状无关），但确认 0.1.3 的纯自发过滤推导式仍以 `groups.items()` 形态工作（键变 tuple 不影响）。

- [ ] **Step 5.4: 跑测试**

```bash
python3 -m pytest tests/test_notifier.py -q && python3 -m pytest -q
```

- [ ] **Step 5.5: Commit**

```bash
git add lark_listener/notifier.py tests/test_notifier.py
git commit -m "feat: 卡片新增特别关注区，展示顺序与归类优先级统一

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: analyzer.py — 特别关注标注与 prompt 增强

**Files:**
- Modify: `lark_listener/analyzer.py`
- Test: `tests/test_analyzer.py`

- [ ] **Step 6.1: 写失败测试**

```python
def _capture_prompt(analyzer, categorized, **kwargs):
    captured = {}
    def fake_call(prompt):
        captured["prompt"] = prompt
        return []
    analyzer._call_ai = fake_call
    analyzer.analyze(categorized, **kwargs)
    return captured["prompt"]


def test_analyze_marks_special_chat_with_bound_keywords():
    analyzer = Analyzer(provider="claude", model="m", api_key="k",
                        base_url="", keywords=["SDK"])
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.SPECIAL] = [{
        "message_id": "m1", "chat_id": "oc_vip",
        "sender": {"id": "ou_a", "name": "甲"},
        "msg_type": "text", "content": "聊扩容", "create_time": "1716796800",
    }]
    prompt = _capture_prompt(analyzer, categorized,
                             special_chats={"oc_vip": ["扩容"]})
    assert "--- conversation_id: oc_vip [特别关注]（本群关注关键词：扩容） ---" in prompt
    assert "标注为 [特别关注] 的会话" in prompt


def test_analyze_marks_special_chat_without_bound_keywords():
    analyzer = Analyzer(provider="claude", model="m", api_key="k",
                        base_url="", keywords=["SDK"])
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.SPECIAL] = [{
        "message_id": "m1", "chat_id": "oc_vip",
        "sender": {"id": "ou_a", "name": "甲"},
        "msg_type": "text", "content": "随便聊", "create_time": "1716796800",
    }]
    prompt = _capture_prompt(analyzer, categorized, special_chats={"oc_vip": []})
    assert "--- conversation_id: oc_vip [特别关注] ---" in prompt


def test_analyze_no_special_marks_when_not_passed():
    analyzer = Analyzer(provider="claude", model="m", api_key="k",
                        base_url="", keywords=["SDK"])
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.P2P] = [{
        "message_id": "m1", "chat_id": "oc_p",
        "sender": {"id": "ou_a", "name": "甲"},
        "msg_type": "text", "content": "hi", "create_time": "1716796800",
    }]
    prompt = _capture_prompt(analyzer, categorized)
    assert "[特别关注]" not in prompt.split("会话列表：")[1]
```

（若 test_analyzer 已有 prompt 捕获 helper，则复用其风格。）

- [ ] **Step 6.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_analyzer.py -q
```
预期：FAIL（analyze 不接受 special_chats / 无标注段落）。

- [ ] **Step 6.3: 实现 analyzer.py**

`USER_PROMPT_TEMPLATE` 在「请对每个会话…」之前插入一段（完整新模板）：

```python
USER_PROMPT_TEMPLATE = """\
用户关注的关键词：{keywords}

以下是按会话分组的消息。标记为 [我] 的是用户自己发的消息，标记为 [上下文] 的是前后相关消息，两者仅作为理解上下文使用。
标注为 [特别关注] 的会话是用户重点关注的群：其 summary 除常规概括外，还须围绕用户关注的关键词（及该会话标注的本群关注关键词，如有）展开分析，relevance 按合并后的关键词集合评估。
请对每个会话（conversation_id）进行整体分析，输出：
1. conversation_id: 会话 ID
2. relevance: 该会话与关键词的语义相关度（high/medium/low）
3. urgency: 紧急度（urgent/normal/low）
4. summary: 用一两句话概括该会话的核心内容和要点
5. relevant_message_id: 该会话中与关键词最相关的那条消息的 ID（不要选 [我] 的消息），如果都不相关则选最后一条非 [我] 的消息

输出格式为 JSON 数组：
[{{"conversation_id": "...", "relevance": "...", "urgency": "...", "summary": "...", "relevant_message_id": "..."}}]

会话列表：
{conversations}"""
```

`analyze` 签名与会话块头：

```python
    def analyze(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
        my_user_id: str = "",
        context: Optional[dict[str, list[dict[str, Any]]]] = None,
        special_chats: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, ConversationAnalysis]:
```

会话块头行（替换 `lines = [f"--- conversation_id: {chat_id} ---"]`）：

```python
            tag = ""
            if special_chats and chat_id in special_chats:
                bound = special_chats[chat_id]
                tag = " [特别关注]"
                if bound:
                    tag += f"（本群关注关键词：{'、'.join(bound)}）"
            lines = [f"--- conversation_id: {chat_id}{tag} ---"]
```

- [ ] **Step 6.4: 跑测试**

```bash
python3 -m pytest tests/test_analyzer.py -q && python3 -m pytest -q
```

- [ ] **Step 6.5: Commit**

```bash
git add lark_listener/analyzer.py tests/test_analyzer.py
git commit -m "feat: AI 分析标注特别关注会话并注入本群关注关键词

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: main.py — 接线（registry、新 Fetcher 签名、special_chats）

**Files:**
- Modify: `lark_listener/main.py`（`_fetch_window` / `_analyze_window` / 模块级 registry）
- Test: `tests/test_main.py`

- [ ] **Step 7.1: 写失败测试**

`tests/test_main.py` 增加 autouse fixture（放在文件顶部 fixture 区；并为既有 poll_once 用例提供 registry 隔离）：

```python
import pytest
from lark_listener import main as main_mod


class _FakeRegistry:
    def __init__(self, special_enabled=False):
        self.special_enabled = special_enabled
    def refresh(self):
        return False
    def classify(self, chat_id, chat_type):
        from lark_listener.chats import ChatClass
        return ChatClass.MUTED if chat_type == "group" else ChatClass.NORMAL
    def special_chat_ids(self):
        return []
    def name_of(self, chat_id):
        return ""


@pytest.fixture(autouse=True)
def _stub_chat_registry(monkeypatch):
    """poll_once/_fetch_window 不真发 chat-list；每个用例重置全局 registry。"""
    monkeypatch.setattr(main_mod, "_chat_registry", None, raising=False)
    monkeypatch.setattr(main_mod, "ChatRegistry", _FakeRegistry, raising=False)
    yield
    main_mod._chat_registry = None
```

新增用例：

```python
def test_fetch_window_builds_registry_and_fetcher(tmp_path, monkeypatch):
    """_fetch_window：建 registry、refresh、按 special_focus 配置组装 Fetcher。"""
    captured = {}

    class _SpyFetcher:
        def __init__(self, keywords=None, registry=None, special_max_messages=20):
            captured["keywords"] = keywords
            captured["registry"] = registry
            captured["special_max_messages"] = special_max_messages
        def fetch(self, start, end, processed_ids, exclude_chat_ids=None):
            captured["exclude"] = exclude_chat_ids
            return {cat: [] for cat in MessageCategory}

    monkeypatch.setattr(main_mod, "Fetcher", _SpyFetcher)
    config = {
        "keywords": ["SDK"],
        "exclude_chats": [{"chat_id": "oc_bot", "name": ""}],
        "special_focus": {"enabled": True, "max_messages": 5, "chats": []},
    }
    start = datetime(2026, 6, 11, 10, 0, tzinfo=TZ)
    end = datetime(2026, 6, 11, 11, 0, tzinfo=TZ)
    main_mod._fetch_window(config, start, end, set())
    assert isinstance(captured["registry"], _FakeRegistry)
    assert captured["registry"].special_enabled is True
    assert captured["special_max_messages"] == 5
    assert captured["exclude"] == {"oc_bot"}


def test_analyze_window_passes_special_chats(monkeypatch):
    """_analyze_window：把「出现在本轮且属特别关注」的会话与绑定词传给 analyzer。"""
    captured = {}

    class _SpyAnalyzer:
        def __init__(self, **kwargs):
            pass
        def analyze(self, categorized, my_user_id="", context=None, special_chats=None):
            captured["special_chats"] = special_chats
            return {}

    monkeypatch.setattr(main_mod, "Analyzer", _SpyAnalyzer)

    class _Reg(_FakeRegistry):
        def special_chat_ids(self):
            return ["oc_vip", "oc_quiet_this_round"]

    class _F:
        registry = _Reg()
        def fetch_context(self, *a, **k):
            return {}

    config = {
        "context_messages": 0,
        "ai": {"provider": "claude", "model": "m"},
        "special_focus": {"enabled": True, "max_messages": 20,
                          "chats": [{"chat_id": "oc_vip", "name": "", "keywords": ["扩容"]}]},
    }
    categorized = {cat: [] for cat in MessageCategory}
    categorized[MessageCategory.SPECIAL] = [{"message_id": "m1", "chat_id": "oc_vip"}]
    start = datetime(2026, 6, 11, 10, 0, tzinfo=TZ)
    end = datetime(2026, 6, 11, 11, 0, tzinfo=TZ)
    main_mod._analyze_window(config, _F(), categorized, start, end, "ou_me")
    # 只包含本轮出现的特别关注会话；绑定词跟随
    assert captured["special_chats"] == {"oc_vip": ["扩容"]}
```

（test_main 顶部已有 `MessageCategory`/`TZ`/`datetime` import 则复用；否则补。）

- [ ] **Step 7.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_main.py -q
```
预期：新用例 FAIL（main 无 ChatRegistry / Fetcher 仍传 include_at_all / analyze 无 special_chats）。

- [ ] **Step 7.3: 实现 main.py**

import 区增加：

```python
from lark_listener.chats import ChatRegistry
from lark_listener.config import exclude_chat_id_set
```

模块级（放 `_fetch_window` 之前）：

```python
# 守护进程内跨轮复用同一 registry：refresh 失败时保留上一轮 mute 结果
# （spec §2 降级）。cmd_summarize 子进程各自新建，首刷失败则全按勿扰。
_chat_registry: Optional[ChatRegistry] = None


def _get_chat_registry(special_enabled: bool) -> ChatRegistry:
    global _chat_registry
    if _chat_registry is None:
        _chat_registry = ChatRegistry(special_enabled=special_enabled)
    _chat_registry.special_enabled = special_enabled
    return _chat_registry
```

`_fetch_window` 替换为：

```python
def _fetch_window(config, start, end, processed_ids):
    """拉取 [start, end) 内的相关消息。返回 (categorized, fetcher)。
    fetcher 一并返回，供 _analyze_window 取上下文与特别关注判定（同一实例）。"""
    exclude_ids = exclude_chat_id_set(config)
    sf = config.get("special_focus") or {}
    registry = _get_chat_registry(bool(sf.get("enabled")))
    registry.refresh()
    fetcher = Fetcher(
        keywords=config.get("keywords", []),
        registry=registry,
        special_max_messages=sf.get("max_messages", 20),
    )
    categorized = fetcher.fetch(
        start, end,
        processed_ids=processed_ids,
        exclude_chat_ids=exclude_ids or None,
    )
    return categorized, fetcher
```

`_analyze_window` 末段改为：

```python
    sf = config.get("special_focus") or {}
    bound = {c["chat_id"]: c.get("keywords", [])
             for c in sf.get("chats", [])
             if isinstance(c, dict) and c.get("chat_id")}
    registry = getattr(fetcher, "registry", None)
    special_set = set(registry.special_chat_ids()) if registry else set()
    all_chat_ids = {m.get("chat_id") for msgs in categorized.values()
                    for m in msgs if m.get("chat_id")}
    special_chats = {cid: bound.get(cid, []) for cid in all_chat_ids & special_set}
    ai_cfg = config["ai"]
    analyzer = Analyzer(
        provider=ai_cfg["provider"],
        model=ai_cfg["model"],
        api_key=ai_cfg.get("api_key", ""),
        base_url=ai_cfg.get("base_url", ""),
        keywords=config.get("keywords", []),
    )
    return analyzer.analyze(categorized, my_user_id=my_user_id, context=context,
                            special_chats=special_chats or None)
```

- [ ] **Step 7.4: 跑全量测试**

```bash
python3 -m pytest -q
```
预期：全绿（setup/intent 残留引用在 Task 8/9/12 处理，如有失败确认属于后续任务清单）。

- [ ] **Step 7.5: Commit**

```bash
git add lark_listener/main.py tests/test_main.py
git commit -m "feat: 守护循环接入会话分类（registry 每轮刷新、特别关注词下传 AI）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: config_editor + intent — bot 改配置适配新结构

**Files:**
- Modify: `lark_listener/config_editor.py`、`lark_listener/intent.py`
- Test: `tests/test_config_editor.py`、`tests/test_intent.py`

- [ ] **Step 8.1: 写失败测试**

`tests/test_config_editor.py`（既有 EFFECTIVE fixture 中 `include_at_all`/`exclude_chat_ids` 键改为新结构：`"exclude_chats": [{"chat_id": "oc_bot", "name": ""}]` + `"special_focus": {"enabled": False, "max_messages": 20, "chats": []}`，相关既有用例同步改键名）。新增：

```python
def test_exclude_chats_add_by_chat_id():
    resolved, err = _plan_changes(
        [{"field": "exclude_chats", "op": "add", "value": "oc_new"}], EFFECTIVE)
    assert err is None
    field, new_value, _ = resolved[0]
    assert {"chat_id": "oc_new", "name": ""} in new_value


def test_exclude_chats_remove_bot_chat_rejected():
    _, err = compute_diff(
        [{"field": "exclude_chats", "op": "remove", "value": "oc_bot"}], EFFECTIVE)
    assert err and "bot 会话不可移除" in err


def test_exclude_chats_remove_other_ok():
    cfg = dict(EFFECTIVE)
    cfg["exclude_chats"] = [{"chat_id": "oc_bot", "name": ""},
                            {"chat_id": "oc_x", "name": "X群"}]
    resolved, err = _plan_changes(
        [{"field": "exclude_chats", "op": "remove", "value": "oc_x"}], cfg)
    assert err is None
    assert resolved[0][1] == [{"chat_id": "oc_bot", "name": ""}]


def test_special_focus_dotted_scalar_set():
    resolved, err = _plan_changes(
        [{"field": "special_focus.enabled", "op": "set", "value": "true"}], EFFECTIVE)
    assert err is None
    assert resolved[0][1] is True


def test_special_focus_dict_field_rejected():
    _, err = compute_diff(
        [{"field": "special_focus", "op": "set", "value": "on"}], EFFECTIVE)
    assert err and "special_focus.enabled" in err


def test_removes_bot_chat_handles_dict_entries():
    cur = [{"chat_id": "oc_bot", "name": ""}]
    assert removes_bot_chat("oc_bot", cur, []) is True
    assert removes_bot_chat("oc_bot", cur, cur) is False
    # 旧形态纯 str 仍兼容
    assert removes_bot_chat("oc_bot", ["oc_bot"], []) is True
```

`tests/test_intent.py`：断言 prompt 文案含新键说明（按该文件现有 parse/prompt 测试风格）：

```python
def test_intent_prompt_mentions_new_fields():
    from lark_listener.intent import INTENT_PROMPT
    assert "exclude_chats" in INTENT_PROMPT
    assert "special_focus.enabled" in INTENT_PROMPT
    assert "exclude_chat_ids" not in INTENT_PROMPT
```

- [ ] **Step 8.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_config_editor.py tests/test_intent.py -q
```

- [ ] **Step 8.3: 实现**

`config_editor.py`：

1. `_coerce_scalar` 第一行去掉特例：`if isinstance(current, bool):`
2. `removes_bot_chat` 替换为（dict/str 双形态）：

```python
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
```

3. 新增 `_apply_chat_list_op`（紧跟 `_apply_list_op`）：

```python
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
```

4. `_plan_changes` 改造（点号嵌套标量 + 字段路由）：

```python
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
```

`_plan_changes` 主体：

```python
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
            continue
        resolved.append((field, new_value, f"{field}: {current!r} → {new_value!r}"))
```

5. `apply_changes` 写入支持点号：

```python
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
```

`intent.py`：

- `_editable_config` docstring 中 `exclude_chat_ids` 改为 `exclude_chats`。
- `INTENT_PROMPT` 的 config_modify 段补一句（紧跟「列表字段…」行后）：

```
  exclude_chats 的 add/remove 值为会话 chat_id（如 "oc_xxx"）；
  special_focus.enabled / special_focus.max_messages 是嵌套标量，field 写点号路径；
  special_focus.chats 不可经 bot 修改（请直接编辑配置文件）。
```

- [ ] **Step 8.4: 跑测试**

```bash
python3 -m pytest tests/test_config_editor.py tests/test_intent.py -q && python3 -m pytest -q
```

- [ ] **Step 8.5: Commit**

```bash
git add lark_listener/config_editor.py lark_listener/intent.py tests/test_config_editor.py tests/test_intent.py
git commit -m "feat: bot 改配置适配 exclude_chats 新结构与 special_focus 点号路径

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: config_cli — exclude_chats 守卫与按 chat_id 增删

**Files:**
- Modify: `lark_listener/config_cli.py`
- Test: `tests/test_config_cli.py`

- [ ] **Step 9.1: 写失败测试**

`tests/test_config_cli.py`（沿用既有 tmp config 写入 helper；既有用例中的 `exclude_chat_ids` 键名与断言同步改为 `exclude_chats` 新结构）。新增：

```python
def test_config_set_exclude_chats_add(tmp_cfg):
    rc = config_set("exclude_chats", "oc_new", add=True, path=tmp_cfg)
    assert rc == 0
    cfg = config_mod.load_config(str(tmp_cfg))
    assert {"chat_id": "oc_new", "name": ""} in cfg["exclude_chats"]


def test_config_set_exclude_chats_remove_bot_guarded(tmp_cfg):
    rc = config_set("exclude_chats", "oc_bot", remove=True, path=tmp_cfg)
    assert rc == 1   # 防自反馈守卫


def test_config_set_exclude_chats_remove_bot_forced(tmp_cfg):
    rc = config_set("exclude_chats", "oc_bot", remove=True, force=True, path=tmp_cfg)
    assert rc == 0


def test_config_set_special_focus_enabled(tmp_cfg):
    rc = config_set("special_focus.enabled", "true", path=tmp_cfg)
    assert rc == 0
    cfg = config_mod.load_config(str(tmp_cfg))
    assert cfg["special_focus"]["enabled"] is True


def test_config_set_special_focus_chats_rejected(tmp_cfg):
    rc = config_set("special_focus.chats", "oc_x", add=True, path=tmp_cfg)
    assert rc == 1
```

（`tmp_cfg` fixture 的配置文件需含 `exclude_chats: [{chat_id: oc_bot, name: ""}]` 与 `special_focus: {enabled: false, max_messages: 20, chats: []}`；若既有 fixture 是函数式构造则照其风格。）

- [ ] **Step 9.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_config_cli.py -q
```

- [ ] **Step 9.3: 实现 config_cli.py**

1. import 行补 `_apply_chat_list_op`。
2. 列表操作路由（`config_set` 中 `if add or remove:` 段及 set-列表段）：

```python
    if leaf == "chats" and "special_focus" in parts:
        print("❌ special_focus.chats 结构含每群关键词，请直接编辑 config.yaml")
        return 1
    if add or remove:
        if not isinstance(current, list):
            print(f"❌ {key} 不是列表，--add/--remove 不适用")
            return 1
        op = "add" if add else "remove"
        if leaf == "exclude_chats":
            new_value, err = _apply_chat_list_op(current, op, value)
        else:
            new_value, err = _apply_list_op(current, op, value)
    elif isinstance(current, list):
        items = [v.strip() for v in value.split(",") if v.strip()]
        if leaf == "exclude_chats":
            new_value, err = _apply_chat_list_op(current, "set", items)
        else:
            new_value, err = _apply_list_op(current, "set", items)
    else:
        new_value, err = _coerce_scalar(leaf, value, current)
```

3. 守卫键名：`if leaf == "exclude_chats" and not force:`（其余不变，`removes_bot_chat` 已在 Task 8 兼容 dict 条目）。
4. 列表空值兜底（line 100）改为同时认识嵌套默认：

```python
    if current is None:
        default_leaf = config_mod.DEFAULTS.get(leaf)
        if default_leaf is None and len(parts) == 2:
            default_leaf = (config_mod.DEFAULTS.get(parts[0]) or {}).get(leaf)
        if isinstance(default_leaf, list):
            current = []
```

- [ ] **Step 9.4: 跑测试**

```bash
python3 -m pytest tests/test_config_cli.py -q && python3 -m pytest -q
```

- [ ] **Step 9.5: Commit**

```bash
git add lark_listener/config_cli.py tests/test_config_cli.py
git commit -m "feat: config CLI 支持 exclude_chats 按 chat_id 增删与 special_focus 路径

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 配置补名与旧键迁移（autofill_chat_names）

**Files:**
- Modify: `lark_listener/config_editor.py`、`lark_listener/main.py`
- Test: `tests/test_config_editor.py`、`tests/test_main.py`

- [ ] **Step 10.1: 写失败测试**

`tests/test_config_editor.py`：

```python
def _write_yaml(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_autofill_fills_missing_names(tmp_path):
    path = _write_yaml(tmp_path, (
        "exclude_chats:\n"
        "  - chat_id: oc_a\n"
        "    name: ''\n"
        "  - chat_id: oc_b\n"
        "    name: 已有名\n"
        "special_focus:\n"
        "  enabled: true\n"
        "  chats:\n"
        "    - chat_id: oc_vip\n"
        "      name: ''\n"
        "      keywords: [扩容]\n"
    ))
    changed = autofill_chat_names(path, lambda cid: {"oc_a": "A群", "oc_vip": "VIP群"}.get(cid, ""))
    assert changed is True
    data = load_roundtrip(path)
    assert data["exclude_chats"][0]["name"] == "A群"
    assert data["exclude_chats"][1]["name"] == "已有名"     # 手填不覆盖
    assert data["special_focus"]["chats"][0]["name"] == "VIP群"


def test_autofill_migrates_legacy_exclude_key(tmp_path):
    path = _write_yaml(tmp_path, (
        "# 注释要保留\n"
        "include_at_all: false\n"
        "exclude_chat_ids:\n"
        "  - oc_old\n"
    ))
    changed = autofill_chat_names(path, lambda cid: "")
    assert changed is True
    data = load_roundtrip(path)
    assert "exclude_chat_ids" not in data
    assert "include_at_all" not in data
    assert data["exclude_chats"] == [{"chat_id": "oc_old", "name": ""}]
    # 注释保留（ruamel round-trip）
    assert "注释要保留" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_autofill_noop_returns_false(tmp_path):
    path = _write_yaml(tmp_path, "exclude_chats:\n  - chat_id: oc_a\n    name: A群\n")
    assert autofill_chat_names(path, lambda cid: "新名") is False


def test_autofill_resolver_failure_leaves_empty(tmp_path):
    path = _write_yaml(tmp_path, "exclude_chats:\n  - chat_id: oc_a\n    name: ''\n")
    assert autofill_chat_names(path, lambda cid: "") is False
```

`tests/test_main.py`（poll_once 调补名是 best-effort 且 mock registry 下跳过）：

```python
def test_autofill_skipped_for_non_registry(monkeypatch, tmp_path):
    """fetcher.registry 非 ChatRegistry 实例（mock/None）时跳过补名，
    单测/降级路径不碰配置文件。"""
    p = tmp_path / "config.yaml"
    p.write_text("exclude_chats: []\n", encoding="utf-8")
    class _F:
        registry = object()
    main_mod._autofill_config_names(str(p), _F())   # 不抛、不改文件
    assert p.read_text(encoding="utf-8") == "exclude_chats: []\n"
```

- [ ] **Step 10.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_config_editor.py tests/test_main.py -q
```

- [ ] **Step 10.3: 实现**

`config_editor.py` 末尾新增：

```python
def autofill_chat_names(path, name_of) -> bool:
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
```

`main.py`：poll_once 在 `_fetch_window` 调用之后插入一行 `_autofill_config_names(config_path, fetcher)`，并新增：

```python
def _autofill_config_names(config_path: Optional[str], fetcher) -> None:
    """best-effort：补群名 + 迁移旧键。mock/降级（registry 非 ChatRegistry）
    时跳过——单测的 poll_once 不应碰配置文件。失败仅告警，绝不阻断轮询。"""
    registry = getattr(fetcher, "registry", None)
    if not isinstance(registry, ChatRegistry):
        return
    from lark_listener import config_editor
    path = config_path or str(listener_home() / "config.yaml")
    try:
        config_editor.autofill_chat_names(path, registry.name_of)
    except Exception:  # noqa: BLE001
        logger.warning("配置补名/迁移失败（忽略，下轮再试）", exc_info=True)
```

（`listener_home` 已在 main.py import 里；若没有则补 `from lark_listener.common import listener_home`。）

- [ ] **Step 10.4: 跑测试**

```bash
python3 -m pytest -q
```

- [ ] **Step 10.5: Commit**

```bash
git add lark_listener/config_editor.py lark_listener/main.py tests/test_config_editor.py tests/test_main.py
git commit -m "feat: 配置自动补群名并迁移旧键（exclude_chat_ids/include_at_all）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: doctor — special_focus 检查与 --deep 探测

**Files:**
- Modify: `lark_listener/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 11.1: 写失败测试**

```python
from lark_listener.doctor import check_special_focus


def _cfg_sf(enabled=True, chats=None):
    return {"lark_cli_appid": "cli_x",
            "special_focus": {"enabled": enabled, "max_messages": 20,
                              "chats": chats or []}}


def test_special_focus_disabled_ok():
    c = check_special_focus(_cfg_sf(enabled=False))
    assert c.status == "ok" and "未启用" in c.detail


def test_special_focus_shallow_ok():
    c = check_special_focus(_cfg_sf(chats=[{"chat_id": "oc_a", "name": "", "keywords": []}]))
    assert c.status == "ok" and "--deep" in c.detail


def _fake_run_unmuted(chat_ids):
    import json as _json
    def run(args, **kwargs):
        class R:
            returncode = 0
            stdout = _json.dumps({"ok": True, "data": {
                "chats": [{"chat_id": cid, "name": cid} for cid in chat_ids],
                "has_more": False, "page_token": ""}})
        return R()
    return run


def test_special_focus_deep_warns_muted_binding():
    c = check_special_focus(
        _cfg_sf(chats=[{"chat_id": "oc_muted", "name": "勿扰群", "keywords": ["x"]}]),
        deep=True, run=_fake_run_unmuted(["oc_other"]))
    assert c.status == "warn" and "勿扰群" in c.detail


def test_special_focus_deep_probe_failure():
    def bad_run(args, **kwargs):
        raise OSError("no cli")
    c = check_special_focus(_cfg_sf(), deep=True, run=bad_run)
    assert c.status == "fail"


def test_special_focus_deep_all_unmuted_ok():
    c = check_special_focus(
        _cfg_sf(chats=[{"chat_id": "oc_vip", "name": "VIP", "keywords": []}]),
        deep=True, run=_fake_run_unmuted(["oc_vip"]))
    assert c.status == "ok"
```

- [ ] **Step 11.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_doctor.py -q
```

- [ ] **Step 11.3: 实现 doctor.py**

新增（放 `check_ai_backend` 之后）：

```python
def _probe_unmuted_chats(appid: str, run=None):
    """chat-list --exclude-muted 单页真探：返回未免打扰群 id 集合，失败 None。
    doctor 只需判定可用性与绑定群状态，单页 100 足够；分页全量是运行时
    ChatRegistry 的职责。"""
    import json as _json
    import subprocess
    from lark_listener.binaries import resolve_executable
    run = run or subprocess.run
    cmd = [resolve_executable("lark-cli"), "im", "+chat-list", "--exclude-muted",
           "--page-size", "100", "--format", "json"]
    if appid:
        cmd += ["--profile", appid]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=30)
        data = _json.loads(r.stdout)
        if r.returncode != 0 or not data.get("ok"):
            return None
        return {c.get("chat_id") for c in (data.get("data") or {}).get("chats") or []
                if isinstance(c, dict) and c.get("chat_id")}
    except Exception:  # noqa: BLE001
        return None


def check_special_focus(config: dict, deep: bool = False, run=None) -> Check:
    """special_focus 配置体检。浅检只看形状（load_config 已钳制，重点是
    提示语义）；--deep 真探未免打扰列表并核对绑定群状态——绑定了关注词的
    群若已免打扰，关注词静默失效，这是用户最易踩的暗坑。"""
    sf = config.get("special_focus") or {}
    if not sf.get("enabled"):
        return Check("special_focus", "ok", "特别关注未启用")
    bound = sf.get("chats") or []
    if not deep:
        return Check("special_focus", "ok",
                     f"特别关注已启用（绑定 {len(bound)} 个群；--deep 可验证免打扰状态）")
    unmuted = _probe_unmuted_chats(config.get("lark_cli_appid", ""), run=run)
    if unmuted is None:
        return Check("special_focus", "fail",
                     "chat-list --exclude-muted 探测失败（特别关注将降级为全勿扰）",
                     fix="lark-cli auth login --profile "
                         f"{config.get('lark_cli_appid', '')} 重新授权后重试")
    muted_bound = [c for c in bound
                   if isinstance(c, dict) and c.get("chat_id") not in unmuted]
    if muted_bound:
        names = "、".join((c.get("name") or c.get("chat_id", "")) for c in muted_bound)
        return Check("special_focus", "warn",
                     f"绑定的群当前处于免打扰，关注关键词不会生效：{names}",
                     fix="在飞书取消这些群的消息免打扰，或从 special_focus.chats 移除")
    return Check("special_focus", "ok",
                 f"特别关注已启用，{len(unmuted)} 个未免打扰群")
```

`run_doctor` 中在 AI 后端检查之后追加（config 加载失败时跳过，照 run_doctor 既有的 config 获取方式接入；若 run_doctor 内已有 config 变量则直接复用）：

```python
    try:
        _cfg = config_mod.load_config()
        checks.append(check_special_focus(_cfg, deep=deep))
    except Exception:  # noqa: BLE001 — config 坏时 check_config 已报，无需重复
        pass
```

- [ ] **Step 11.4: 跑测试**

```bash
python3 -m pytest tests/test_doctor.py -q && python3 -m pytest -q
```

- [ ] **Step 11.5: Commit**

```bash
git add lark_listener/doctor.py tests/test_doctor.py
git commit -m "feat: doctor 体检 special_focus（--deep 真探未免打扰与绑定群状态）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: setup_wizard — 新配置格式落盘

**Files:**
- Modify: `lark_listener/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 12.1: 写失败测试**

```python
def test_build_config_dict_new_format():
    cfg = build_config_dict(
        poll_interval=300, appid="cli_x", keywords=["SDK"],
        ai_provider="claude", ai_model="m", ai_key="k", ai_base_url="",
        user_id="ou_me", bot_chat_id="oc_bot")
    assert "include_at_all" not in cfg
    assert "exclude_chat_ids" not in cfg
    assert cfg["exclude_chats"] == [{"chat_id": "oc_bot", "name": "LarkListener Bot"}]
    assert cfg["special_focus"] == {"enabled": False, "max_messages": 20, "chats": []}
```

（既有 build_config_dict 用例中对 `include_at_all`/`exclude_chat_ids` 的断言同步更新。）

- [ ] **Step 12.2: 跑测试确认失败**

```bash
python3 -m pytest tests/test_setup_wizard.py -q
```

- [ ] **Step 12.3: 实现**

`build_config_dict` 返回值改为：

```python
    return {
        "poll_interval": poll_interval,
        "lark_cli_appid": appid,
        "context_messages": 20,
        "keywords": list(keywords),
        "special_focus": {"enabled": False, "max_messages": 20, "chats": []},
        "ai": {
            "provider": ai_provider,
            "model": ai_model,
            "api_key": ai_key,
            "base_url": ai_base_url,
        },
        # bot 自身会话默认排除（防汇总自反馈）；name 直接写死——它就是本服务的 bot
        "exclude_chats": [{"chat_id": bot_chat_id, "name": "LarkListener Bot"}],
        "notify": {"user_id": user_id, "bot_chat_id": bot_chat_id},
    }
```

- [ ] **Step 12.4: 跑测试**

```bash
python3 -m pytest tests/test_setup_wizard.py -q && python3 -m pytest -q
```

- [ ] **Step 12.5: Commit**

```bash
git add lark_listener/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: setup 向导落盘新配置格式（exclude_chats + special_focus）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: 文档同步与最终验证

**Files:**
- Modify: `config.example.yaml`、`README.md`、`CLAUDE.md`、`lark_listener/skills/lark-listener/SKILL.md`

- [ ] **Step 13.1: config.example.yaml**

`include_at_all: true` 行删除；`exclude_chat_ids` 段替换为：

```yaml
# 排除的会话（不参与任何汇总）。name 留空会在轮询时自动补全
exclude_chats: []
# 例：
#   exclude_chats:
#     - chat_id: oc_xxxxxxxx
#       name: 某某群

# 特别关注：未免打扰的群在开启后全量汇总（含未 @你、未命中关键词的消息）。
# 顶层 keywords 即全局关注词；chats 可按群叠加专属关注词（仅 AI 分析用）。
special_focus:
  enabled: false
  max_messages: 20        # 每个特别关注群单轮汇总的消息条数上限
  chats: []
  # 例：
  #   chats:
  #     - chat_id: oc_xxxxxxxx
  #       name: 某某群        # 留空自动补全
  #       keywords: [扩容]
```

- [ ] **Step 13.2: README.md / SKILL.md / CLAUDE.md**

- README：`exclude_chat_ids` 引用改 `exclude_chats`；新增「会话分类」小节说明三类会话与 @所有人 行为差异（勿扰群仅命中关键词才收 @all；特别关注=未免打扰的群+开关开启=全量汇总）；`config set` 示例更新。
- SKILL.md：`exclude_chat_ids` → `exclude_chats`；封套 `category` 枚举补 `special` 与新顺序说明。
- CLAUDE.md 模块表：新增 `chats.py` 行（「未免打扰探测与会话分类唯一事实源：classify_chat/ChatRegistry（每轮 refresh、失败降级全勿扰）」）；`fetcher.py`/`notifier.py`/`config.py` 行的描述补一句新职责（@all 按分类分流+特别关注全量抓取；特别关注区与统一顺序；special_focus/exclude_chats 钳制）。

- [ ] **Step 13.3: 最终验证**

```bash
python3 -m pytest -q          # 全绿
./dev-test.sh                  # unit + smoke（无副作用）
```
预期：全部通过。若 smoke 因 `config.example.yaml` 字段校验失败，按报错修复后重跑。

- [ ] **Step 13.4: Commit**

```bash
git add config.example.yaml README.md CLAUDE.md lark_listener/skills/lark-listener/SKILL.md
git commit -m "docs: 同步会话分类与 special_focus 配置说明

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 自查清单（计划完成后执行方核对）

- [ ] spec §1 行为矩阵五行均有对应测试（勿扰 @all 跳过/关键词捞回、普通群 @all 收、特别关注全量、p2p 不变）
- [ ] 同一群跨类别拆行（test_same_chat_splits_into_at_me_and_special_rows）
- [ ] 归类优先级=卡片顺序（test_category_order_matches_priority）
- [ ] 降级三态（失败沿用/首刷全勿扰/special sweep 跳过）有测试
- [ ] 旧配置兼容（exclude_chat_ids 读取 + 回写迁移 + include_at_all 忽略与清理）有测试
- [ ] 截断必留日志（fetcher special sweep 的 dropped 分支）
- [ ] `python3 -m pytest -q` 全绿、`./dev-test.sh` 通过
- [ ] 不真发飞书、不碰生产 `~/.lark_listener`
