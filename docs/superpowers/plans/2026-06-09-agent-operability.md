# Agent Operability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让安装后的 LarkListener 能被 AI Agent 可靠地启停、配置、查错、诊断，并通过 Claude Code skill 实现「零知识发现」。

**Architecture:** 地板层 = 自描述 CLI（`status --json`+退出码、`doctor`、`config get/set`）作为契约唯一事实源；推送层 = 可插拔 adapter 注册表，首期只实现 `ClaudeCodeAdapter`（把包内 skill 拷进 `~/.claude/skills/`），随服务安装/卸载自动接入。

**Tech Stack:** Python ≥3.9 标准库 + argparse + ruamel.yaml（保留注释）+ importlib.resources（读包内 skill）+ pytest。

**Spec:** `docs/superpowers/specs/2026-06-09-agent-operability-design.md`

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `lark_listener/service.py`（改） | `collect_status()` 纯函数 + `cmd_status(as_json)->int` 渲染/退出码；`cmd_start/stop/restart/config/uninstall` 返回退出码；`cmd_uninstall` 调 agent-skills 清理 |
| `lark_listener/doctor.py`（新） | 各检查纯函数（`Check` dataclass）+ `run_doctor(deep)` + 渲染 + `cmd_doctor(as_json,deep)->int` |
| `lark_listener/config_cli.py`（新） | `config_get`/`config_set` 薄实现（点号路径、列表增/减/整体替换、`--force`、写后校验回滚、api_key 脱敏） |
| `lark_listener/agent_adapters.py`（新） | adapter 协议 + 注册表 + `ClaudeCodeAdapter` + `install_agent_skills`/`uninstall_agent_skills` |
| `lark_listener/skills/lark-listener/SKILL.md`（新） | 包内资源，安装后运维契约，defer 到 `--help`/`doctor` |
| `lark_listener/main.py`（改） | argparse 拆开统一循环、各命令单独定义参数、`sys.exit` 传退出码、help 标注 ✅/🚫 |
| `pyproject.toml`（改） | `package-data` 打包 SKILL.md |
| `install.sh`（改） | best-effort 调 `agent-skills install` |
| `AGENTS.md`（改） | 提新增 floor 命令 + 会装 skill |
| 测试 | `tests/test_doctor.py`、`tests/test_config_cli.py`、`tests/test_agent_adapters.py`（新）；扩展 `tests/test_service.py`、`tests/test_main.py` |

退出码约定：status `0/3/4/1`（运行/停/未装/错）；doctor `0/1`（全过/有 fail）；其余命令 `0/1`。

---

## Task 1: `collect_status()` 纯函数

**Files:**
- Modify: `lark_listener/service.py`（在 `cmd_status` 上方新增）
- Test: `tests/test_service.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_service.py`：

```python
def test_collect_status_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "PLIST_PATH", tmp_path / "nope.plist")
    monkeypatch.setattr(service, "LISTENER_HOME", tmp_path)
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "_pids", lambda pat: [])
    monkeypatch.setattr(service, "_recorded_shim", lambda: None)
    st = service.collect_status()
    assert st["state"] == "not_installed"
    assert st["main_pids"] == []
    assert st["files"]["config"]["exists"] is False
    assert st["last_poll_time"] is None


def test_collect_status_running_reads_last_poll(tmp_path, monkeypatch):
    plist = tmp_path / "svc.plist"; plist.write_text("x")
    (tmp_path / "state.json").write_text('{"last_poll_time": "2026-06-09T10:00:00+08:00"}')
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "LISTENER_HOME", tmp_path)
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "_is_running", lambda: True)
    monkeypatch.setattr(service, "_pids", lambda pat: ["123"])
    monkeypatch.setattr(service, "_recorded_shim", lambda: None)
    st = service.collect_status()
    assert st["state"] == "running"
    assert st["main_pids"] == ["123"]
    assert st["last_poll_time"] == "2026-06-09T10:00:00+08:00"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_service.py::test_collect_status_not_installed -v`
Expected: FAIL — `AttributeError: module 'lark_listener.service' has no attribute 'collect_status'`

- [ ] **Step 3: 实现 `collect_status()`**

在 `lark_listener/service.py` 顶部确保 `import json`（若无则加），并在 `def cmd_status` 之前插入：

```python
def collect_status() -> dict:
    """采集服务状态为机读 dict（cmd_status 的渲染数据源）。"""
    if not PLIST_PATH.exists():
        state = "not_installed"
    elif _is_running():
        state = "running"
    else:
        state = "stopped"

    main_pids = _pids(f"{VENV_DIR}/bin/lark-listener run")
    event_pids = _pids("lark-cli event.*--as bot")

    shim = _recorded_shim() or str(SHIM_LINK)
    paths = {
        "config": LISTENER_HOME / "config.yaml",
        "state": LISTENER_HOME / "state.json",
        "logs": LISTENER_HOME / "logs",
        "venv": VENV_DIR,
        "launchd": PLIST_PATH,
        "shim": Path(shim),
    }
    files = {
        name: {"path": str(p), "exists": bool(p.exists() or p.is_symlink())}
        for name, p in paths.items()
    }

    last_poll = None
    state_file = LISTENER_HOME / "state.json"
    try:
        if state_file.exists():
            last_poll = json.loads(state_file.read_text(encoding="utf-8")).get("last_poll_time")
    except Exception:
        last_poll = None

    return {
        "state": state,
        "main_pids": main_pids,
        "event_pids": event_pids,
        "files": files,
        "last_poll_time": last_poll,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_service.py -k collect_status -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/service.py tests/test_service.py
git commit -m "feat(status): add collect_status() machine-readable state"
```

---

## Task 2: `cmd_status(as_json)` 渲染 + 退出码

**Files:**
- Modify: `lark_listener/service.py:213-240`（重写 `cmd_status`）
- Test: `tests/test_service.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_cmd_status_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(service, "collect_status",
                        lambda: {"state": "running", "main_pids": ["1"], "event_pids": [],
                                 "files": {}, "last_poll_time": None})
    assert service.cmd_status() == 0
    monkeypatch.setattr(service, "collect_status",
                        lambda: {"state": "stopped", "main_pids": [], "event_pids": [],
                                 "files": {}, "last_poll_time": None})
    assert service.cmd_status() == 3
    monkeypatch.setattr(service, "collect_status",
                        lambda: {"state": "not_installed", "main_pids": [], "event_pids": [],
                                 "files": {}, "last_poll_time": None})
    assert service.cmd_status() == 4


def test_cmd_status_json_output(monkeypatch, capsys):
    monkeypatch.setattr(service, "collect_status",
                        lambda: {"state": "running", "main_pids": ["7"], "event_pids": [],
                                 "files": {}, "last_poll_time": "2026-06-09T10:00:00+08:00"})
    code = service.cmd_status(as_json=True)
    out = capsys.readouterr().out
    import json as _j
    data = _j.loads(out)
    assert data["state"] == "running" and data["main_pids"] == ["7"]
    assert code == 0


def test_cmd_status_collect_failure_returns_1(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(service, "collect_status", boom)
    assert service.cmd_status() == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_service.py::test_cmd_status_exit_codes -v`
Expected: FAIL — 旧 `cmd_status()` 返回 `None`，`assert ... == 0` 失败

- [ ] **Step 3: 重写 `cmd_status`**

把 `lark_listener/service.py` 中现有 `cmd_status`（213-240 行）整体替换为：

```python
_STATUS_EXIT = {"running": 0, "stopped": 3, "not_installed": 4}


def _render_status_text(st: dict) -> None:
    label = {"running": "● 服务运行中", "stopped": "○ 服务已安装，未运行",
             "not_installed": "◇ 未安装"}
    print(label.get(st["state"], st["state"]))
    print("\n进程：")
    print(f"  主进程 (lark-listener run)  : {' '.join(st['main_pids']) or '无'}")
    print(f"  监听子进程 (lark-cli event) : {' '.join(st['event_pids']) or '无'}")
    print("\n文件位置：")
    names = {"config": "配置", "state": "状态", "logs": "日志",
             "venv": "venv", "launchd": "launchd", "shim": "短命令"}
    for key, zh in names.items():
        info = st["files"].get(key)
        if not info:
            continue
        mark = "✓" if info["exists"] else "—"
        print(f"  {zh:<7}{mark} {info['path']}")
    if st["last_poll_time"]:
        print(f"\n上次轮询：{st['last_poll_time']}")


def cmd_status(as_json: bool = False) -> int:
    try:
        st = collect_status()
    except Exception as e:  # noqa: BLE001 — 诊断命令本身不可崩
        print(f"❌ 状态获取失败：{e}")
        return 1
    if as_json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        _render_status_text(st)
    return _STATUS_EXIT.get(st["state"], 1)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_service.py -k "cmd_status or collect_status" -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/service.py tests/test_service.py
git commit -m "feat(status): JSON output + meaningful exit codes"
```

---

## Task 3: `cmd_start/stop/restart/config/uninstall` 返回退出码

**Files:**
- Modify: `lark_listener/service.py`（`cmd_start` 168-183、`cmd_stop` 186-188、`cmd_restart` 191-193、`cmd_config` 243-249、`cmd_uninstall` 252-276）
- Test: `tests/test_service.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_cmd_start_returns_1_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "PLIST_PATH", tmp_path / "nope.plist")
    assert service.cmd_start() == 1


def test_cmd_start_returns_0_when_running(monkeypatch, tmp_path):
    plist = tmp_path / "svc.plist"; plist.write_text("x")
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(service.time, "sleep", lambda *_: None)
    monkeypatch.setattr(service, "_is_running", lambda: True)
    assert service.cmd_start() == 0


def test_cmd_stop_and_restart_return_int(monkeypatch):
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr(service, "cmd_start", lambda: 0)
    assert service.cmd_stop() == 0
    assert service.cmd_restart() == 0


def test_cmd_config_missing_returns_1(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "LISTENER_HOME", tmp_path)
    assert service.cmd_config() == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_service.py::test_cmd_start_returns_1_when_not_installed -v`
Expected: FAIL — 返回 `None` 而非 `1`

- [ ] **Step 3: 改各命令返回退出码**

`cmd_start`（168-183）替换为：

```python
def cmd_start() -> int:
    if not PLIST_PATH.exists():
        print("❌ 未安装，请先运行: lark-listener setup")
        return 1
    if _is_running():
        print("正在重启...")
    stop_service()
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True)
    time.sleep(3)
    if _is_running():
        print("✓ 服务已启动")
        return 0
    print(f"❌ 启动失败，请查看日志:\n  cat {LISTENER_HOME}/logs/stderr.log")
    return 1
```

`cmd_stop`（186-188）：

```python
def cmd_stop() -> int:
    stop_service()
    print("✓ 服务已停止")
    return 0
```

`cmd_restart`（191-193）：

```python
def cmd_restart() -> int:
    stop_service()
    return cmd_start()
```

`cmd_config`（243-249）：

```python
def cmd_config() -> int:
    cfg = LISTENER_HOME / "config.yaml"
    if not cfg.exists():
        print("❌ 配置文件不存在，请先运行: lark-listener setup")
        return 1
    subprocess.run(["open", "-t", str(cfg)])
    print("✓ 已打开配置文件（修改后下次轮询自动生效）")
    return 0
```

`cmd_uninstall`（252-276）：把末尾 `print("✓ 已卸载完成。")` 改为：

```python
    print("✓ 已卸载完成。")
    return 0
```

并把开头 `confirm != "y"` 分支的 `return` 改为 `return 0`（取消也是正常退出）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_service.py -v`
Expected: PASS（含原有 test_cmd_start_unloads_before_load 仍过）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/service.py tests/test_service.py
git commit -m "feat(service): commands return exit codes"
```

---

## Task 4: `doctor.py` 检查纯函数

**Files:**
- Create: `lark_listener/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_doctor.py`：

```python
from datetime import datetime, timezone, timedelta

from lark_listener import doctor

TZ = timezone(timedelta(hours=8))


def test_check_config_ok(monkeypatch):
    monkeypatch.setattr(doctor.config_mod, "load_config", lambda *a, **k: {"ok": 1})
    c = doctor.check_config()
    assert c.status == "ok"


def test_check_config_fail(monkeypatch):
    def boom(*a, **k):
        raise ValueError("缺少 notify.user_id")
    monkeypatch.setattr(doctor.config_mod, "load_config", boom)
    c = doctor.check_config()
    assert c.status == "fail"
    assert "notify.user_id" in c.detail


def test_check_service_states():
    assert doctor.check_service({"state": "running"}).status == "ok"
    assert doctor.check_service({"state": "stopped"}).status == "fail"
    assert doctor.check_service({"state": "not_installed"}).status == "fail"


def test_check_last_poll_stale():
    now = datetime(2026, 6, 9, 12, 0, tzinfo=TZ)
    fresh = {"last_poll_time": "2026-06-09T11:59:00+08:00"}
    stale = {"last_poll_time": "2026-06-09T10:00:00+08:00"}
    assert doctor.check_last_poll(fresh, 300, now=now).status == "ok"
    assert doctor.check_last_poll(stale, 300, now=now).status == "warn"
    assert doctor.check_last_poll({"last_poll_time": None}, 300, now=now).status == "warn"


def test_check_ai_backend_shallow_missing_model(monkeypatch):
    monkeypatch.setattr(doctor, "_sdk_installed", lambda name: True)
    cfg = {"ai": {"provider": "claude", "model": "", "api_key": "k"}}
    assert doctor.check_ai_backend(cfg).status == "fail"


def test_check_ai_backend_shallow_sdk_missing(monkeypatch):
    monkeypatch.setattr(doctor, "_sdk_installed", lambda name: False)
    cfg = {"ai": {"provider": "claude", "model": "m", "api_key": "k", "base_url": ""}}
    c = doctor.check_ai_backend(cfg)
    assert c.status == "fail" and "anthropic" in c.detail


def test_check_ai_backend_shallow_ok(monkeypatch):
    monkeypatch.setattr(doctor, "_sdk_installed", lambda name: True)
    cfg = {"ai": {"provider": "openai", "model": "gpt", "api_key": "k", "base_url": ""}}
    assert doctor.check_ai_backend(cfg).status == "ok"


def test_check_recent_errors(tmp_path):
    log = tmp_path / "stderr.log"
    log.write_text("ok line\nTraceback (most recent call last):\n  boom\n")
    assert doctor.check_recent_errors(log).status == "warn"
    log.write_text("all good\n")
    assert doctor.check_recent_errors(log).status == "ok"
    assert doctor.check_recent_errors(tmp_path / "none.log").status == "ok"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_doctor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.doctor'`

- [ ] **Step 3: 实现检查函数**

新建 `lark_listener/doctor.py`：

```python
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from lark_listener import config as config_mod
from lark_listener import service

TZ = timezone(timedelta(hours=8))

# claude→anthropic, openai→openai, ollama→无需 SDK
_SDK_FOR = {"claude": "anthropic", "openai": "openai"}


@dataclass
class Check:
    check: str
    status: str  # ok | warn | fail
    detail: str = ""
    fix: str = ""


def _sdk_installed(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def check_config() -> Check:
    try:
        config_mod.load_config()
        return Check("config", "ok", "配置存在且合法")
    except Exception as e:  # noqa: BLE001
        return Check("config", "fail", str(e),
                     fix="lark-listener config get 查看；按提示补字段")


def check_service(status: dict) -> Check:
    state = status.get("state")
    if state == "running":
        return Check("service", "ok", "服务运行中")
    if state == "stopped":
        return Check("service", "fail", "服务已安装但未运行", fix="lark-listener start")
    return Check("service", "fail", "服务未安装", fix="lark-listener setup")


def check_lark_cli(run=None) -> Check:
    import subprocess
    from lark_listener.binaries import resolve_executable
    run = run or subprocess.run
    exe = resolve_executable("lark-cli")
    if exe == "lark-cli" and not Path(exe).is_absolute():
        return Check("lark_cli", "fail", "未找到 lark-cli",
                     fix="npm install -g @larksuite/cli")
    try:
        r = run([exe, "profile", "list"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return Check("lark_cli", "warn", "lark-cli 可能未登录或授权过期",
                         fix="lark-cli auth login --scope search:message")
        return Check("lark_cli", "ok", "lark-cli 可用")
    except Exception as e:  # noqa: BLE001
        return Check("lark_cli", "warn", f"lark-cli 调用失败：{e}",
                     fix="lark-cli auth login --scope search:message")


def check_last_poll(status: dict, poll_interval: int, now: Optional[datetime] = None) -> Check:
    now = now or datetime.now(TZ)
    raw = status.get("last_poll_time")
    if not raw:
        return Check("last_poll", "warn", "从未成功轮询过", fix="lark-listener doctor --deep / 看日志")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
    except ValueError:
        return Check("last_poll", "warn", f"无法解析 last_poll_time：{raw}")
    age = (now - dt).total_seconds()
    if age > poll_interval * 3:
        return Check("last_poll", "warn",
                     f"上次轮询距今 {int(age)}s，超过间隔×3（{poll_interval*3}s）",
                     fix="lark-listener status / tail 日志")
    return Check("last_poll", "ok", f"上次轮询 {raw}")


def check_recent_errors(log_path: Path) -> Check:
    try:
        if not log_path.exists():
            return Check("recent_errors", "ok", "无日志（尚未运行或已清理）")
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        for i, line in enumerate(tail):
            if "Traceback (most recent call last)" in line:
                snippet = " / ".join(tail[i:i + 3])
                return Check("recent_errors", "warn", f"近期日志有异常：{snippet}",
                             fix="tail -n 100 ~/.lark_listener/logs/stderr.log")
        return Check("recent_errors", "ok", "近期日志无 traceback")
    except Exception as e:  # noqa: BLE001
        return Check("recent_errors", "ok", f"日志读取跳过：{e}")


def check_ai_backend(config: dict, deep: bool = False, run=None) -> Check:
    ai = config.get("ai") or {}
    provider = ai.get("provider")
    model = ai.get("model")
    api_key = ai.get("api_key")
    base_url = ai.get("base_url") or ""

    if provider not in ("claude", "openai", "ollama"):
        return Check("ai_backend", "fail", f"provider 非法：{provider!r}",
                     fix="config set ai.provider claude|openai|ollama --force")
    if not model:
        return Check("ai_backend", "fail", "ai.model 为空",
                     fix="config set ai.model <模型名> --force")
    if provider in ("claude", "openai") and not api_key:
        return Check("ai_backend", "fail", f"{provider} 缺 api_key",
                     fix="config set ai.api_key <key> --force")
    sdk = _SDK_FOR.get(provider)
    if sdk and not _sdk_installed(sdk):
        return Check("ai_backend", "fail", f"venv 内缺 {sdk} SDK",
                     fix=f"~/.lark_listener/venv/bin/pip install {sdk}")
    if provider == "ollama" and not base_url:
        return Check("ai_backend", "warn", "ollama 未设 base_url（将用默认本地端点）")

    if not deep:
        return Check("ai_backend", "ok", f"{provider}/{model} 配置完整（未做真实请求，--deep 可验证）")

    ok, detail = _deep_probe(provider, model, api_key, base_url, run)
    if ok:
        return Check("ai_backend", "ok", f"{provider}/{model} 真实请求成功")
    return Check("ai_backend", "fail", f"真实请求失败：{detail}",
                 fix="核对 api_key / base_url / 模型名 / ollama 是否在跑")
```

注：`_deep_probe` 在 Task 5 实现（深检入口）；本任务测试只覆盖浅检，`deep=False` 不会触达它。为避免 `NameError`，本任务先加一个占位实现放在文件末尾：

```python
def _deep_probe(provider, model, api_key, base_url, run=None):
    """真实最小请求探测，返回 (ok, detail)。Task 5 完善。"""
    return False, "未实现"
```

> **有意简化（非遗漏）**：spec 浅检提到「该有 base_url 的有（openai 兼容端点）」。本实现只对 `ollama` 缺 base_url 给 warn，不对 openai 强制要求 base_url——因为 openai 官方端点本就不需要 base_url，而「是否第三方兼容端点」无法可靠从 model 名推断。真正的端点错误由 `--deep` 真实请求兜底。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_doctor.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): individual diagnostic check functions"
```

---

## Task 5: `doctor` 深检 + 汇总 + 渲染 + `cmd_doctor`

**Files:**
- Modify: `lark_listener/doctor.py`（完善 `_deep_probe`、新增 `run_doctor`/渲染/`cmd_doctor`）
- Test: `tests/test_doctor.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_run_doctor_aggregates_and_exit(monkeypatch):
    monkeypatch.setattr(doctor, "check_config", lambda: doctor.Check("config", "ok"))
    monkeypatch.setattr(doctor, "check_service", lambda s: doctor.Check("service", "ok"))
    monkeypatch.setattr(doctor, "check_lark_cli", lambda **k: doctor.Check("lark_cli", "ok"))
    monkeypatch.setattr(doctor, "check_last_poll", lambda *a, **k: doctor.Check("last_poll", "warn"))
    monkeypatch.setattr(doctor, "check_recent_errors", lambda p: doctor.Check("recent_errors", "ok"))
    monkeypatch.setattr(doctor, "check_ai_backend", lambda *a, **k: doctor.Check("ai_backend", "ok"))
    monkeypatch.setattr(doctor.service, "collect_status", lambda: {"state": "running", "last_poll_time": None})
    monkeypatch.setattr(doctor.config_mod, "load_config", lambda *a, **k: {"poll_interval": 300, "ai": {}})
    checks, code = doctor.run_doctor()
    assert code == 0  # 只有 warn，无 fail
    assert any(c.check == "ai_backend" for c in checks)


def test_run_doctor_fail_exit_1(monkeypatch):
    monkeypatch.setattr(doctor, "check_config", lambda: doctor.Check("config", "fail", "x"))
    monkeypatch.setattr(doctor, "check_service", lambda s: doctor.Check("service", "ok"))
    monkeypatch.setattr(doctor, "check_lark_cli", lambda **k: doctor.Check("lark_cli", "ok"))
    monkeypatch.setattr(doctor, "check_last_poll", lambda *a, **k: doctor.Check("last_poll", "ok"))
    monkeypatch.setattr(doctor, "check_recent_errors", lambda p: doctor.Check("recent_errors", "ok"))
    monkeypatch.setattr(doctor, "check_ai_backend", lambda *a, **k: doctor.Check("ai_backend", "ok"))
    monkeypatch.setattr(doctor.service, "collect_status", lambda: {"state": "running", "last_poll_time": None})
    monkeypatch.setattr(doctor.config_mod, "load_config", lambda *a, **k: {"poll_interval": 300, "ai": {}})
    _, code = doctor.run_doctor()
    assert code == 1


def test_cmd_doctor_json(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_doctor",
                        lambda deep=False: ([doctor.Check("config", "ok", "fine")], 0))
    code = doctor.cmd_doctor(as_json=True)
    import json as _j
    data = _j.loads(capsys.readouterr().out)
    assert data[0]["check"] == "config" and data[0]["status"] == "ok"
    assert code == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_doctor.py::test_run_doctor_aggregates_and_exit -v`
Expected: FAIL — `AttributeError: ... has no attribute 'run_doctor'`

- [ ] **Step 3: 实现汇总/渲染/入口 + 完善深检**

替换 Task 4 末尾的占位 `_deep_probe`，并在 `doctor.py` 追加：

```python
def _deep_probe(provider, model, api_key, base_url, run=None):
    """真实最小请求探测，返回 (ok, detail)。best-effort，异常即视为失败。"""
    try:
        if provider == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            client.messages.create(model=model, max_tokens=1,
                                    messages=[{"role": "user", "content": "ping"}])
            return True, ""
        if provider == "openai":
            import openai
            client = openai.OpenAI(api_key=api_key, base_url=base_url or None)
            client.models.list()
            return True, ""
        if provider == "ollama":
            import urllib.request
            url = (base_url or "http://localhost:11434").rstrip("/") + "/api/tags"
            with urllib.request.urlopen(url, timeout=10) as resp:
                resp.read()
            return True, ""
        return False, f"未知 provider {provider}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def run_doctor(deep: bool = False):
    """跑全部检查，返回 (checks, exit_code)。exit_code: 有 fail=1 否则 0。"""
    status = service.collect_status()
    try:
        config = config_mod.load_config()
    except Exception:
        config = {}
    poll_interval = config.get("poll_interval", 300) if isinstance(config, dict) else 300
    log_path = service.LISTENER_HOME / "logs" / "stderr.log"

    checks = [
        check_config(),
        check_service(status),
        check_lark_cli(),
        check_last_poll(status, poll_interval),
        check_recent_errors(log_path),
        check_ai_backend(config if isinstance(config, dict) else {}, deep=deep),
    ]
    code = 1 if any(c.status == "fail" for c in checks) else 0
    return checks, code


_ICON = {"ok": "✓", "warn": "⚠", "fail": "✗"}


def _render_doctor_text(checks) -> None:
    print("LarkListener 诊断：\n")
    for c in checks:
        print(f"  {_ICON.get(c.status, '?')} [{c.check}] {c.detail}")
        if c.fix and c.status != "ok":
            print(f"      → 修复：{c.fix}")
    fails = [c for c in checks if c.status == "fail"]
    print(f"\n{'有问题需处理' if fails else '总体正常'}（fail={len(fails)}）")


def cmd_doctor(as_json: bool = False, deep: bool = False) -> int:
    checks, code = run_doctor(deep=deep)
    if as_json:
        import json
        print(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2))
    else:
        _render_doctor_text(checks)
    return code
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_doctor.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): aggregation, deep probe, render, cmd_doctor"
```

---

## Task 6: `config_cli.config_get`

**Files:**
- Create: `lark_listener/config_cli.py`
- Test: `tests/test_config_cli.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_config_cli.py`：

```python
import json
import pytest

from lark_listener import config_cli

FULL = {
    "poll_interval": 300, "keywords": ["上线"],
    "ai": {"provider": "claude", "model": "m", "api_key": "secret", "base_url": ""},
    "notify": {"user_id": "ou_x", "bot_chat_id": "oc_y"},
    "lark_cli_appid": "cli_x",
}


def test_config_get_masks_api_key(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get(as_json=True, path="/tmp/x.yaml")
    data = json.loads(capsys.readouterr().out)
    assert data["ai"]["api_key"] == "***"
    assert data["ai"]["model"] == "m"
    assert code == 0


def test_config_get_dotted_key(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get("ai.provider", as_json=True, path="/tmp/x.yaml")
    assert json.loads(capsys.readouterr().out) == "claude"
    assert code == 0


def test_config_get_unknown_key(monkeypatch):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    assert config_cli.config_get("ai.nope", path="/tmp/x.yaml") == 1


def test_config_get_load_failure(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad")
    monkeypatch.setattr(config_cli.config_mod, "load_config", boom)
    assert config_cli.config_get(path="/tmp/x.yaml") == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_config_cli.py::test_config_get_masks_api_key -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.config_cli'`

- [ ] **Step 3: 实现 `config_get`**

新建 `lark_listener/config_cli.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_config_cli.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/config_cli.py tests/test_config_cli.py
git commit -m "feat(config-cli): config_get with dotted keys + api_key masking"
```

---

## Task 7: `config_cli.config_set`

**Files:**
- Modify: `lark_listener/config_cli.py`（追加 `config_set`）
- Test: `tests/test_config_cli.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def _write_cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "poll_interval: 300\n"
        "keywords:\n  - 上线\n"
        "ai:\n  provider: claude\n  model: m\n  api_key: secret\n  base_url: ''\n"
        "notify:\n  user_id: ou_x\n  bot_chat_id: oc_y\n"
        "lark_cli_appid: cli_x\n",
        encoding="utf-8",
    )
    return p


def test_config_set_scalar(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("poll_interval", "600", path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["poll_interval"] == 600


def test_config_set_list_replace_add_remove(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("keywords", "故障,告警", path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["keywords"] == ["故障", "告警"]
    assert config_cli.config_set("keywords", "上线", add=True, path=p) == 0
    assert "上线" in config_cli.config_mod.load_config(str(p))["keywords"]
    assert config_cli.config_set("keywords", "故障", remove=True, path=p) == 0
    assert "故障" not in config_cli.config_mod.load_config(str(p))["keywords"]


def test_config_set_protected_needs_force(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("ai.model", "m2", path=p) == 1          # 无 force 拒绝
    assert config_cli.config_set("ai.model", "m2", force=True, path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["ai"]["model"] == "m2"


def test_config_set_validation_rollback(tmp_path):
    p = _write_cfg(tmp_path)
    # 把必填的 ai.model 清空 → _validate 失败 → 应回滚到原值。
    # 注意：不能字节比对原文件（ruamel 回滚会重新 dump，格式不保证逐字节一致），
    # 改为校验「值已回到原值」。
    assert config_cli.config_set("ai.model", "", force=True, path=p) == 1
    assert config_cli.config_mod.load_config(str(p))["ai"]["model"] == "m"


def test_config_set_add_on_scalar_errors(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("poll_interval", "5", add=True, path=p) == 1


def test_config_set_preserves_comments(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("# 轮询秒数\npoll_interval: 300\nkeywords: []\n"
                 "ai:\n  provider: claude\n  model: m\n  api_key: k\n  base_url: ''\n"
                 "notify:\n  user_id: ou\n  bot_chat_id: oc\n"
                 "lark_cli_appid: cli\n", encoding="utf-8")
    config_cli.config_set("poll_interval", "600", path=p)
    assert "# 轮询秒数" in p.read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_config_cli.py::test_config_set_scalar -v`
Expected: FAIL — `AttributeError: ... has no attribute 'config_set'`

- [ ] **Step 3: 实现 `config_set`**

在 `lark_listener/config_cli.py` 追加：

```python
def config_set(key: str, value: str, add: bool = False, remove: bool = False,
               force: bool = False, path: Optional[str | Path] = None) -> int:
    cfg_path = _config_path(path)
    top = key.split(".")[0]
    if top in PROTECTED and not force:
        print(f"❌ {top} 受保护，需加 --force 才能通过 CLI 修改")
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

    old = current
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
        dump_roundtrip(cfg_path, data)
        print(f"❌ 校验失败，已回滚：{e}")
        return 1

    print(f"✓ {key}: {old!r} → {new_value!r}（下次轮询生效）")
    return 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_config_cli.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/config_cli.py tests/test_config_cli.py
git commit -m "feat(config-cli): config_set with dotted paths, list ops, --force, validate+rollback"
```

---

## Task 8: 打包 SKILL.md（包内资源 + pyproject）

**Files:**
- Create: `lark_listener/skills/lark-listener/SKILL.md`
- Modify: `pyproject.toml`
- Test: `tests/test_agent_adapters.py`（先建，仅测资源可读）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_agent_adapters.py`：

```python
from importlib import resources


def test_skill_resource_is_packaged():
    src = resources.files("lark_listener").joinpath("skills", "lark-listener", "SKILL.md")
    text = src.read_text(encoding="utf-8")
    assert "LarkListener" in text
    assert "doctor" in text  # 必须指向 doctor 作为事实源
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_agent_adapters.py::test_skill_resource_is_packaged -v`
Expected: FAIL — 文件不存在（`FileNotFoundError` / `NotADirectoryError`）

- [ ] **Step 3: 建 SKILL.md + 配 package-data**

新建 `lark_listener/skills/lark-listener/SKILL.md`：

```markdown
---
name: lark-listener
description: 管理与诊断本机的 LarkListener 飞书消息汇总后台服务。当用户提到 LarkListener / 飞书汇总服务 / 飞书消息总结 bot，或要启动/停止/重启、查看状态、改配置、排查「不工作/收不到汇总/bot 不回」等运行时问题时使用。
---

# 操作 LarkListener（macOS 后台服务）

LarkListener 是装在本机的 launchd 后台服务：定时拉飞书未读 → AI 汇总 → bot 私聊推送。
本 skill 教你（AI）安装后如何安全操作它。**`lark-listener --help` 与 `lark-listener doctor`
是契约的唯一事实源——本文若与其冲突，以命令输出为准。**

## 先诊断
排查任何问题，先跑（机读）：
```bash
lark-listener doctor --json     # 主动自检：config/服务/lark-cli 授权/轮询时效/日志/AI 后端
lark-listener status --json     # 服务三态 + 进程 PID + 文件位置 + 上次轮询
```
`doctor` 每项带 `fix` 字段，直接给修复命令。退出码：status 0=运行/3=停/4=未装；doctor 0=全过/1=有 fail。

## ✅ 可直接（非交互）运行
- `lark-listener start | stop | restart` — 服务控制
- `lark-listener status [--json]` / `lark-listener doctor [--json] [--deep]`
- `lark-listener config get [KEY] [--json]` — 查看配置（api_key 已脱敏）
- `lark-listener config set KEY VALUE [--add|--remove] [--force]` — 改配置，下次轮询生效（不重启）
  - 点号路径：`poll_interval`、`keywords`、`ai.model`、`notify.user_id`、`lark_cli_appid` 等
  - 列表：整体 `config set keywords a,b`；增 `--add`；减 `--remove`
  - 受保护项（`ai`/`notify`/`lark_cli_appid`）需 `--force`
- `lark-listener agent-skills install|uninstall`

## 🚫 不要无人值守运行（会卡 stdin / 弹 GUI）
- `lark-listener setup`（交互向导）、`lark-listener uninstall`（二次确认）、
  `lark-listener config`（无参=开 GUI 编辑器）——交给用户在自己终端跑。

## 常见修复
- 拉不到消息 → lark-cli 授权过期：`lark-cli auth login --scope search:message`
- bot 不回 → `lark-listener status`，没跑就 `lark-listener start`
- 升级后行为没变 → 必须 `lark-listener restart`
- 日志：`tail -n 100 ~/.lark_listener/logs/stderr.log`

## 路径
`~/.lark_listener/`（config.yaml / state.json / logs / venv）；
`~/Library/LaunchAgents/com.larklistener.plist`。
```

在 `pyproject.toml` 的 `[tool.setuptools.packages.find]` 段**之后**追加（**非递归 glob**：setuptools 的 `package-data` 不可靠支持 `**`，我们的结构正好是 `skills/<name>/*.md` 两级）：

```toml
[tool.setuptools.package-data]
lark_listener = ["skills/*/*.md"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_agent_adapters.py::test_skill_resource_is_packaged -v`
Expected: PASS

验证打包生效（**真实安装**，非 editable —— editable 直读源码树，无法证明数据被打进分发）：

Run:
```bash
python3 -m pip install . --target /tmp/ll-pkgcheck --no-deps -q && \
test -f /tmp/ll-pkgcheck/lark_listener/skills/lark-listener/SKILL.md && echo PACKAGED-OK
rm -rf /tmp/ll-pkgcheck
```
Expected: 打印 `PACKAGED-OK`（证明 `package-data` 把 SKILL.md 打进了已安装的包）。

- [ ] **Step 5: 提交**

```bash
git add lark_listener/skills/lark-listener/SKILL.md pyproject.toml tests/test_agent_adapters.py
git commit -m "feat(skill): packaged Claude Code operating skill + package-data"
```

---

## Task 9: `agent_adapters.py`

**Files:**
- Create: `lark_listener/agent_adapters.py`
- Test: `tests/test_agent_adapters.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path
from lark_listener import agent_adapters


def test_claude_adapter_detect(tmp_path):
    claude = tmp_path / ".claude"
    ad = agent_adapters.ClaudeCodeAdapter(skills_dir=claude / "skills")
    assert ad.detect() is False
    claude.mkdir()
    assert ad.detect() is True


def test_claude_adapter_install_uninstall(tmp_path):
    claude = tmp_path / ".claude"; claude.mkdir()
    ad = agent_adapters.ClaudeCodeAdapter(skills_dir=claude / "skills")
    ad.install()
    skill = claude / "skills" / "lark-listener" / "SKILL.md"
    assert skill.is_file() and "LarkListener" in skill.read_text(encoding="utf-8")
    ad.uninstall()
    assert not (claude / "skills" / "lark-listener").exists()


def test_install_agent_skills_skips_undetected(tmp_path, capsys):
    ad = agent_adapters.ClaudeCodeAdapter(skills_dir=tmp_path / "absent" / "skills")
    code = agent_adapters.install_agent_skills(adapters=[ad])
    assert code == 0
    assert "跳过" in capsys.readouterr().out


def test_install_agent_skills_best_effort(tmp_path, monkeypatch, capsys):
    claude = tmp_path / ".claude"; claude.mkdir()
    ad = agent_adapters.ClaudeCodeAdapter(skills_dir=claude / "skills")
    monkeypatch.setattr(ad, "install", lambda: (_ for _ in ()).throw(OSError("denied")))
    code = agent_adapters.install_agent_skills(adapters=[ad])
    assert code == 0  # 失败不阻断
    assert "失败" in capsys.readouterr().out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_agent_adapters.py::test_claude_adapter_detect -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.agent_adapters'`

- [ ] **Step 3: 实现 adapters**

新建 `lark_listener/agent_adapters.py`：

```python
from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path
from typing import Optional

SKILL_NAME = "lark-listener"


class ClaudeCodeAdapter:
    name = "claude-code"

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or (Path.home() / ".claude" / "skills")

    def detect(self) -> bool:
        # ~/.claude 存在即认为用户在用 Claude Code（非侵入前提）
        return self.skills_dir.parent.exists()

    def install(self) -> None:
        dest = self.skills_dir / SKILL_NAME
        dest.mkdir(parents=True, exist_ok=True)
        src = resources.files("lark_listener").joinpath("skills", SKILL_NAME, "SKILL.md")
        (dest / "SKILL.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    def uninstall(self) -> None:
        dest = self.skills_dir / SKILL_NAME
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)


# 注册表：将来加 MCP 适配器只需往这里追加一个类
ADAPTERS = [ClaudeCodeAdapter]


def _default_adapters():
    return [cls() for cls in ADAPTERS]


def install_agent_skills(adapters=None) -> int:
    adapters = adapters if adapters is not None else _default_adapters()
    installed = []
    for ad in adapters:
        try:
            if ad.detect():
                ad.install()
                installed.append(ad.name)
        except Exception as e:  # noqa: BLE001 — best-effort，不阻断安装
            print(f"  ⚠️ {ad.name} skill 安装失败（{e}），不影响服务运行。")
    if installed:
        print(f"✓ 已为 {', '.join(installed)} 安装操作 skill。")
    else:
        print("（未检测到受支持的 AI Agent，跳过 skill 安装）")
    return 0


def uninstall_agent_skills(adapters=None) -> int:
    adapters = adapters if adapters is not None else _default_adapters()
    for ad in adapters:
        try:
            ad.uninstall()
        except Exception:  # noqa: BLE001
            pass
    return 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_agent_adapters.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/agent_adapters.py tests/test_agent_adapters.py
git commit -m "feat(adapters): pluggable agent-skill registry + ClaudeCodeAdapter"
```

---

## Task 10: `cmd_uninstall` 接入 agent-skills 清理

**Files:**
- Modify: `lark_listener/service.py`（`cmd_uninstall`）
- Test: `tests/test_service.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_cmd_uninstall_calls_agent_skills(monkeypatch, tmp_path):
    plist = tmp_path / "svc.plist"; plist.write_text("x")
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "LISTENER_HOME", home)
    monkeypatch.setattr(service, "SHIM_LINK", tmp_path / "shim")
    monkeypatch.setattr(service, "SHIM_RECORD", home / "shim_link")
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    called = {"n": 0}
    import lark_listener.agent_adapters as aa
    monkeypatch.setattr(aa, "uninstall_agent_skills", lambda: called.__setitem__("n", called["n"] + 1) or 0)
    assert service.cmd_uninstall() == 0
    assert called["n"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_service.py::test_cmd_uninstall_calls_agent_skills -v`
Expected: FAIL — `uninstall_agent_skills` 未被调用（`called["n"] == 0`）

- [ ] **Step 3: 在 `cmd_uninstall` 中调用清理**

在 `cmd_uninstall` 里、`shutil.rmtree(LISTENER_HOME, ...)` 之前插入（确认前的取消分支无需改）：

```python
    # 清理为各 AI Agent 安装的操作 skill（独立目录，与 LISTENER_HOME 无耦合）
    from lark_listener.agent_adapters import uninstall_agent_skills
    uninstall_agent_skills()
```

具体位置：在 `stop_service()` 之后、删 plist/软链/`rmtree` 这段之中任意一处（rmtree 之前）即可。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_service.py -k uninstall -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add lark_listener/service.py tests/test_service.py
git commit -m "feat(uninstall): remove agent skills on uninstall"
```

---

## Task 11: `main.py` argparse 重构 + 退出码分发 + help 标注

**Files:**
- Modify: `lark_listener/main.py:427-470`（重写 `main()`，确保顶部 `import sys`）
- Test: `tests/test_main.py`（追加分发测试）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_main.py`（顶部若无则加 `import pytest`、`from lark_listener import main as main_mod`）：

```python
def test_main_status_dispatch_exit_code(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "status", "--json"])
    from lark_listener import service
    monkeypatch.setattr(service, "cmd_status", lambda as_json=False: 3 if as_json else 0)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 3


def test_main_config_set_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv",
                        ["lark-listener", "config", "set", "keywords", "上线", "--add"])
    captured = {}
    from lark_listener import config_cli
    def fake_set(key, value, add=False, remove=False, force=False):
        captured.update(key=key, value=value, add=add, remove=remove, force=force)
        return 0
    monkeypatch.setattr(config_cli, "config_set", fake_set)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0
    assert captured == {"key": "keywords", "value": "上线", "add": True,
                        "remove": False, "force": False}


def test_main_doctor_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "doctor", "--deep"])
    from lark_listener import doctor
    seen = {}
    monkeypatch.setattr(doctor, "cmd_doctor",
                        lambda as_json=False, deep=False: seen.update(deep=deep) or 1)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 1 and seen["deep"] is True


def test_main_agent_skills_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv", ["lark-listener", "agent-skills", "install"])
    from lark_listener import agent_adapters
    monkeypatch.setattr(agent_adapters, "install_agent_skills", lambda: 0)
    with pytest.raises(SystemExit) as ei:
        main_mod.main()
    assert ei.value.code == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_main.py::test_main_doctor_dispatch -v`
Expected: FAIL — 旧 `main()` 不识别 `doctor` 子命令（argparse error / 无 SystemExit code 1）

- [ ] **Step 3: 重写 `main()`**

确保 `lark_listener/main.py` 顶部有 `import sys`（无则加）。把 `def main(): ... ` 整段（427-470）替换为：

```python
def main():
    ensure_path()
    parser = argparse.ArgumentParser(
        prog="lark-listener",
        description="飞书消息汇总后台服务：定时拉取未读消息 → AI 分析 → Bot 私聊推送汇总 + macOS 通知。",
        epilog=(
            "AI agent 操作入口：`lark-listener doctor --json`（自检）与 "
            "`lark-listener status --json`（状态）是排查起点。\n"
            "✅ 可非交互运行：start/stop/restart/status/doctor/config get/config set/agent-skills。\n"
            "🚫 交互式·交给用户：setup、uninstall、config（无参开 GUI）。\n"
            "\n"
            "配置文件：~/.lark_listener/config.yaml；日志：~/.lark_listener/logs/stderr.log\n"
            "首次安装后请先运行 `lark-listener setup`。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("run", help="运行守护循环（launchd 调用，一般无需手动跑）")
    sub.add_parser("setup", help="🚫 交互式·交给用户：安装向导（选 Bot/AI/轮询/授权）")
    sub.add_parser("start", help="✅ 启动后台服务")
    sub.add_parser("stop", help="✅ 停止后台服务")
    sub.add_parser("restart", help="✅ 重启服务（升级或改代码后需要）")

    p_status = sub.add_parser("status", help="✅ 查看服务运行状态")
    p_status.add_argument("--json", action="store_true", help="机读 JSON 输出")

    p_doctor = sub.add_parser("doctor", help="✅ 主动自检诊断（排查起点）")
    p_doctor.add_argument("--json", action="store_true", help="机读 JSON 输出")
    p_doctor.add_argument("--deep", action="store_true", help="对 AI 后端发真实最小请求")

    p_config = sub.add_parser(
        "config", help="✅ get/set 非交互改配置；🚫 无参=打开编辑器（人用）")
    csub = p_config.add_subparsers(dest="op")
    p_cget = csub.add_parser("get", help="✅ 查看配置（api_key 脱敏）")
    p_cget.add_argument("key", nargs="?", help="点号路径，如 ai.provider；省略=全部")
    p_cget.add_argument("--json", action="store_true")
    p_cset = csub.add_parser("set", help="✅ 改配置（点号路径）")
    p_cset.add_argument("key")
    p_cset.add_argument("value")
    grp = p_cset.add_mutually_exclusive_group()
    grp.add_argument("--add", action="store_true", help="列表：增一项")
    grp.add_argument("--remove", action="store_true", help="列表：减一项")
    p_cset.add_argument("--force", action="store_true", help="放行受保护项 ai/notify/lark_cli_appid")

    p_as = sub.add_parser("agent-skills", help="✅ 安装/卸载 AI Agent 操作 skill")
    p_as.add_argument("op", choices=["install", "uninstall"])

    sub.add_parser("uninstall", help="🚫 交互式·交给用户：卸载（二次确认）")

    args = parser.parse_args()
    cmd = args.command

    if cmd == "run":
        run()
        return
    if cmd == "setup":
        from lark_listener.setup_wizard import cmd_setup
        cmd_setup()
        return

    from lark_listener import service
    if cmd == "start":
        sys.exit(service.cmd_start())
    if cmd == "stop":
        sys.exit(service.cmd_stop())
    if cmd == "restart":
        sys.exit(service.cmd_restart())
    if cmd == "status":
        sys.exit(service.cmd_status(as_json=args.json))
    if cmd == "uninstall":
        sys.exit(service.cmd_uninstall())
    if cmd == "doctor":
        from lark_listener import doctor
        sys.exit(doctor.cmd_doctor(as_json=args.json, deep=args.deep))
    if cmd == "config":
        if not args.op:
            sys.exit(service.cmd_config())
        from lark_listener import config_cli
        if args.op == "get":
            sys.exit(config_cli.config_get(args.key, as_json=args.json))
        sys.exit(config_cli.config_set(args.key, args.value, add=args.add,
                                       remove=args.remove, force=args.force))
    if cmd == "agent-skills":
        from lark_listener import agent_adapters
        if args.op == "install":
            sys.exit(agent_adapters.install_agent_skills())
        sys.exit(agent_adapters.uninstall_agent_skills())

    parser.print_help()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: PASS（新增 4 个 + 原有全过）

- [ ] **Step 5: 提交**

```bash
git add lark_listener/main.py tests/test_main.py
git commit -m "feat(cli): restructure argparse, exit-code dispatch, agent-safe help"
```

---

## Task 12: `install.sh` best-effort 安装 skill

**Files:**
- Modify: `install.sh`（第 5 步「安装本工具」之后）

- [ ] **Step 1: 加 agent-skills 安装调用**

在 `install.sh` 中 `pip install`（约 49-52 行）成功之后、软链步骤（约 56 行）之前，插入：

```bash
# 5b) 为受支持的 AI Agent 安装操作 skill（best-effort：失败不阻断安装；
#     用 venv 绝对路径，因短命令软链可能尚未进 PATH）
"$VENV/bin/lark-listener" agent-skills install || true
```

- [ ] **Step 2: 手动验证（隔离）**

Run:
```bash
python3 -m venv /tmp/ll-skill-venv && /tmp/ll-skill-venv/bin/pip install --upgrade pip -q && /tmp/ll-skill-venv/bin/pip install -e . -q
HOME=/tmp/ll-fakehome mkdir -p /tmp/ll-fakehome/.claude
HOME=/tmp/ll-fakehome /tmp/ll-skill-venv/bin/lark-listener agent-skills install
ls /tmp/ll-fakehome/.claude/skills/lark-listener/SKILL.md
```
Expected: 打印「已为 claude-code 安装操作 skill」且 `SKILL.md` 存在。

清理：`rm -rf /tmp/ll-skill-venv /tmp/ll-fakehome`

- [ ] **Step 3: 提交**

```bash
git add install.sh
git commit -m "feat(install): best-effort install agent skill after pip install"
```

---

## Task 13: 更新 `AGENTS.md`

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: 在「✅ Safe for you to run」段补充新命令**

把 `AGENTS.md` 中 `## ✅ Safe for you to run` 列表替换为（在原有基础上加 doctor / config get-set / agent-skills，并把 status 标 --json）：

```markdown
## ✅ Safe for you to run

- `lark-listener doctor [--json] [--deep]` — active self-check (config / service /
  lark-cli auth / poll freshness / logs / AI backend), each finding carries a `fix`.
  **Start here when something is wrong.** Exit 0 = all pass, 1 = has a fail.
- `lark-listener status [--json]` — service state + main/listener PIDs + file
  locations + last poll. Exit 0 running / 3 stopped / 4 not installed.
- `lark-listener config get [KEY] [--json]` — view config (api_key masked).
- `lark-listener config set KEY VALUE [--add|--remove] [--force]` — non-interactive
  edit; dotted paths (`poll_interval`, `keywords`, `ai.model`, …); protected keys
  (`ai`/`notify`/`lark_cli_appid`) need `--force`; takes effect next poll.
- `lark-listener start | stop | restart` — non-interactive service control.
- `lark-listener agent-skills install | uninstall` — manage on-machine operating skill.
- `lark-cli profile list` — enumerate available bots.
- `tail -n 100 ~/.lark_listener/logs/stderr.log` — logs.
```

- [ ] **Step 2: 在文件末尾「Paths」前加一句关于 skill 的说明**

在 `## Paths` 之前插入：

```markdown
## On-machine discovery (Claude Code)

Installing LarkListener also drops a Claude Code skill at
`~/.claude/skills/lark-listener/` (when `~/.claude/` exists), so any later Claude
session auto-discovers how to operate the service — no need to re-fetch this file.
The skill defers to `lark-listener --help` / `doctor` as the source of truth.
```

- [ ] **Step 3: 提交**

```bash
git add AGENTS.md
git commit -m "docs(agents): document doctor/config CLI + on-machine skill"
```

---

## Task 14: 全量回归 + smoke

**Files:** 无（验证）

- [ ] **Step 1: 全量单测**

Run: `python3 -m pytest -q`
Expected: 全绿（无 fail / error）

- [ ] **Step 2: smoke（隔离生命周期 + 新命令）**

Run: `./dev-test.sh smoke`
Expected: 安装文件层→状态→卸载流程通过、自我清理；脚本无 `set -e`，新退出码不致中断。

补充手动验证新命令（隔离）：
```bash
LARK_LISTENER_HOME=/tmp/ll-dev python3 -m lark_listener.main status --json; echo "exit=$?"
```
Expected: 打印 JSON（state=not_installed），`exit=4`。

- [ ] **Step 3: 提交（如有遗留格式/lint 修整）**

```bash
git add -A
git commit -m "test: full regression green for agent operability"
```

---

## 自检（写完计划后回看 spec）

- **覆盖**：① status --json+退出码 → T1/T2；start 非零 → T3；② doctor 浅/深 → T4/T5；③ config get/set（点号/列表三态/force/回滚/脱敏）→ T6/T7；④ 自描述 help → T11；推送层 adapter+skill+打包 → T8/T9，install/uninstall 接入 → T10/T12；AGENTS.md → T13；测试约束 → 各任务 TDD + T14。
- **占位**：无 TBD/TODO；每个 code step 均含完整代码。
- **类型/签名一致**：`collect_status()` dict 字段在 T2/doctor 一致；`Check`(check/status/detail/fix) 全程一致；`config_set(key,value,add,remove,force,path)` 在 T7 定义、T11 调用一致；`install_agent_skills/uninstall_agent_skills` 在 T9 定义、T10/T11/T12 调用一致；退出码约定全程一致。
