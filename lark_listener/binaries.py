from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

# Directories where lark-cli / terminal-notifier commonly live. Searched when
# the executable is not on the inherited PATH, which happens when the service is
# launched from Finder (a .command runs in a bash login shell that never reads
# ~/.zshrc), from launchd, or from any other non-interactive context.
_COMMON_BIN_DIRS = (
    "/opt/homebrew/bin",                       # Apple Silicon Homebrew + npm
    "/usr/local/bin",                          # Intel Homebrew + npm default
    str(Path.home() / ".local" / "bin"),
    "/usr/bin",
    "/bin",
)


def ensure_path() -> None:
    """Prepend common bin dirs to ``PATH`` so child processes can be found.

    Necessary because ``lark-cli`` is a Node script whose ``#!/usr/bin/env node``
    shebang needs ``node`` on PATH at run time. Resolving lark-cli's own absolute
    path is not enough: the child still inherits PATH and would fail to find node
    when launched outside an interactive shell (Finder .command, launchd, etc.).
    """
    current = os.environ.get("PATH", "")
    existing = current.split(os.pathsep) if current else []
    missing = [d for d in _COMMON_BIN_DIRS if d not in existing]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing + existing)


# lark-cli profile (which configured bot/app to act as) the service is pinned
# to. Set once at startup from config's ``lark_cli_appid`` — a lark-cli profile
# is named after its appId, so the appId doubles as the ``--profile`` value.
# Without pinning, lark-cli falls back to its globally-active profile, which is
# fragile once several bots are configured: an interactive ``profile use`` would
# silently redirect the running service. Pinning keeps the service independent.
_lark_profile: Optional[str] = None


def set_lark_profile(profile: Optional[str]) -> None:
    """Pin every subsequent ``lark_cli(...)`` call to this profile (or clear it)."""
    global _lark_profile
    _lark_profile = profile or None


def get_lark_profile() -> Optional[str]:
    """当前钉住的 profile（appId），未设返回 None。"""
    return _lark_profile


def lark_cli(*args: str) -> list[str]:
    """Build a ``lark-cli`` argv, pinned to the configured profile if one is set.

    Centralizes command construction so ``--profile`` is appended uniformly to
    every invocation. ``--profile`` is a global flag and is appended last,
    matching where the existing ``--as`` flags sit.
    """
    cmd = [resolve_executable("lark-cli"), *args]
    if _lark_profile:
        cmd += ["--profile", _lark_profile]
    return cmd


def event_subscriber_pkill_pattern(profile: str) -> str:
    """本实例 event 订阅子进程的 pkill -f（ERE）模式——唯一事实源。

    三要素缺一不可，曾经分写两处时漂移过：subscribe 限定（不误杀同 profile
    的 `event consume`，其它 agent 会话的真实用法）、--as bot、--profile +
    结尾锚定（cli_abc 不得匹配 cli_abc123；appid 经 re.escape 防元字符）。
    """
    return f"lark-cli event.*subscribe.*--as bot.*--profile {re.escape(profile)}( |$)"


# 只缓存「成功解析」的绝对路径，进程内复用。失败不缓存：服务启动早于安装/升级完成
# 时第一次会解析失败，若把裸名永久缓存，软链就位后仍读旧值需重启才生效。
_resolve_cache: dict[str, str] = {}


def resolve_executable(name: str) -> str:
    """Resolve ``name`` to an absolute path, independent of inherited PATH.

    Tries PATH first, then well-known install locations. Falls back to the bare
    name so the original FileNotFoundError still surfaces if it is truly missing.
    A successful resolution is cached; a failed one is not (see _resolve_cache).
    """
    cached = _resolve_cache.get(name)
    if cached is not None:
        return cached
    found = shutil.which(name)
    if not found:
        for directory in _COMMON_BIN_DIRS:
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                found = str(candidate)
                break
    if found:
        _resolve_cache[name] = found
        return found
    return name
