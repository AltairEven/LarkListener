"""跨模块共享的常量与路径推导——唯一事实源。

收口此前各写一份的两类知识：
- TZ：飞书场景固定 +08:00，曾在 main/intent/doctor/state 重复定义 4 份。
- listener_home()：数据目录推导（LARK_LISTENER_HOME 覆盖用于 dev 隔离），
  曾在 config/config_cli/service 重复 3 份，state 还漏读 env。
  惰性求值（调用时读 env）；service 保持自己的 import 时冻结语义
  （LISTENER_HOME = listener_home()），测试依赖该差异。
"""
from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))


def listener_home() -> Path:
    """数据目录：$LARK_LISTENER_HOME（dev 隔离）或 ~/.lark_listener。"""
    home = os.environ.get("LARK_LISTENER_HOME")
    return Path(home).expanduser() if home else Path.home() / ".lark_listener"
