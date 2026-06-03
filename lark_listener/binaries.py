from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

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


@lru_cache(maxsize=None)
def resolve_executable(name: str) -> str:
    """Resolve ``name`` to an absolute path, independent of inherited PATH.

    Tries PATH first, then well-known install locations. Falls back to the bare
    name so the original FileNotFoundError still surfaces if it is truly missing.
    """
    found = shutil.which(name)
    if found:
        return found
    for directory in _COMMON_BIN_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return name
