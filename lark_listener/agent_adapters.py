from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

SKILL_NAME = "lark-listener"


class ClaudeCodeAdapter:
    name = "claude-code"

    def __init__(self, skills_dir: Path | None = None):
        self.skills_dir = skills_dir or (Path.home() / ".claude" / "skills")

    def detect(self) -> bool:
        # ~/.claude 存在即认为用户在用 Claude Code（非侵入前提）。无更精确的探测方式；
        # 误判时只是多写一个 SKILL.md，不影响任何工具。
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
    # 不检查 detect()：即使 Claude Code 已卸载也应清理残留 skill
    adapters = adapters if adapters is not None else _default_adapters()
    for ad in adapters:
        try:
            ad.uninstall()
        except Exception:  # noqa: BLE001 — best-effort 清理，不阻断
            pass
    return 0
