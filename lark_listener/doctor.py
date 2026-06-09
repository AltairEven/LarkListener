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
    # resolve_executable returns the bare name "lark-cli" when it can't resolve it
    if exe == "lark-cli":
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
    except (ValueError, TypeError):
        return Check("last_poll", "warn", f"无法解析 last_poll_time：{raw!r}")
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


def check_ai_backend(config: dict, deep: bool = False) -> Check:
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

    ok, detail = _deep_probe(provider, model, api_key, base_url)
    if ok:
        return Check("ai_backend", "ok", f"{provider}/{model} 真实请求成功")
    return Check("ai_backend", "fail", f"真实请求失败：{detail}",
                 fix="核对 api_key / base_url / 模型名 / ollama 是否在跑")


# 有意简化（非遗漏）：spec 浅检提到「该有 base_url 的有（openai 兼容端点）」。本实现
# 只对 ollama 缺 base_url 给 warn，不对 openai 强制 base_url——openai 官方端点本就
# 不需要，而「是否第三方兼容端点」无法可靠从 model 名推断；端点错误由 --deep 兜底。


def _deep_probe(provider, model, api_key, base_url):
    """真实最小请求探测，返回 (ok, detail)。best-effort，异常即视为失败。"""
    try:
        if provider == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=30)
            client.messages.create(model=model, max_tokens=1,
                                    messages=[{"role": "user", "content": "ping"}])
            return True, ""
        if provider == "openai":
            import openai
            client = openai.OpenAI(api_key=api_key, base_url=base_url or None, timeout=30)
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
