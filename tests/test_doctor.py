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
