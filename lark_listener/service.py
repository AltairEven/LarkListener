from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape as _xml_escape

# 数据目录与服务标识支持环境变量覆盖，便于开发时与生产隔离（不设则用默认，
# 行为与原先完全一致）：
#   LARK_LISTENER_HOME  —— 数据目录（config/state/logs/venv），默认 ~/.lark_listener
#   LARK_LISTENER_LABEL —— launchd Label，默认 com.larklistener（plist 文件名随之）
_HOME_ENV = os.environ.get("LARK_LISTENER_HOME")
LISTENER_HOME = Path(_HOME_ENV).expanduser() if _HOME_ENV else Path.home() / ".lark_listener"
LABEL = os.environ.get("LARK_LISTENER_LABEL", "com.larklistener")
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

# 隔离虚拟环境（标准库 venv，install.sh 创建）与其内的可执行入口。
VENV_DIR = LISTENER_HOME / "venv"
# 短命令软链默认位置：~/.local/bin/lark-listener → venv 入口。install.sh 可能改建在
# 其它「可写+在 PATH」的目录，实际位置记录在 SHIM_RECORD，供 uninstall 精确清理。
SHIM_LINK = Path.home() / ".local" / "bin" / "lark-listener"
SHIM_RECORD = LISTENER_HOME / "shim_link"

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

    开发隔离态（设了 LARK_LISTENER_HOME）直接跳过：短命令软链是给生产用户的便利，
    dev 用 venv 内绝对路径调用即可，绝不覆盖生产的 ~/.local/bin/lark-listener。
    """
    if os.environ.get("LARK_LISTENER_HOME"):
        return
    target = Path(shim_path())
    if not target.is_file():
        return
    # install.sh 已建好软链（记录在 SHIM_RECORD）且仍有效 → 不重复建，避免与其选定的
    # 目录冲突（如 install.sh 建在 /opt/homebrew/bin，这里别再往 ~/.local/bin 建一个）。
    try:
        rec = SHIM_RECORD.read_text().strip()
        if rec and Path(rec).is_symlink() and os.path.realpath(rec) == os.path.realpath(target):
            return
    except OSError:
        pass
    # 否则在默认 ~/.local/bin best-effort 建（兼容本地 pip 直装、未走 install.sh）。
    # 不可写（如属主 root）时不要崩掉 setup——服务用 venv 绝对路径仍可运行。
    try:
        SHIM_LINK.parent.mkdir(parents=True, exist_ok=True)
        if SHIM_LINK.is_symlink() or SHIM_LINK.exists():
            SHIM_LINK.unlink()
        SHIM_LINK.symlink_to(target)
        SHIM_RECORD.write_text(str(SHIM_LINK) + "\n")
    except OSError as e:
        print(f"  ⚠️ 未能创建短命令软链 {SHIM_LINK}（{e}）。")
        print(f"     可用绝对路径：{target}")
        print(f"     或修复后重建：sudo chown $(whoami) {SHIM_LINK.parent} && ln -sf {target} {SHIM_LINK}")


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

    # launchd 起的进程不继承当前 shell 的环境变量，必须把它们写进 plist。生产态
    # 只需 PATH；开发隔离态（设了 LARK_LISTENER_HOME）还要透传 HOME/LABEL，否则
    # launchd 跑的 run 服务会回退到生产 ~/.lark_listener 而读不到 dev 配置。
    env_items = {"PATH": path_value}
    if os.environ.get("LARK_LISTENER_HOME"):
        env_items["LARK_LISTENER_HOME"] = str(LISTENER_HOME)
        env_items["LARK_LISTENER_LABEL"] = LABEL
    # 所有插值都经 XML 转义：路径/Label 可能含 & < >（生产路径固定，但 dev 隔离用
    # 任意 LARK_LISTENER_HOME，含特殊字符会生成非法 plist 使 launchctl load 静默失败）。
    env_xml = "\n".join(
        f"        <key>{_xml_escape(k)}</key>\n        <string>{_xml_escape(v)}</string>"
        for k, v in env_items.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_xml_escape(LABEL)}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_xml_escape(program_path)}</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{_xml_escape(str(LISTENER_HOME))}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>{_xml_escape(str(logs))}/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{_xml_escape(str(logs))}/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
{env_xml}
    </dict>
</dict>
</plist>
"""


def _is_running() -> bool:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        # 按行尾的精确 Label 匹配，避免子串误判：`com.larklistener` 是
        # `com.larklistener.dev` 的子串，否则 dev job 加载时生产会误报「运行中」。
        for line in out.stdout.splitlines():
            parts = line.split()
            if parts and parts[-1] == LABEL:
                return True
        return False
    except Exception:
        return False


def stop_service() -> None:
    """Stop via launchctl unload (clean SIGTERM), then pkill fallback."""
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        time.sleep(1)
    # 按本实例 venv 路径精确匹配，避免 dev 测试与生产互相误杀（进程 cmdline 含
    # venv 内入口绝对路径）。
    subprocess.run(["pkill", "-f", f"{VENV_DIR}/bin/lark-listener run"], capture_output=True)
    subprocess.run(["pkill", "-f", "lark-cli event.*--as bot"], capture_output=True)


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


def cmd_stop() -> int:
    stop_service()
    print("✓ 服务已停止")
    return 0


def cmd_restart() -> int:
    stop_service()
    return cmd_start()


def _pids(pattern: str) -> list[str]:
    """匹配 pattern 的进程 PID 列表（best-effort）。"""
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
        return out.stdout.split()
    except Exception:
        return []


def _recorded_shim() -> Optional[str]:
    """install.sh 记录的短命令软链实际位置（若有）。"""
    try:
        return SHIM_RECORD.read_text().strip() or None
    except OSError:
        return None


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
    state_file = paths["state"]
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


_STATUS_EXIT = {"running": 0, "stopped": 3, "not_installed": 4}


def _render_status_text(st: dict) -> None:
    label = {"running": "● 服务运行中", "stopped": "○ 服务已安装，未运行",
             "not_installed": "◇ 未安装"}
    print(label.get(st["state"], st["state"]))
    print("\n进程：")
    print(f"  主进程 (lark-listener run)  : {' '.join(st['main_pids']) or '无'}")
    print(f"  监听子进程 (lark-cli event) : {' '.join(st['event_pids']) or '无'}")
    print("\n文件位置：")
    rows = [("config", "配置     "), ("state", "状态     "), ("logs", "日志     "),
            ("venv", "venv     "), ("launchd", "launchd  "), ("shim", "短命令   ")]
    for key, label_txt in rows:
        info = st["files"].get(key)
        if not info:
            continue
        mark = "✓" if info["exists"] else "—"
        path = info["path"] + ("/" if key == "logs" else "")
        print(f"  {label_txt}{mark} {path}")
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


def cmd_config() -> int:
    cfg = LISTENER_HOME / "config.yaml"
    if not cfg.exists():
        print("❌ 配置文件不存在，请先运行: lark-listener setup")
        return 1
    subprocess.run(["open", "-t", str(cfg)])
    print("✓ 已打开配置文件（修改后下次轮询自动生效）")
    return 0


def cmd_uninstall() -> int:
    print(f"⚠️  即将删除服务、launchd 配置、短命令软链与 {LISTENER_HOME}（含 venv、配置、日志）")
    confirm = input("确认卸载？(y/N) ").strip().lower()
    if confirm != "y":
        print("已取消")
        return 0
    stop_service()
    PLIST_PATH.unlink(missing_ok=True)
    # 删软链：记录的实际位置（install.sh 可能建在 /opt/homebrew/bin 等）+ 默认位置。
    # 必须在 rmtree 之前读取 SHIM_RECORD（它在 LISTENER_HOME 内）。
    links = {SHIM_LINK}
    try:
        rec = SHIM_RECORD.read_text().strip()
        if rec:
            links.add(Path(rec))
    except OSError:
        pass
    for link in links:
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
        except OSError:
            pass
    shutil.rmtree(LISTENER_HOME, ignore_errors=True)  # 含 venv 与 shim_link 记录，一步删干净
    print("✓ 已卸载完成。")
    return 0
