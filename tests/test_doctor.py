from datetime import datetime, timezone, timedelta

from lark_listener import doctor

TZ = timezone(timedelta(hours=8))


def test_check_config_ok(monkeypatch):
    monkeypatch.setattr(doctor.config_mod, "load_config", lambda *a, **k: {"ok": 1})
    c = doctor.check_config()
    assert c.status == "ok"


def test_check_config_fail(monkeypatch):
    def boom(*a, **k):
        raise ValueError("缺少 notify.user_id")
    monkeypatch.setattr(doctor.config_mod, "load_config", boom)
    c = doctor.check_config()
    assert c.status == "fail"
    assert "notify.user_id" in c.detail


def test_check_service_states():
    assert doctor.check_service({"state": "running"}).status == "ok"
    assert doctor.check_service({"state": "stopped"}).status == "fail"
    assert doctor.check_service({"state": "not_installed"}).status == "fail"


def test_check_last_poll_stale():
    now = datetime(2026, 6, 9, 12, 0, tzinfo=TZ)
    fresh = {"last_poll_time": "2026-06-09T11:59:00+08:00"}
    stale = {"last_poll_time": "2026-06-09T10:00:00+08:00"}
    assert doctor.check_last_poll(fresh, 300, now=now).status == "ok"
    assert doctor.check_last_poll(stale, 300, now=now).status == "warn"
    assert doctor.check_last_poll({"last_poll_time": None}, 300, now=now).status == "warn"


def test_check_last_poll_skips_when_auto_poll_disabled():
    # poll_interval=0 关闭自动轮询：再陈旧的 last_poll 也不该报 warn
    now = datetime(2026, 6, 9, 12, 0, tzinfo=TZ)
    stale = {"last_poll_time": "2020-01-01T00:00:00+08:00"}
    c = doctor.check_last_poll(stale, 0, now=now)
    assert c.status == "ok"
    assert "自动轮询已关闭" in c.detail


def test_check_ai_backend_shallow_missing_model(monkeypatch):
    monkeypatch.setattr(doctor, "_sdk_installed", lambda name: True)
    cfg = {"ai": {"provider": "claude", "model": "", "api_key": "k"}}
    assert doctor.check_ai_backend(cfg).status == "fail"


def test_check_ai_backend_shallow_sdk_missing(monkeypatch):
    monkeypatch.setattr(doctor, "_sdk_installed", lambda name: False)
    cfg = {"ai": {"provider": "claude", "model": "m", "api_key": "k", "base_url": ""}}
    c = doctor.check_ai_backend(cfg)
    assert c.status == "fail" and "anthropic" in c.detail


def test_check_ai_backend_shallow_ok(monkeypatch):
    monkeypatch.setattr(doctor, "_sdk_installed", lambda name: True)
    cfg = {"ai": {"provider": "openai", "model": "gpt", "api_key": "k", "base_url": ""}}
    assert doctor.check_ai_backend(cfg).status == "ok"


def test_check_lark_cli_not_found(monkeypatch):
    import lark_listener.binaries as binaries
    monkeypatch.setattr(binaries, "resolve_executable", lambda name: "lark-cli")
    c = doctor.check_lark_cli(run=lambda *a, **k: None)
    assert c.status == "fail"


def test_check_lark_cli_ok(monkeypatch):
    import lark_listener.binaries as binaries
    monkeypatch.setattr(binaries, "resolve_executable", lambda name: "/usr/local/bin/lark-cli")
    class _R:
        returncode = 0
        stdout = ""
    c = doctor.check_lark_cli(run=lambda *a, **k: _R())
    assert c.status == "ok"


def test_check_lark_cli_auth_warn(monkeypatch):
    import lark_listener.binaries as binaries
    monkeypatch.setattr(binaries, "resolve_executable", lambda name: "/usr/local/bin/lark-cli")
    class _R:
        returncode = 1
        stdout = ""
    c = doctor.check_lark_cli(run=lambda *a, **k: _R())
    assert c.status == "warn"


def test_check_recent_errors(tmp_path):
    log = tmp_path / "stderr.log"
    log.write_text("ok line\nTraceback (most recent call last):\n  boom\n")
    assert doctor.check_recent_errors(log).status == "warn"
    log.write_text("all good\n")
    assert doctor.check_recent_errors(log).status == "ok"
    assert doctor.check_recent_errors(tmp_path / "none.log").status == "ok"


def test_run_doctor_aggregates_and_exit(monkeypatch):
    monkeypatch.setattr(doctor, "check_config", lambda: doctor.Check("config", "ok"))
    monkeypatch.setattr(doctor, "check_service", lambda s: doctor.Check("service", "ok"))
    monkeypatch.setattr(doctor, "check_lark_cli", lambda *a, **k: doctor.Check("lark_cli", "ok"))
    monkeypatch.setattr(doctor, "check_last_poll", lambda *a, **k: doctor.Check("last_poll", "warn"))
    monkeypatch.setattr(doctor, "check_recent_errors", lambda p: doctor.Check("recent_errors", "ok"))
    monkeypatch.setattr(doctor, "check_ai_backend", lambda *a, **k: doctor.Check("ai_backend", "ok"))
    monkeypatch.setattr(doctor.service, "collect_status", lambda: {"state": "running", "last_poll_time": None})
    monkeypatch.setattr(doctor.config_mod, "load_config", lambda *a, **k: {"poll_interval": 300, "ai": {}})
    checks, code = doctor.run_doctor()
    assert code == 0  # 只有 warn，无 fail
    assert any(c.check == "ai_backend" for c in checks)


def test_run_doctor_fail_exit_1(monkeypatch):
    monkeypatch.setattr(doctor, "check_config", lambda: doctor.Check("config", "fail", "x"))
    monkeypatch.setattr(doctor, "check_service", lambda s: doctor.Check("service", "ok"))
    monkeypatch.setattr(doctor, "check_lark_cli", lambda *a, **k: doctor.Check("lark_cli", "ok"))
    monkeypatch.setattr(doctor, "check_last_poll", lambda *a, **k: doctor.Check("last_poll", "ok"))
    monkeypatch.setattr(doctor, "check_recent_errors", lambda p: doctor.Check("recent_errors", "ok"))
    monkeypatch.setattr(doctor, "check_ai_backend", lambda *a, **k: doctor.Check("ai_backend", "ok"))
    monkeypatch.setattr(doctor.service, "collect_status", lambda: {"state": "running", "last_poll_time": None})
    monkeypatch.setattr(doctor.config_mod, "load_config", lambda *a, **k: {"poll_interval": 300, "ai": {}})
    _, code = doctor.run_doctor()
    assert code == 1


def test_check_ai_backend_deep_paths(monkeypatch):
    monkeypatch.setattr(doctor, "_sdk_installed", lambda name: True)
    cfg = {"ai": {"provider": "claude", "model": "m", "api_key": "k", "base_url": ""}}
    monkeypatch.setattr(doctor, "_deep_probe", lambda *a, **k: (True, ""))
    assert doctor.check_ai_backend(cfg, deep=True).status == "ok"
    monkeypatch.setattr(doctor, "_deep_probe", lambda *a, **k: (False, "boom"))
    c = doctor.check_ai_backend(cfg, deep=True)
    assert c.status == "fail" and "boom" in c.detail


def test_cmd_doctor_json(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_doctor",
                        lambda deep=False: ([doctor.Check("config", "ok", "fine")], 0))
    code = doctor.cmd_doctor(as_json=True)
    import json as _j
    data = _j.loads(capsys.readouterr().out)
    assert data[0]["check"] == "config" and data[0]["status"] == "ok"
    assert code == 0


def test_check_last_poll_non_string_does_not_crash():
    # 损坏的 state.json：last_poll_time 是数字而非字符串 → 不应抛 TypeError
    c = doctor.check_last_poll({"last_poll_time": 12345}, 300)
    assert c.status == "warn"


# --- 二轮 review：lark-cli 授权检查与日志 tail 读取 ---


def test_check_lark_cli_appid_missing_from_profiles(monkeypatch):
    """配置的 appid 不在 lark-cli profile 列表 → fail（服务钉在该 profile 上，
    profile list 成功不代表该 bot 可用）。"""
    import lark_listener.binaries as binaries
    monkeypatch.setattr(binaries, "resolve_executable", lambda name: "/usr/local/bin/lark-cli")

    class _R:
        returncode = 0
        stdout = '[{"appId": "cli_other", "active": true}]'
    c = doctor.check_lark_cli(run=lambda *a, **k: _R(), appid="cli_mine")
    assert c.status == "fail"
    assert "cli_mine" in c.detail
    assert "--profile cli_mine" in c.fix


def test_check_lark_cli_deep_probe_detects_expired_auth(monkeypatch):
    """--deep 用窄区间 messages-search 真探授权：token 过期/缺 scope 时
    profile list 照样成功，浅检会漏报「收不到汇总」的最常见根因。"""
    import lark_listener.binaries as binaries
    monkeypatch.setattr(binaries, "resolve_executable", lambda name: "/usr/local/bin/lark-cli")

    def fake_run(argv, **kw):
        class _R:
            returncode = 0
        if "profile" in argv:
            _R.stdout = '[{"appId": "cli_mine", "active": true}]'
        else:
            assert "--profile" in argv and "cli_mine" in argv
            _R.stdout = '{"ok": false, "error": "token expired"}'
        return _R()

    c = doctor.check_lark_cli(run=fake_run, appid="cli_mine", deep=True)
    assert c.status == "fail"
    assert "auth login" in c.fix


def test_check_lark_cli_deep_probe_ok(monkeypatch):
    import lark_listener.binaries as binaries
    monkeypatch.setattr(binaries, "resolve_executable", lambda name: "/usr/local/bin/lark-cli")

    def fake_run(argv, **kw):
        class _R:
            returncode = 0
            stdout = ('[{"appId": "cli_mine"}]' if "profile" in argv
                      else '{"ok":true,"data":{"messages":[]}}')
        return _R()

    c = doctor.check_lark_cli(run=fake_run, appid="cli_mine", deep=True)
    assert c.status == "ok"


def test_check_recent_errors_reads_only_tail(tmp_path):
    """只读文件尾部（日志无轮转、可长到数百 MB）：64KB 窗口之前的历史
    traceback 不应再被报告。"""
    log = tmp_path / "stderr.log"
    old_tb = "Traceback (most recent call last):\n  old boom\n"
    filler = ("normal line\n" * 8000)  # ≈ 96KB，把旧 traceback 挤出窗口
    log.write_text(old_tb + filler)
    assert doctor.check_recent_errors(log).status == "ok"


def test_deep_probe_claude_passes_base_url(monkeypatch):
    """--deep 的 claude 探测同样要消费 base_url，否则代理网关用户
    探测打到官方端点必失败，doctor 反而误导排查。"""
    import sys
    from unittest.mock import MagicMock, patch
    fake = MagicMock()
    with patch.dict(sys.modules, {"anthropic": fake}):
        doctor._deep_probe("claude", "m", "k", "https://proxy.example")
    assert fake.Anthropic.call_args.kwargs["base_url"] == "https://proxy.example"


def test_check_lark_cli_nonlist_profiles_not_misjudged(monkeypatch):
    """profile list 输出可解析但形态非 list（dict 包裹）→ 不得据此误报
    「appid 不在列表」fail（与「输出不可解析时不误判」同一原则）。"""
    import lark_listener.binaries as binaries
    monkeypatch.setattr(binaries, "resolve_executable", lambda name: "/usr/local/bin/lark-cli")

    class _R:
        returncode = 0
        stdout = '{"profiles": [{"appId": "cli_mine"}]}'
    c = doctor.check_lark_cli(run=lambda *a, **k: _R(), appid="cli_mine")
    assert c.status == "ok"


def test_check_lark_cli_list_of_nondict_profiles_not_misjudged(monkeypatch):
    """profiles 是 list 但元素非 dict（如字符串数组）→ 推不出任何 appId，
    视为不可判定，不得误报「appid 不在列表」（与 dict 包裹同一原则）。"""
    import lark_listener.binaries as binaries
    monkeypatch.setattr(binaries, "resolve_executable", lambda name: "/usr/local/bin/lark-cli")

    class _R:
        returncode = 0
        stdout = '["cli_mine", "cli_other"]'
    c = doctor.check_lark_cli(run=lambda *a, **k: _R(), appid="cli_mine")
    assert c.status == "ok"
