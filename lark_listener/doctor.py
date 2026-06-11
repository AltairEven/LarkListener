from __future__ import annotations

import importlib.util
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from lark_listener import config as config_mod
from lark_listener import service

from lark_listener.common import TZ

# 服务必需的 lark-cli 用户授权 scope（doctor 修复指引与 setup 引导共用）。
SEARCH_SCOPE = "search:message"


def probe_messages_search(appid: str, run=None) -> bool:
    """窄区间 messages-search 真探 search:message 授权是否可用。

    doctor --deep 与 setup 末段共用的唯一实现。token 过期/缺 scope 时
    profile list 照样成功，只有真打一次搜索才能发现。判定走 json 解析——
    字符串匹配（`'"ok": true' in out`）依赖 lark-cli 的缩进格式，
    输出转 compact 即全员误报缺权限。"""
    import json as _json
    import subprocess
    from lark_listener.binaries import resolve_executable
    run = run or subprocess.run
    try:
        probe = run([resolve_executable("lark-cli"), "im", "+messages-search",
                     "--chat-type", "p2p",
                     "--start", "2020-01-01T00:00:00+08:00",
                     "--end", "2020-01-01T00:01:00+08:00",
                     "--format", "json", "--profile", appid],
                    capture_output=True, text=True, timeout=30)
        return bool(_json.loads(probe.stdout).get("ok"))
    except Exception:  # noqa: BLE001
        return False


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


def check_lark_cli(run=None, appid: str = "", deep: bool = False) -> Check:
    """lark-cli 可用性检查。

    浅检：profile list 可执行 + 配置的 appid 在 profile 列表中（服务被钉在该
    profile，仅看 active profile 会查错对象）。注意 profile list 是本地操作，
    token 过期时照样成功——授权时效只有 --deep 的窄区间 messages-search 真探
    才能发现（这恰是「收不到汇总」最常见的根因）。"""
    import json as _json
    import subprocess
    from lark_listener.binaries import resolve_executable
    run = run or subprocess.run
    exe = resolve_executable("lark-cli")
    # resolve_executable returns the bare name "lark-cli" when it can't resolve it
    if exe == "lark-cli":
        return Check("lark_cli", "fail", "未找到 lark-cli",
                     fix="npm install -g @larksuite/cli")
    login_fix = (f"lark-cli auth login --profile {appid} --scope {SEARCH_SCOPE}"
                 if appid else f"lark-cli auth login --scope {SEARCH_SCOPE}")
    try:
        r = run([exe, "profile", "list"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return Check("lark_cli", "warn", "lark-cli 执行异常（profile list 失败）",
                         fix="lark-cli config init；仍失败则重装：npm install -g @larksuite/cli")
        if appid:
            try:
                profiles = _json.loads(r.stdout)
                # 形态非预期时一律不据此判（原则：解析不出 ≠ appid 不存在）：
                # dict 包裹 / list 元素非 dict（推不出任何 appId）都视为不可判定；
                # 仅当 profiles 是空列表（真没有任何 profile）时按缺失处理。
                ids = None
                if isinstance(profiles, list):
                    ids = {p.get("appId") for p in profiles if isinstance(p, dict)}
                    if profiles and not ids:
                        ids = None
            except Exception:  # noqa: BLE001
                ids = None  # 输出不可解析时不据此误判
            if ids is not None and appid not in ids:
                return Check("lark_cli", "fail",
                             f"配置的 lark_cli_appid={appid} 不在 lark-cli profile 列表中",
                             fix=login_fix)
        if deep and appid:
            if not probe_messages_search(appid, run=run):
                return Check("lark_cli", "fail",
                             "search:message 探测失败（授权过期或缺 scope）", fix=login_fix)
            return Check("lark_cli", "ok", "lark-cli 可用，search:message 已验证")
        if deep and not appid:
            return Check("lark_cli", "ok", "lark-cli 可用（配置缺 appid，已跳过授权真探）")
        return Check("lark_cli", "ok", "lark-cli 可用（浅检不验授权时效，--deep 真探）")
    except Exception as e:  # noqa: BLE001
        return Check("lark_cli", "warn", f"lark-cli 调用失败：{e}", fix=login_fix)


def check_last_poll(status: dict, poll_interval: int, now: Optional[datetime] = None) -> Check:
    now = now or datetime.now(TZ)
    if poll_interval <= 0:
        # 自动轮询已关闭（仅按需汇总），last_poll 不会推进，时效检查无意义。
        return Check("last_poll", "ok", "自动轮询已关闭（poll_interval=0），仅按需汇总")
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
        # 只读尾部 64KB：日志无轮转，长跑数月可达数百 MB，read_text 全量
        # 载入既慢又吃内存；窗口外的陈年 traceback 也不应永久 warn。
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            tail = f.read().decode("utf-8", errors="replace").splitlines()[-200:]
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
    from lark_listener.providers import sdk_import_for
    sdk = sdk_import_for(provider)
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
    """真实最小请求探测，返回 (ok, detail)。best-effort，异常即视为失败。
    分发与各后端实现统一在 providers.py（探活语义独立于 complete）。"""
    from lark_listener import providers
    try:
        p = providers.get(provider)
    except ValueError:
        return False, f"未知 provider {provider}"
    try:
        return p.deep_probe(model=model, api_key=api_key, base_url=base_url)
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _probe_unmuted_chats(appid: str, run=None):
    """chat-list --exclude-muted 单页真探：返回未免打扰群 id 集合，失败 None。
    doctor 只需判定可用性与绑定群状态，单页 100 足够；分页全量是运行时
    ChatRegistry 的职责。"""
    import json as _json
    import subprocess
    from lark_listener.binaries import resolve_executable
    run = run or subprocess.run
    cmd = [resolve_executable("lark-cli"), "im", "+chat-list", "--exclude-muted",
           "--page-size", "100", "--format", "json"]
    if appid:
        cmd += ["--profile", appid]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=30)
        data = _json.loads(r.stdout)
        if r.returncode != 0 or not data.get("ok"):
            return None
        return {c.get("chat_id") for c in (data.get("data") or {}).get("chats") or []
                if isinstance(c, dict) and c.get("chat_id")}
    except Exception:  # noqa: BLE001
        return None


def check_special_focus(config: dict, deep: bool = False, run=None) -> Check:
    """special_focus 配置体检。浅检只看形状（load_config 已钳制，重点是
    提示语义）；--deep 真探未免打扰列表并核对绑定群状态——绑定了关注词的
    群若已免打扰，关注词静默失效，这是用户最易踩的暗坑。"""
    sf = config.get("special_focus") or {}
    if not sf.get("enabled"):
        return Check("special_focus", "ok", "特别关注未启用")
    bound = sf.get("chats") or []
    if not deep:
        return Check("special_focus", "ok",
                     f"特别关注已启用（绑定 {len(bound)} 个群；--deep 可验证免打扰状态）")
    unmuted = _probe_unmuted_chats(config.get("lark_cli_appid", ""), run=run)
    if unmuted is None:
        return Check("special_focus", "fail",
                     "chat-list --exclude-muted 探测失败（特别关注将降级为全勿扰）",
                     fix="lark-cli auth login --profile "
                         f"{config.get('lark_cli_appid', '')} 重新授权后重试")
    muted_bound = [c for c in bound
                   if isinstance(c, dict) and c.get("chat_id") not in unmuted]
    if muted_bound:
        names = "、".join((c.get("name") or c.get("chat_id", "")) for c in muted_bound)
        return Check("special_focus", "warn",
                     f"绑定的群当前处于免打扰，关注关键词不会生效：{names}",
                     fix="在飞书取消这些群的消息免打扰，或从 special_focus.chats 移除")
    return Check("special_focus", "ok",
                 f"特别关注已启用，{len(unmuted)} 个未免打扰群")


def run_doctor(deep: bool = False):
    """跑全部检查，返回 (checks, exit_code)。exit_code: 有 fail=1 否则 0。"""
    status = service.collect_status()
    try:
        config = config_mod.load_config()
    except Exception:
        config = {}
    poll_interval = config.get("poll_interval", 300) if isinstance(config, dict) else 300
    log_path = service.LISTENER_HOME / "logs" / "stderr.log"

    appid = config.get("lark_cli_appid", "") if isinstance(config, dict) else ""
    checks = [
        check_config(),
        check_service(status),
        check_lark_cli(appid=appid or "", deep=deep),
        check_last_poll(status, poll_interval),
        check_recent_errors(log_path),
        check_ai_backend(config if isinstance(config, dict) else {}, deep=deep),
    ]
    try:
        _cfg = config_mod.load_config()
        checks.append(check_special_focus(_cfg, deep=deep))
    except Exception:  # noqa: BLE001 — config 坏时 check_config 已报，无需重复
        pass
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
