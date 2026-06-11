from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML

from lark_listener import service


def ai_packages_for(provider: str) -> list[str]:
    """选定 AI 后端需要 pip 安装的包（唯一事实源在 providers.py）。

    ollama 用标准库 urllib 直连，无需任何 SDK，返回空；未知后端也返回空
    （交由运行时报错，不擅自装包）。"""
    from lark_listener.providers import pip_packages_for
    return pip_packages_for(provider)


def _venv_python() -> str:
    """守护进程实际运行所在的 venv python，不存在则回退当前解释器。

    务必装进 venv：plist 指向 venv/bin/lark-listener，守护 `run` 在 venv 内 import
    SDK。若 setup 经「本地 pip 直装」启动，sys.executable 可能是系统/CLT python，
    把 SDK 装到 venv 之外会让运行时 import 不到（且 PEP 668 系统上 pip 还会直接失败）。
    """
    py = service.VENV_DIR / "bin" / "python"
    return str(py) if py.exists() else sys.executable


def _pip_install_ai(provider: str) -> None:
    """按 AI 后端把对应 SDK 装进守护进程所在的 venv（见 _venv_python）。

    只是给 venv 增装一个依赖包（守护进程 `run` 之后才 import 它），不是「进程重装
    自己」，无自我覆盖问题。幂等：已装则 pip 立即跳过。失败仅警告、不中断 setup。
    """
    pkgs = ai_packages_for(provider)
    if not pkgs:
        print(f"✓ AI 后端 {provider or '(未设置)'}：无需额外依赖")
        return
    print(f"安装 AI 依赖（{provider}）：{' '.join(pkgs)} ...")
    try:
        result = subprocess.run([_venv_python(), "-m", "pip", "install", *pkgs])
        if result.returncode == 0:
            print("✓ AI 依赖已安装")
        else:
            print(f"⚠️  AI 依赖安装失败（退出码 {result.returncode}）。可稍后手动安装：")
            print(f"   ~/.lark_listener/venv/bin/pip install {' '.join(pkgs)}")
    except Exception as e:
        print(f"⚠️  AI 依赖安装出错（{e}）。可稍后手动安装。")


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


def _parse_poll(raw: str) -> int:
    """轮询间隔输入 → 非负 int。空=默认 300；非数字回退 300（向导绝不因一次
    手滑裸 ValueError 终止）；负数按 0（关闭自动轮询）。"""
    raw = (raw or "").strip()
    if not raw:
        return 300
    try:
        n = int(raw)
    except ValueError:
        print("⚠️ 轮询间隔需为整数秒，已按默认 300 处理")
        return 300
    if n < 0:
        print("⚠️ 轮询间隔不能为负，已按 0（关闭自动轮询）处理")
        return 0
    return n


def write_config_file(path: str, cfg: dict) -> None:
    """新建/覆写配置：复用 dump_roundtrip（tmp 0600 创建 + replace 原子写，
    密钥不落 0644 窗口、断电不留半截文件），并强制收紧到 0600。"""
    import os
    from lark_listener.config_editor import dump_roundtrip
    dump_roundtrip(path, cfg)
    os.chmod(path, 0o600)


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


def cmd_setup() -> int:
    """交互安装向导。返回退出码（0 成功 / 1 前置缺失或用户取消），与其它
    子命令的退出码约定一致，便于脚本/agent 判断结果。"""
    try:
        return _cmd_setup_inner()
    except (EOFError, KeyboardInterrupt):
        # stdin 关闭（管道/非交互误跑）或 Ctrl-C：干净取消，不裸 traceback。
        print("\n已取消 setup")
        return 1


def _cmd_setup_inner() -> int:
    from lark_listener.binaries import lark_cli, resolve_executable

    # 0) 前置：lark-cli 必须在场（npm 装的，本工具的前提）
    if not Path(resolve_executable("lark-cli")).is_file():
        print("❌ 未检测到 lark-cli。请先安装并登录：")
        print("   npm install -g @larksuite/cli && lark-cli config init")
        return 1

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
        poll = _parse_poll(input("轮询间隔（秒，默认 300；填 0 关闭自动轮询、仅按需汇总）: "))
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
            poll_interval=poll, appid=chosen, keywords=keywords,
            ai_provider=provider, ai_model=model, ai_key=api_key, ai_base_url=base_url,
            user_id=user_id, bot_chat_id=bot_chat_id,
        )
        write_config_file(str(cfg_path), cfg)
        print("✓ 配置文件已生成")
    else:
        # 已有配置：仅同步 appid。经 dump_roundtrip 原子写（tmp+replace，保持
        # 文件权限），与其它写回路径一致——直接覆写遇断电会留半截 config。
        from lark_listener.config_editor import dump_roundtrip
        yaml = YAML()
        data = yaml.load(cfg_path.read_text(encoding="utf-8")) or {}
        data["lark_cli_appid"] = chosen
        dump_roundtrip(cfg_path, data)
        print(f"✓ 已保留配置，lark_cli_appid = {chosen}")

    # 2.9) 按所选 AI 后端安装对应 SDK（ollama 无需）。从最终配置读 provider，
    #      兼容「新建配置」与「已有配置」两条分支。
    final_cfg = YAML().load(cfg_path.read_text(encoding="utf-8")) or {}
    _pip_install_ai((final_cfg.get("ai") or {}).get("provider", ""))

    # 3) 老用户迁移：停旧服务、删旧二进制
    service.stop_service()
    service._OLD_BINARY.unlink(missing_ok=True)

    # 3.5) 幂等确保短命令软链存在（兼容本地 pip 直装、未走 install.sh 的情况）
    service.ensure_shim_link()

    # 4) 写 plist（绝对路径 + 动态 node 目录）
    extra = [d for d in [service.node_bin_dir()] if d]
    service.PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    service.PLIST_PATH.write_text(service.build_plist(service.shim_path(), extra), encoding="utf-8")
    print("✓ 已写入 launchd 配置")

    # 5) 引导授权：两步检查（对齐旧 bash 向导）——先看是否登录 user 身份，
    #    再用一次窄区间 messages-search 探测 search:message scope 是否真的授权。
    #    登录了但缺 scope 时服务仍拉不到消息，故两者任一不满足都需重新授权。
    from lark_listener.doctor import SEARCH_SCOPE, probe_messages_search
    scope = SEARCH_SCOPE
    name = _run_lark(["contact", "+get-user", "--jq", ".data.user.name"], chosen).strip()
    needs_login = False
    if not name:
        print("\n该 bot 尚未登录 user 身份。")
        needs_login = True
    else:
        print(f"✓ 已登录: {name}")
        # 窄区间真探（与 doctor --deep 共用同一实现，json 判 ok 的理由见彼处）。
        if not probe_messages_search(chosen):
            print(f"○ 缺少 {scope} 权限（或登录已过期）。")
            needs_login = True
        else:
            print(f"✓ {scope} 权限正常")
    if needs_login:
        print(f"需为该 bot 授权 {scope}（浏览器打开链接完成）：")
        if input("现在发起授权登录？(Y/n) ").strip().lower() in ("", "y"):
            subprocess.run(lark_cli("auth", "login", "--profile", chosen, "--scope", scope))
        else:
            print(f"已跳过。稍后可手动运行：lark-cli auth login --profile {chosen} --scope \"{scope}\"")
    print("\n=== 安装完成 ===\n运行 `lark-listener start` 启动服务，给 Bot 发「汇总」可立即触发。")
    return 0
