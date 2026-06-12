# LarkListener 友好分发（标准库 venv 路线）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> ⚠️ **方案变更（2026-06-05，实现中调整）：分发从 pipx 改为标准库 `venv`**，以「不让用户多装任何工具 + 彻底回避 PEP 668」为由。实际代码已是 venv 终态；本计划下文凡涉及 **pipx** 的描述（尤其 Task 4 的 `shim_path`、Task 6 的 `install.sh`、Task 7 的升级命令、Task 8 的验证）**以实际代码与设计文档为准**。关键差异：
> - `install.sh` 用 `python3 -m venv ~/.lark_listener/venv` 建环境 → venv 的 `pip install git+...` → `ln -sf` 软链 `~/.local/bin/lark-listener`（无 pipx、无 PEP668 回退）。
> - `service.shim_path()` 直接返回 `~/.lark_listener/venv/bin/lark-listener`（确定路径，不再 which/argv0 兜底）；新增 `service.ensure_shim_link()`。
> - `cmd_uninstall` 删软链 + plist + `~/.lark_listener`（含 venv），不再提示 `pipx uninstall`。
> - 升级：`~/.lark_listener/venv/bin/pip install --force-reinstall "git+..."` + `restart`。

**Goal:** 把 LarkListener 从「PyInstaller 二进制 + 双击 .command」改为「`curl|bash` + 标准库 venv」分发，消除 macOS Gatekeeper 弹窗，并把管理菜单替换为 `lark-listener` 子命令。

**Architecture:** `lark-listener` 仍是单一 Python console script（`lark_listener.main:main`），`main()` 改为 argparse 分发器：`run` 跑守护循环（旧 `main()` 主体），`setup` 是交互安装向导，`start/stop/restart/status/config/uninstall` 是 launchd/进程管理薄封装。守护进程逻辑（`poll_once` / `_handle_message` 等）保持原位、原签名以不破坏现有测试。桌面通知默认走系统原生 `osascript`，检测到 `terminal-notifier` 时仍用它。

**Tech Stack:** Python 3.9+（CLT 自带）、标准库 venv、argparse、ruamel.yaml（已是依赖）、launchd、lark-cli。

设计来源：`docs/superpowers/specs/2026-06-05-friendly-distribution-design.md`

---

## File Structure

- `pyproject.toml` — 加 `[build-system]` 与 `[tool.setuptools.packages.find]`（修复 `pipx install git+...` 构建失败）。
- `lark_listener/main.py` — 拆出 `run()`，`main()` 改 argparse 分发；守护逻辑符号不动。
- `lark_listener/notifier.py` — `_send_macos_notification` 改 osascript 默认 + 转义；新增 `_applescript_escape`。
- `lark_listener/service.py` — **新增**。launchd/进程管理：路径解析、plist 生成、`stop_service` 与 `cmd_start/stop/restart/status/config/uninstall`。
- `lark_listener/setup_wizard.py` — **新增**。交互安装向导 `cmd_setup` 及其纯函数辅助（`build_config_dict` / `write_config_file`）。
- `install.sh` — **新增**。`curl|bash` 引导脚本（python/git/pipx 检测、pipx 引导、检测 lark-cli、`pipx install`）。
- `tests/test_notifier.py` — 改：覆盖 osascript 默认 / terminal-notifier 分支 / 转义。
- `tests/test_main.py` — 加：argparse 分发测试。
- `tests/test_service.py` — **新增**。`shim_path` / `node_bin_dir` / `build_plist` 纯函数测试。
- `tests/test_setup_wizard.py` — **新增**。`build_config_dict` / `write_config_file` 纯函数测试。
- 删：`build.sh`、`run_service.py`、`LarkListener.command`、`requirements.txt`、`build/`。
- 改：`README.md` — 单行 curl 安装 + `setup` + 子命令 + 升级说明。

---

## Task 1: pyproject 加 build-system 与打包范围

让 `pipx install git+...` 能在构建阶段成功（当前缺 `[build-system]`，且 `tests/__init__.py` 会触发 flat-layout 多包发现报错）。

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 追加 build-system 与 packages.find**

在 `pyproject.toml` 末尾（`[project.scripts]` 之后）追加：

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["lark_listener*"]
```

- [ ] **Step 2: 验证可构建（不污染环境）**

Run:
```bash
cd /Users/altair/Documents/Projects/LarkListener && python3 -m pip install --quiet build && python3 -m build --wheel --outdir /tmp/ll_build_check 2>&1 | tail -5
```
Expected: 成功生成 `lark_listener-0.1.0-*.whl`，**不出现** `Multiple top-level packages discovered in a flat-layout`。

- [ ] **Step 3: 确认 wheel 不含 tests 包**

Run:
```bash
python3 -c "import zipfile,glob; z=zipfile.ZipFile(glob.glob('/tmp/ll_build_check/*.whl')[0]); print([n for n in z.namelist() if n.startswith('tests/')] or 'OK: no tests packaged')"
```
Expected: `OK: no tests packaged`

- [ ] **Step 4: 清理并提交**

```bash
rm -rf /tmp/ll_build_check
git add pyproject.toml
git commit -m "build: add build-system and packaging config for pipx install"
```

---

## Task 2: 桌面通知改 osascript 默认（保留 terminal-notifier）

砍掉对 `terminal-notifier`（brew）的硬依赖。无 terminal-notifier 时用系统原生 `osascript`；检测到则仍用它（保留点击跳转）。

**Files:**
- Modify: `lark_listener/notifier.py`
- Test: `tests/test_notifier.py`

- [ ] **Step 1: 写转义函数的失败测试**

在 `tests/test_notifier.py` 末尾追加：

```python
from lark_listener.notifier import _applescript_escape


def test_applescript_escape_quotes_and_backslashes():
    assert _applescript_escape('a"b') == 'a\\"b'
    assert _applescript_escape("a\\b") == "a\\\\b"
    # 反斜杠先转义，避免把已转义的引号再次破坏
    assert _applescript_escape('x"\\y') == 'x\\"\\\\y'
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd /Users/altair/Documents/Projects/LarkListener && python3 -m pytest tests/test_notifier.py::test_applescript_escape_quotes_and_backslashes -v`
Expected: FAIL（`ImportError: cannot import name '_applescript_escape'`）

- [ ] **Step 3: 实现转义函数**

在 `lark_listener/notifier.py` 顶部 import 区下方（`logger = ...` 之后）加：

```python
def _applescript_escape(s: str) -> str:
    """Escape a string for embedding in an AppleScript double-quoted literal.

    Backslash must be escaped first, otherwise the backslashes we add for quotes
    would themselves be re-escaped.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')
```

并在文件顶部 import 区加入 `import os`（若尚无）：

```python
import os
```

- [ ] **Step 4: 运行，确认通过**

Run: `python3 -m pytest tests/test_notifier.py::test_applescript_escape_quotes_and_backslashes -v`
Expected: PASS

- [ ] **Step 5: 写「无 terminal-notifier 走 osascript」的失败测试**

在 `tests/test_notifier.py` 末尾追加：

```python
@patch("lark_listener.notifier.resolve_executable")
@patch("lark_listener.notifier.subprocess.run")
def test_notify_uses_osascript_when_no_terminal_notifier(mock_run, mock_resolve):
    # terminal-notifier 未安装 → resolve 返回裸名；osascript 解析到绝对路径。
    def resolve(name):
        return "terminal-notifier" if name == "terminal-notifier" else "/usr/bin/" + name
    mock_resolve.side_effect = resolve
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    assert mock_run.call_count == 2
    second = mock_run.call_args_list[1][0][0]
    assert second[0].endswith("osascript")
    assert "-e" in second
    script = second[second.index("-e") + 1]
    assert "display notification" in script
    assert "1个私聊" in script
```

- [ ] **Step 6: 写「有 terminal-notifier 仍用它」的失败测试**

继续追加：

```python
@patch("lark_listener.notifier.resolve_executable")
@patch("lark_listener.notifier.subprocess.run")
def test_notify_prefers_terminal_notifier_when_present(mock_run, mock_resolve):
    # terminal-notifier 解析到绝对路径 → 走它，保留 -open 点击跳转。
    def resolve(name):
        return "/opt/homebrew/bin/" + name
    mock_resolve.side_effect = resolve
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30", my_user_id=MY_USER_ID)

    second = mock_run.call_args_list[1][0][0]
    assert second[0].endswith("terminal-notifier")
    assert "-open" in second
```

- [ ] **Step 7: 改既有两处「断言第二个调用是 terminal-notifier」的旧测试**

旧测试 `test_notify_sends_message_and_notification`（约 :273）与 `test_notify_macos_notification_counts`（约 :301）默认环境下现在会走 osascript，使断言失效。给它们都加 `resolve_executable` patch 强制 terminal-notifier 存在，保持原断言成立。两处函数签名分别改成：

`test_notify_sends_message_and_notification`：
```python
@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_sends_message_and_notification(mock_run, mock_resolve):
```

`test_notify_macos_notification_counts`：
```python
@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_macos_notification_counts(mock_run, mock_resolve):
```
（注意：`@patch` 自下而上注入参数，`mock_run` 在前、`mock_resolve` 在后。函数体不变。）

`test_notify_survives_failing_desktop_notification`（:318）的 `side_effect` 按 `cmd[0].endswith("terminal-notifier")` 判断——加同样的 resolve patch 强制 terminal-notifier 路径，使该分支仍被触发：
```python
@patch("lark_listener.notifier.resolve_executable", side_effect=lambda n: "/opt/homebrew/bin/" + n)
@patch("lark_listener.notifier.subprocess.run")
def test_notify_survives_failing_desktop_notification(mock_run, mock_resolve):
```

- [ ] **Step 8: 运行新测试，确认失败（实现尚未改）**

Run: `python3 -m pytest tests/test_notifier.py -v`
Expected: 新的 osascript 两个用例 FAIL（仍调用 terminal-notifier）。

- [ ] **Step 9: 改 `_send_macos_notification` 实现**

把 `lark_listener/notifier.py` 中 `_send_macos_notification` 内从 `open_url = ...` 之后到 `except` 之前的命令构造段替换为：

```python
        open_url = f"https://applink.feishu.cn/client/chat/open?openChatId={self.bot_chat_id}"

        # 优先 terminal-notifier（解析到绝对路径才算装了），它支持点击跳转飞书会话；
        # 否则退回系统原生 osascript（零依赖，但点击不可跳转）。
        tn = resolve_executable("terminal-notifier")
        if os.path.isabs(tn):
            cmd = [
                tn,
                "-title", "LarkListener",
                "-subtitle", "有新消息汇总",
                "-message", message,
                "-open", open_url,
            ]
        else:
            title = _applescript_escape("LarkListener")
            body = _applescript_escape(message)
            cmd = [
                resolve_executable("osascript"),
                "-e", f'display notification "{body}" with title "{title}"',
            ]
```

`try/except subprocess.run(...)` 那段保持不变（仍 best-effort，失败仅 warning）。把 `except` 里的提示文案改为不再强推 brew：

```python
        except Exception as e:
            logger.warning("Desktop notification skipped (%s).", e)
```

- [ ] **Step 10: 运行全部通知测试，确认通过**

Run: `python3 -m pytest tests/test_notifier.py -v`
Expected: PASS（全部）

- [ ] **Step 11: 提交**

```bash
git add lark_listener/notifier.py tests/test_notifier.py
git commit -m "feat: default desktop notification to osascript, drop terminal-notifier hard dep"
```

---

## Task 3: main.py 拆出 run() + argparse 分发器

`main()` 改为只解析 argv 并分发；守护循环主体移入 `run()`。`poll_once` / `_handle_message` / `_reply_bot` / `_add_reaction` / `_pending_change` 等被 `tests/test_main.py` 依赖的符号保持原位、原签名。

**Files:**
- Modify: `lark_listener/main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: 写分发测试（失败）**

在 `tests/test_main.py` 末尾追加：

```python
import sys as _sys


@patch("lark_listener.main.run")
def test_main_run_subcommand_invokes_run(mock_run, monkeypatch):
    monkeypatch.setattr(_sys, "argv", ["lark-listener", "run"])
    main_mod.main()
    mock_run.assert_called_once()


@patch("lark_listener.main.run")
def test_main_no_subcommand_does_not_run(mock_run, monkeypatch):
    monkeypatch.setattr(_sys, "argv", ["lark-listener"])
    main_mod.main()
    mock_run.assert_not_called()


@patch("lark_listener.service.cmd_start")
def test_main_start_subcommand_dispatches_to_service(mock_start, monkeypatch):
    monkeypatch.setattr(_sys, "argv", ["lark-listener", "start"])
    main_mod.main()
    mock_start.assert_called_once()
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd /Users/altair/Documents/Projects/LarkListener && python3 -m pytest tests/test_main.py::test_main_run_subcommand_invokes_run -v`
Expected: FAIL（`AttributeError: ... has no attribute 'run'`，或导入 `lark_listener.service` 失败）

> 注：本步会导入 `lark_listener.service`，该模块在 Task 4 创建。为让本任务可独立通过，先在 Task 4 之前临时建一个最小 `service.py`（仅含 `def cmd_start(): ...`）也可；推荐的执行顺序是先做 Task 4 再回填本测试。**若按编号顺序执行，请将本 Step 的 `test_main_start_subcommand_dispatches_to_service` 留到 Task 4 完成后再加。** 其余两个 run 相关测试不依赖 service，可立即进行。

- [ ] **Step 3: 改 `main.py` 顶部 import**

在 `import json` 等之后加：

```python
import argparse
```

- [ ] **Step 4: 把现有 `main()` 改名为 `run()`，去掉其中的 `ensure_path()`**

把当前 `def main():`（约 :321）整体改名为 `def run():`，并**删除函数体第一行的 `ensure_path()`**（移到新的 `main()`）。其余函数体（signal 注册、daemon 循环）保持不变。

- [ ] **Step 5: 新增 argparse 分发器 `main()`**

在 `run()` 之后、`if __name__ == "__main__":` 之前加：

```python
def main():
    ensure_path()
    parser = argparse.ArgumentParser(prog="lark-listener")
    sub = parser.add_subparsers(dest="command")
    for name in ("run", "setup", "start", "stop", "restart", "status", "config", "uninstall"):
        sub.add_parser(name)
    args = parser.parse_args()

    if args.command == "run":
        run()
    elif args.command == "setup":
        from lark_listener.setup_wizard import cmd_setup
        cmd_setup()
    elif args.command in ("start", "stop", "restart", "status", "config", "uninstall"):
        from lark_listener import service
        getattr(service, f"cmd_{args.command}")()
    else:
        parser.print_help()
```

`if __name__ == "__main__": main()` 保持不变。

- [ ] **Step 6: 运行 run 相关分发测试，确认通过**

Run: `python3 -m pytest tests/test_main.py::test_main_run_subcommand_invokes_run tests/test_main.py::test_main_no_subcommand_does_not_run -v`
Expected: PASS

- [ ] **Step 7: 运行全部 main 测试，确认重构未破坏守护逻辑**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: PASS（`poll_once` / `_handle_message` / `_add_reaction` 既有用例全绿）

- [ ] **Step 8: 提交**

```bash
git add lark_listener/main.py tests/test_main.py
git commit -m "refactor: split main() into run() daemon + argparse dispatcher"
```

---

## Task 4: 新增 service.py（路径解析、plist 生成、管理命令）

承载 `start/stop/restart/status/config/uninstall` 与共享的路径/plist 辅助函数。纯函数走 TDD；launchctl 交互部分用真实命令包装并在末尾人工验证。

**Files:**
- Create: `lark_listener/service.py`
- Test: `tests/test_service.py`

- [ ] **Step 1: 写纯函数失败测试**

创建 `tests/test_service.py`：

```python
import os
from unittest.mock import patch
from lark_listener import service


def test_shim_path_prefers_which():
    with patch("lark_listener.service.shutil.which", return_value="/custom/bin/lark-listener"):
        assert service.shim_path() == "/custom/bin/lark-listener"


def test_shim_path_falls_back_to_local_bin():
    with patch("lark_listener.service.shutil.which", return_value=None):
        with patch("lark_listener.service.sys") as mock_sys:
            mock_sys.argv = ["lark-listener"]  # 非绝对路径，触发兜底
            p = service.shim_path()
    assert p.endswith("/.local/bin/lark-listener")
    assert os.path.isabs(p)


def test_node_bin_dir_returns_dirname():
    with patch("lark_listener.service.shutil.which", return_value="/Users/x/.nvm/versions/node/v20/bin/node"):
        assert service.node_bin_dir() == "/Users/x/.nvm/versions/node/v20/bin"


def test_node_bin_dir_none_when_missing():
    with patch("lark_listener.service.shutil.which", return_value=None):
        assert service.node_bin_dir() is None


def test_build_plist_uses_absolute_program_and_run():
    xml = service.build_plist("/Users/x/.local/bin/lark-listener", ["/Users/x/.nvm/versions/node/v20/bin"])
    assert "<string>/Users/x/.local/bin/lark-listener</string>" in xml
    assert "<string>run</string>" in xml
    # launchd 不展开 ~，确保没有波浪号路径漏进 plist
    assert "~/" not in xml
    # 动态解析的 node 目录并入 PATH
    assert "/Users/x/.nvm/versions/node/v20/bin" in xml
    assert "com.larklistener" in xml
    # 日志路径指向 LISTENER_HOME/logs
    assert "/.lark_listener/logs/stdout.log" in xml
    assert "/.lark_listener/logs/stderr.log" in xml
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd /Users/altair/Documents/Projects/LarkListener && python3 -m pytest tests/test_service.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'lark_listener.service'`）

- [ ] **Step 3: 实现 service.py（常量 + 纯函数）**

创建 `lark_listener/service.py`：

```python
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

LISTENER_HOME = Path.home() / ".lark_listener"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.larklistener.plist"
LABEL = "com.larklistener"

# 旧版 PyInstaller 二进制残留路径，迁移时清理。
_OLD_BINARY = LISTENER_HOME / "lark-listener"

_BASE_PATH_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def shim_path() -> str:
    """Absolute path to the installed `lark-listener` executable (pipx shim).

    launchd needs an absolute path (it does not expand ~). Resolve via PATH first
    (honours custom PIPX_BIN_DIR), then this process's own argv0, then the pipx
    default bin dir.
    """
    found = shutil.which("lark-listener")
    if found:
        return found
    argv0 = os.path.realpath(sys.argv[0])
    if os.path.isabs(argv0) and os.path.basename(argv0) == "lark-listener":
        return argv0
    return str(Path.home() / ".local" / "bin" / "lark-listener")


def node_bin_dir() -> Optional[str]:
    """Directory holding the real `node` (e.g. nvm path), or None.

    Written into the plist PATH so launchd can find node/lark-cli even when node
    lives outside the hard-coded common dirs (nvm).
    """
    node = shutil.which("node")
    return os.path.dirname(node) if node else None


def build_plist(program_path: str, extra_path_dirs: list[str]) -> str:
    """Render the launchd plist. `program_path` MUST be absolute."""
    dirs: list[str] = []
    for d in list(extra_path_dirs) + _BASE_PATH_DIRS:
        if d and d not in dirs:
            dirs.append(d)
    path_value = ":".join(dirs)
    logs = LISTENER_HOME / "logs"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{program_path}</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{LISTENER_HOME}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>{logs}/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{logs}/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path_value}</string>
    </dict>
</dict>
</plist>
"""
```

- [ ] **Step 4: 运行纯函数测试，确认通过**

Run: `python3 -m pytest tests/test_service.py -v`
Expected: PASS

- [ ] **Step 5: 追加管理命令实现（非 TDD，末尾人工验证）**

在 `service.py` 末尾追加：

```python
def _is_running() -> bool:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        return LABEL in out.stdout
    except Exception:
        return False


def stop_service() -> None:
    """Stop via launchctl unload (clean SIGTERM), then pkill fallback."""
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        time.sleep(1)
    # pipx shim / python 进程的 cmdline 都含 "lark-listener run"
    subprocess.run(["pkill", "-f", "lark-listener run"], capture_output=True)
    subprocess.run(["pkill", "-f", "lark-cli event.*--as bot"], capture_output=True)


def cmd_start() -> None:
    if not PLIST_PATH.exists():
        print("❌ 未安装，请先运行: lark-listener setup")
        return
    if _is_running():
        print("正在重启...")
        stop_service()
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True)
    time.sleep(3)
    if _is_running():
        print("✓ 服务已启动")
    else:
        print(f"❌ 启动失败，请查看日志:\n  cat {LISTENER_HOME}/logs/stderr.log")


def cmd_stop() -> None:
    stop_service()
    print("✓ 服务已停止")


def cmd_restart() -> None:
    stop_service()
    cmd_start()


def cmd_status() -> None:
    installed = PLIST_PATH.exists()
    if not installed:
        print("◇ 未安装")
    elif _is_running():
        print("● 服务运行中")
    else:
        print("○ 服务已安装，未运行")


def cmd_config() -> None:
    cfg = LISTENER_HOME / "config.yaml"
    if not cfg.exists():
        print("❌ 配置文件不存在，请先运行: lark-listener setup")
        return
    subprocess.run(["open", "-t", str(cfg)])
    print("✓ 已打开配置文件（修改后下次轮询自动生效）")


def cmd_uninstall() -> None:
    print(f"⚠️  即将删除服务、launchd 配置与 {LISTENER_HOME}（含配置、日志）")
    confirm = input("确认卸载？(y/N) ").strip().lower()
    if confirm != "y":
        print("已取消")
        return
    stop_service()
    PLIST_PATH.unlink(missing_ok=True)
    shutil.rmtree(LISTENER_HOME, ignore_errors=True)
    print("✓ 已卸载。最后一步请手动移除本程序：\n  pipx uninstall lark-listener")
```

- [ ] **Step 6: 回填 Task 3 的 start 分发测试**

若 Task 3 时跳过了 `test_main_start_subcommand_dispatches_to_service`，现在加回 `tests/test_main.py`（见 Task 3 Step 1 第三个测试），并运行：

Run: `python3 -m pytest tests/test_main.py::test_main_start_subcommand_dispatches_to_service tests/test_service.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add lark_listener/service.py tests/test_service.py tests/test_main.py
git commit -m "feat: add service module (launchd plist + start/stop/status/uninstall)"
```

---

## Task 5: 新增 setup_wizard.py（Python 交互向导）

把旧 `LarkListener.command` 的 `_install` 逻辑移植为 Python；纯函数（配置 dict 构造、写文件）走 TDD，交互/网络部分末尾人工验证。

**Files:**
- Create: `lark_listener/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: 写配置构造/写入的失败测试**

创建 `tests/test_setup_wizard.py`：

```python
import yaml
from lark_listener import setup_wizard


def test_build_config_dict_shape():
    cfg = setup_wizard.build_config_dict(
        poll_interval=600, appid="cli_x", keywords=["部署", "故障"],
        ai_provider="openai", ai_model="gpt-4o", ai_key="sk-1", ai_base_url="",
        user_id="ou_me", bot_chat_id="oc_bot",
    )
    assert cfg["poll_interval"] == 600
    assert cfg["lark_cli_appid"] == "cli_x"
    assert cfg["keywords"] == ["部署", "故障"]
    assert cfg["ai"] == {"provider": "openai", "model": "gpt-4o", "api_key": "sk-1", "base_url": ""}
    assert cfg["notify"] == {"user_id": "ou_me", "bot_chat_id": "oc_bot"}
    # bot 自身会话默认排除，避免汇总自己的推送
    assert cfg["exclude_chat_ids"] == ["oc_bot"]


def test_write_config_file_roundtrips(tmp_path):
    cfg = setup_wizard.build_config_dict(
        poll_interval=300, appid="cli_y", keywords=[],
        ai_provider="claude", ai_model="claude-sonnet-4-6", ai_key="", ai_base_url="http://x",
        user_id="ou_a", bot_chat_id="oc_b",
    )
    path = tmp_path / "config.yaml"
    setup_wizard.write_config_file(str(path), cfg)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded == cfg
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd /Users/altair/Documents/Projects/LarkListener && python3 -m pytest tests/test_setup_wizard.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'lark_listener.setup_wizard'`）

- [ ] **Step 3: 实现纯函数**

创建 `lark_listener/setup_wizard.py`：

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML

from lark_listener import service


def build_config_dict(
    *, poll_interval: int, appid: str, keywords: list[str],
    ai_provider: str, ai_model: str, ai_key: str, ai_base_url: str,
    user_id: str, bot_chat_id: str,
) -> dict:
    """Assemble the config mapping. Building a dict (not a heredoc) avoids the
    YAML indentation/escaping bugs the old bash wizard was prone to."""
    return {
        "poll_interval": poll_interval,
        "lark_cli_appid": appid,
        "include_at_all": True,
        "context_messages": 20,
        "keywords": list(keywords),
        "ai": {
            "provider": ai_provider,
            "model": ai_model,
            "api_key": ai_key,
            "base_url": ai_base_url,
        },
        "exclude_chat_ids": [bot_chat_id],
        "notify": {"user_id": user_id, "bot_chat_id": bot_chat_id},
    }


def write_config_file(path: str, cfg: dict) -> None:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)
```

- [ ] **Step 4: 运行纯函数测试，确认通过**

Run: `python3 -m pytest tests/test_setup_wizard.py -v`
Expected: PASS

- [ ] **Step 5: 追加交互向导 `cmd_setup`（非 TDD，末尾人工验证）**

在 `setup_wizard.py` 末尾追加。逻辑对应旧 `LarkListener.command:_install` 的分支：

```python
def _run_lark(args: list[str], appid: str) -> str:
    """Run a lark-cli command pinned to appid, return stdout (best-effort)."""
    from lark_listener.binaries import lark_cli
    try:
        out = subprocess.run(lark_cli(*args, "--profile", appid),
                             capture_output=True, text=True, timeout=30)
        return out.stdout
    except Exception:
        return ""


def _detect_active_appid() -> tuple[str, str, str]:
    """Return (appId, user, brand) of the active lark-cli profile, or ('','','')."""
    from lark_listener.binaries import lark_cli
    try:
        out = subprocess.run(lark_cli("profile", "list"), capture_output=True, text=True, timeout=10)
        data = json.loads(out.stdout)
        p = next((x for x in data if x.get("active")), None)
        if p:
            return p.get("appId", ""), p.get("user", ""), p.get("brand", "")
    except Exception:
        pass
    return "", "", ""


def cmd_setup() -> None:
    from lark_listener.binaries import lark_cli, resolve_executable

    # 0) 前置：lark-cli 必须在场（npm 装的，本工具的前提）
    if not resolve_executable("lark-cli") or not Path(resolve_executable("lark-cli")).is_file():
        print("❌ 未检测到 lark-cli。请先安装并登录：")
        print("   npm install -g @larksuite/cli && lark-cli config init")
        return

    service.LISTENER_HOME.mkdir(parents=True, exist_ok=True)
    (service.LISTENER_HOME / "logs").mkdir(parents=True, exist_ok=True)  # launchd 不建中间目录

    # 1) 选择承载服务的 bot（appId）
    appid, user, brand = _detect_active_appid()
    chosen = ""
    if appid:
        print(f"检测到当前 active bot：{appid}（{brand} / 登录用户: {user}）")
        if input("使用它？(Y/n) ").strip().lower() in ("", "y"):
            chosen = appid
    while not chosen:
        chosen = input("请输入承载服务的 lark-cli appId（cli_xxx）: ").strip()

    cfg_path = service.LISTENER_HOME / "config.yaml"
    if not cfg_path.exists():
        # 2) 配置向导
        poll = input("轮询间隔（秒，默认 300）: ").strip() or "300"
        kw_raw = input("关注的关键词（逗号分隔，可空）: ").strip()
        keywords = [k.strip() for k in kw_raw.split(",") if k.strip()] if kw_raw else []
        print("AI 后端：1) openai  2) claude  3) ollama")
        choice = input("选择（默认 1）: ").strip() or "1"
        provider = {"2": "claude", "3": "ollama"}.get(choice, "openai")
        model = input("模型名称（如 gpt-4o / claude-sonnet-4-6 / qwen2.5:7b）: ").strip() or "gpt-4o"
        api_key = input("API Key（ollama 可空）: ").strip()
        base_url = input("API Base URL（留空用默认）: ").strip()

        # 自动取 user_id
        uid_out = _run_lark(["contact", "+get-user", "--jq", ".data.user.open_id"], chosen)
        user_id = uid_out.strip().strip('"')
        if not user_id or user_id == "null":
            user_id = input("无法自动获取，请手动输入 user_id (ou_xxx): ").strip()

        # 发测试消息取 bot_chat_id
        send_out = _run_lark(["im", "+messages-send", "--user-id", user_id,
                              "--text", "LarkListener 安装测试 ✅", "--as", "bot"], chosen)
        bot_chat_id = ""
        try:
            bot_chat_id = json.loads(send_out).get("data", {}).get("chat_id", "")
        except Exception:
            pass
        if not bot_chat_id:
            bot_chat_id = input("无法自动获取，请手动输入 bot_chat_id (oc_xxx): ").strip()

        cfg = build_config_dict(
            poll_interval=int(poll), appid=chosen, keywords=keywords,
            ai_provider=provider, ai_model=model, ai_key=api_key, ai_base_url=base_url,
            user_id=user_id, bot_chat_id=bot_chat_id,
        )
        write_config_file(str(cfg_path), cfg)
        print("✓ 配置文件已生成")
    else:
        # 已有配置：仅同步 appid
        yaml = YAML()
        data = yaml.load(cfg_path.read_text(encoding="utf-8")) or {}
        data["lark_cli_appid"] = chosen
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
        print(f"✓ 已保留配置，lark_cli_appid = {chosen}")

    # 3) 老用户迁移：停旧服务、删旧二进制
    service.stop_service()
    service._OLD_BINARY.unlink(missing_ok=True)

    # 4) 写 plist（绝对路径 + 动态 node 目录）
    extra = [d for d in [service.node_bin_dir()] if d]
    service.PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    service.PLIST_PATH.write_text(service.build_plist(service.shim_path(), extra), encoding="utf-8")
    print("✓ 已写入 launchd 配置")

    # 5) 引导授权
    scope = "search:message"
    name = _run_lark(["contact", "+get-user", "--jq", ".data.user.name"], chosen).strip()
    if not name:
        print(f"\n该 bot 尚未登录 user 身份，需授权 {scope}：")
        if input("现在发起授权登录？(Y/n) ").strip().lower() in ("", "y"):
            from lark_listener.binaries import lark_cli
            subprocess.run(lark_cli("auth", "login", "--profile", chosen, "--scope", scope))
    print("\n=== 安装完成 ===\n运行 `lark-listener start` 启动服务，给 Bot 发「汇总」可立即触发。")
```

- [ ] **Step 6: 运行全部单测，确认无回归**

Run: `python3 -m pytest tests/ -v`
Expected: PASS（全部）

- [ ] **Step 7: 提交**

```bash
git add lark_listener/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: add Python setup wizard (replaces bash .command installer)"
```

---

## Task 6: 新增 install.sh（curl|bash 引导）

非交互安装脚本：检测 python/git、引导 pipx、检测 lark-cli、`pipx install`，结尾提示手动跑 setup。**不在管道里调 setup**（管道无真 tty）。

**Files:**
- Create: `install.sh`

- [ ] **Step 1: 写 install.sh**

创建 `install.sh`：

```bash
#!/bin/bash
set -euo pipefail

REPO="https://github.com/AltairEven/LarkListener.git"

echo "=== LarkListener 安装 ==="

# 1) python3 ≥ 3.9
if ! command -v python3 >/dev/null 2>&1; then
    echo "未检测到 python3，触发 Apple 命令行工具安装（系统级、已公证）..."
    xcode-select --install || true
    echo "请在弹窗完成 Command Line Tools 安装后重跑本脚本。"
    exit 1
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)'; then
    echo "❌ python3 版本过低（$PYV），需 ≥ 3.9。"
    exit 1
fi
echo "✓ python3 $PYV"

# 2) git（pipx install git+ 需要；macOS 随 CLT 提供）
if ! command -v git >/dev/null 2>&1; then
    echo "❌ 未检测到 git，请先安装 Xcode Command Line Tools: xcode-select --install"
    exit 1
fi
echo "✓ git"

# 3) 引导 pipx（应对 PEP 668）
if command -v brew >/dev/null 2>&1; then
    brew list pipx >/dev/null 2>&1 || brew install pipx
else
    if ! python3 -m pip install --user pipx 2>/tmp/ll_pipx_err; then
        if grep -q 'externally-managed-environment' /tmp/ll_pipx_err; then
            python3 -m pip install --user --break-system-packages pipx
        else
            cat /tmp/ll_pipx_err; exit 1
        fi
    fi
fi

# 解析 pipx 调用方式（脚本未必在当前 PATH）
if command -v pipx >/dev/null 2>&1; then
    PIPX="pipx"
elif python3 -m pipx --version >/dev/null 2>&1; then
    PIPX="python3 -m pipx"
else
    PIPX="$HOME/.local/bin/pipx"
fi
$PIPX ensurepath >/dev/null 2>&1 || true
echo "✓ pipx 就绪"

# 4) 检测 lark-cli（不自动安装）
if ! command -v lark-cli >/dev/null 2>&1; then
    echo ""
    echo "⚠️  未检测到 lark-cli（本工具的前提）。请先安装并登录后重跑本脚本："
    echo "   npm install -g @larksuite/cli"
    echo "   lark-cli config init"
    echo "   lark-cli auth login --scope search:message"
    exit 1
fi
echo "✓ lark-cli"

# 5) 安装本工具
$PIPX install --force "git+$REPO"

# 6) 结尾：提示手动跑 setup（用绝对路径，不依赖 PATH 刷新）
echo ""
echo "✅ 安装完成。现在运行："
echo "   ~/.local/bin/lark-listener setup"
echo "（新开终端后可直接用 lark-listener setup）"
```

- [ ] **Step 2: 语法检查 + 可执行位**

Run:
```bash
cd /Users/altair/Documents/Projects/LarkListener && bash -n install.sh && chmod +x install.sh && echo "syntax OK"
```
Expected: `syntax OK`

- [ ] **Step 3: 提交**

```bash
git add install.sh
git commit -m "feat: add curl|bash install.sh (pipx bootstrap, no Gatekeeper)"
```

---

## Task 7: 清理旧分发物 + 重写 README

删除 PyInstaller 时代的文件，README 改为 pipx 安装流程。

**Files:**
- Delete: `build.sh`, `run_service.py`, `LarkListener.command`, `requirements.txt`, `build/`
- Modify: `README.md`

- [ ] **Step 1: 删除旧文件**

```bash
cd /Users/altair/Documents/Projects/LarkListener
git rm build.sh run_service.py LarkListener.command requirements.txt
rm -rf build/
```

- [ ] **Step 2: 确认无代码再引用被删符号**

Run:
```bash
grep -rn "run_service\|PyInstaller\|LarkListener.command\|requirements.txt" lark_listener/ tests/ install.sh README.md || echo "OK: no dangling refs"
```
Expected: `OK: no dangling refs`（README 将在下一步重写，若此处命中 README 属正常，下一步覆盖）

- [ ] **Step 3: 重写 README.md**

把 `README.md` 整体替换为：

```markdown
# LarkListener

定时从飞书获取未读消息，AI 分析后通过 Bot 私聊推送汇总 + macOS 桌面通知。本地后台服务（launchd），由 pipx 安装，无需签名、不触发 macOS 安全弹窗。

## 前置

- 已安装并登录 `lark-cli`（本工具的前提）：
  ```bash
  npm install -g @larksuite/cli
  lark-cli config init
  lark-cli auth login --scope search:message
  ```

## 安装

```bash
curl -fsSL https://raw.githubusercontent.com/AltairEven/LarkListener/main/install.sh | bash
```

安装完成后按提示运行向导（首次需在普通终端里跑，不能在管道里）：

```bash
~/.local/bin/lark-listener setup     # 新开终端后可直接用 lark-listener setup
lark-listener start
```

## 管理命令

| 命令 | 作用 |
|---|---|
| `lark-listener setup` | 交互安装向导（选 bot、配置、写 launchd、引导授权） |
| `lark-listener start` / `stop` / `restart` / `status` | 启停 / 查看服务 |
| `lark-listener config` | 打开配置文件 |
| `lark-listener uninstall` | 停服务、删配置与 launchd（最后手动 `pipx uninstall lark-listener`） |

## 使用

给 Bot 发 **「汇总」/「总结」/「summary」** 可立即触发一次；**「汇总最近2小时」** 可指定时间范围。
发 **「当前配置」/「帮助」** 可查看或自然语言修改配置（仅本人；改动需回复「确认」生效，下次轮询自动应用）。

## 升级

```bash
pipx install --force git+https://github.com/AltairEven/LarkListener.git
lark-listener restart
```
（更新代码后正在跑的守护进程仍是旧代码，必须 `restart` 才生效。）

## 日志

```bash
tail -f ~/.lark_listener/logs/stderr.log
```
```

- [ ] **Step 4: 全量测试**

Run: `cd /Users/altair/Documents/Projects/LarkListener && python3 -m pytest tests/ -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "chore: remove PyInstaller distribution, rewrite README for pipx flow"
```

---

## Task 8: 端到端人工验证（无法自动化的部分）

install.sh、交互 setup、launchd 启动、osascript 通知都依赖真实环境，需手动核验。

- [ ] **Step 1: 推送分支并确认仓库 public**

```bash
git push origin main
```
浏览器确认 `https://github.com/AltairEven/LarkListener` 可匿名访问、`raw.githubusercontent.com/.../main/install.sh` 返回脚本内容。

- [ ] **Step 2: 干净安装（建议在另一台机或新用户下）**

Run: `curl -fsSL https://raw.githubusercontent.com/AltairEven/LarkListener/main/install.sh | bash`
Expected: 全程无 Gatekeeper「无法验证开发者」弹窗；结尾打印 `~/.local/bin/lark-listener setup`。

- [ ] **Step 3: 跑 setup 并启动**

```bash
~/.local/bin/lark-listener setup
lark-listener start
lark-listener status
```
Expected: `status` 显示「● 服务运行中」；Bot 收到「✅ LarkListener 已启动」私聊。

- [ ] **Step 4: 核验 plist 是绝对路径**

Run: `plutil -p ~/Library/LaunchAgents/com.larklistener.plist | grep -A3 ProgramArguments`
Expected: 第一个参数是 `/Users/<you>/.local/bin/lark-listener`（**无** `~`），第二个是 `run`；`PATH` 含真实 node 目录。

- [ ] **Step 5: 核验触发与通知**

给 Bot 发「汇总」。
Expected: 收到汇总私聊；macOS 右上角出现「LarkListener / 有新消息汇总」通知（无 terminal-notifier 时由 osascript 弹出）。

- [ ] **Step 6: 核验升级与卸载**

```bash
pipx install --force git+https://github.com/AltairEven/LarkListener.git && lark-listener restart && lark-listener status
lark-listener uninstall   # 交互确认
```
Expected: restart 后仍运行；uninstall 后 `~/.lark_listener` 与 plist 均删除，并提示 `pipx uninstall lark-listener`。

---

## Self-Review 记录

- **Spec 覆盖**：build-system/packaging(T1)、osascript 通知(T2)、main 拆分+分发(T3)、plist 绝对路径+node 动态解析+管理命令(T4)、Python setup 向导+老用户迁移+logs 目录(T5)、install.sh+pipx 引导+PEP668(T6)、删除旧物+README(T7)、public 仓库+端到端(T8)。均有对应任务。
- **占位符**：无 TBD/TODO；交互与安装脚本部分给出完整代码 + 人工验证步骤（这些天然不可单测）。
- **类型一致**：`shim_path()` / `node_bin_dir()` / `build_plist(program_path, extra_path_dirs)` / `stop_service()` / `build_config_dict(**kwargs)` / `write_config_file(path, cfg)` 在定义与调用处签名一致；`cmd_*` 命名与 main 分发的 `getattr(service, f"cmd_{...}")` 一致。
- **已知取舍**：fresh config 用 ruamel dump，不保留旧 bash 模板里的中文注释（后续 bot 改配置时 `config_editor` 仍保留注释，影响仅限首次生成的文件，可接受）。
```
