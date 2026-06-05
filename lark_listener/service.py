from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

LISTENER_HOME = Path.home() / ".lark_listener"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.larklistener.plist"
LABEL = "com.larklistener"

# 隔离虚拟环境（标准库 venv，install.sh 创建）与其内的可执行入口。
VENV_DIR = LISTENER_HOME / "venv"
# 短命令软链：~/.local/bin/lark-listener → venv 入口。
SHIM_LINK = Path.home() / ".local" / "bin" / "lark-listener"

# 旧版 PyInstaller 二进制残留路径，迁移时清理。
_OLD_BINARY = LISTENER_HOME / "lark-listener"

_BASE_PATH_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]


def shim_path() -> str:
    """venv 内 `lark-listener` 入口的绝对路径。

    launchd 需要绝对路径（不展开 ~）。venv 路线下这个路径是确定的（我们自己建的
    venv），故直接拼出即可，无需 PATH/argv0 兜底。plist 指向 venv 内真实入口而非
    ~/.local/bin 的软链——软链被删时服务仍能启动。
    """
    return str(VENV_DIR / "bin" / "lark-listener")


def ensure_shim_link() -> None:
    """幂等建立 ~/.local/bin/lark-listener → venv 入口 的软链。

    install.sh 已建一次；setup 再调用一次，兼容本地 `pip install` 直装、未走
    install.sh 的情况。仅当 venv 入口存在时才建。
    """
    target = Path(shim_path())
    if not target.is_file():
        return
    SHIM_LINK.parent.mkdir(parents=True, exist_ok=True)
    if SHIM_LINK.is_symlink() or SHIM_LINK.exists():
        SHIM_LINK.unlink()
    SHIM_LINK.symlink_to(target)


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
    # venv 入口 / python 进程的 cmdline 都含 "lark-listener run"
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
    print(f"⚠️  即将删除服务、launchd 配置、短命令软链与 {LISTENER_HOME}（含 venv、配置、日志）")
    confirm = input("确认卸载？(y/N) ").strip().lower()
    if confirm != "y":
        print("已取消")
        return
    stop_service()
    PLIST_PATH.unlink(missing_ok=True)
    if SHIM_LINK.is_symlink() or SHIM_LINK.exists():
        SHIM_LINK.unlink()
    shutil.rmtree(LISTENER_HOME, ignore_errors=True)  # 含 venv，一步删干净
    print("✓ 已卸载完成。")
