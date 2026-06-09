import importlib
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from lark_listener import service


def test_env_overrides_home_and_label(monkeypatch):
    # 设了 env 后重载模块，常量应反映覆盖值；plist 文件名随 Label 派生。
    monkeypatch.setenv("LARK_LISTENER_HOME", "/tmp/ll-test-home")
    monkeypatch.setenv("LARK_LISTENER_LABEL", "com.larklistener.test")
    try:
        importlib.reload(service)
        assert str(service.LISTENER_HOME) == "/tmp/ll-test-home"
        assert str(service.VENV_DIR) == "/tmp/ll-test-home/venv"
        assert service.LABEL == "com.larklistener.test"
        assert service.PLIST_PATH.name == "com.larklistener.test.plist"
    finally:
        monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)
        monkeypatch.delenv("LARK_LISTENER_LABEL", raising=False)
        importlib.reload(service)  # 复原默认，避免污染其它测试


def test_defaults_without_env():
    # 默认（无 env）：~/.lark_listener 与 com.larklistener。
    assert str(service.LISTENER_HOME) == str(Path.home() / ".lark_listener")
    assert service.LABEL == "com.larklistener"
    assert service.PLIST_PATH.name == "com.larklistener.plist"


def test_shim_path_points_into_venv():
    p = service.shim_path()
    assert os.path.isabs(p)
    assert p.endswith("/.lark_listener/venv/bin/lark-listener")


def _stub_venv(tmp_path):
    """建一个临时 venv 入口文件并返回其路径。"""
    venv_exe = tmp_path / "venv" / "bin" / "lark-listener"
    venv_exe.parent.mkdir(parents=True)
    venv_exe.write_text("#!/bin/sh\n")
    return venv_exe


def test_ensure_shim_link_creates_symlink_and_records(tmp_path, monkeypatch):
    # 验证软链建立、指向 venv 入口，并把位置写入 SHIM_RECORD。
    monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)  # 确保非 dev 隔离态
    venv_exe = _stub_venv(tmp_path)
    link = tmp_path / ".local" / "bin" / "lark-listener"
    record = tmp_path / "shim_link"
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "SHIM_LINK", link)
    monkeypatch.setattr(service, "SHIM_RECORD", record)

    service.ensure_shim_link()

    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(venv_exe)
    assert record.read_text().strip() == str(link)


def test_ensure_shim_link_skips_when_record_valid(tmp_path, monkeypatch):
    # install.sh 已建软链（记录有效）→ 不在默认位置重复建，避免目录冲突。
    monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)
    venv_exe = _stub_venv(tmp_path)
    existing = tmp_path / "elsewhere" / "lark-listener"
    existing.parent.mkdir(parents=True)
    existing.symlink_to(venv_exe)
    record = tmp_path / "shim_link"; record.write_text(str(existing) + "\n")
    default_link = tmp_path / ".local" / "bin" / "lark-listener"
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "SHIM_LINK", default_link)
    monkeypatch.setattr(service, "SHIM_RECORD", record)

    service.ensure_shim_link()

    assert not default_link.exists()  # 跳过，未在默认位置另建


def test_ensure_shim_link_survives_permission_error(tmp_path, monkeypatch):
    # ~/.local/bin 不可写（如属主 root）时不得崩掉 setup，只警告。
    monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)
    _stub_venv(tmp_path)
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "SHIM_LINK", tmp_path / ".local" / "bin" / "lark-listener")
    monkeypatch.setattr(service, "SHIM_RECORD", tmp_path / "shim_link")
    import pathlib
    monkeypatch.setattr(pathlib.Path, "symlink_to",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("Permission denied")))
    service.ensure_shim_link()  # 必须不抛


def test_ensure_shim_link_skips_in_dev_mode(tmp_path, monkeypatch):
    # 开发隔离态（设了 LARK_LISTENER_HOME）即使 venv 入口存在也不建软链，
    # 绝不覆盖生产的 ~/.local/bin/lark-listener。
    monkeypatch.setenv("LARK_LISTENER_HOME", str(tmp_path / "dev-home"))
    _stub_venv(tmp_path)
    link = tmp_path / ".local" / "bin" / "lark-listener"
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "SHIM_LINK", link)
    monkeypatch.setattr(service, "SHIM_RECORD", tmp_path / "shim_link")

    service.ensure_shim_link()

    assert not link.exists()  # dev 态跳过，未建软链


def test_ensure_shim_link_noop_when_venv_missing(tmp_path, monkeypatch):
    # venv 入口不存在时不建软链（不抛）。
    monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)
    link = tmp_path / ".local" / "bin" / "lark-listener"
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "SHIM_LINK", link)
    monkeypatch.setattr(service, "SHIM_RECORD", tmp_path / "shim_link")

    service.ensure_shim_link()

    assert not link.exists()


def test_node_bin_dir_returns_dirname():
    with patch("lark_listener.service.shutil.which", return_value="/Users/x/.nvm/versions/node/v20/bin/node"):
        assert service.node_bin_dir() == "/Users/x/.nvm/versions/node/v20/bin"


def test_node_bin_dir_none_when_missing():
    with patch("lark_listener.service.shutil.which", return_value=None):
        assert service.node_bin_dir() is None


def test_build_plist_uses_absolute_program_and_run(monkeypatch):
    monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)
    xml = service.build_plist("/Users/x/.local/bin/lark-listener", ["/Users/x/.nvm/versions/node/v20/bin"])
    assert "<string>/Users/x/.local/bin/lark-listener</string>" in xml
    assert "<string>run</string>" in xml
    # launchd 不展开 ~，确保没有波浪号路径漏进 plist
    assert "~/" not in xml
    # 动态解析的 node 目录并入 PATH
    assert "/Users/x/.nvm/versions/node/v20/bin" in xml
    assert "com.larklistener" in xml
    # 日志路径指向 LISTENER_HOME/logs
    assert "/.lark_listener/logs/stdout.log" in xml
    assert "/.lark_listener/logs/stderr.log" in xml
    # 生产态（无 dev env）只透传 PATH，不写 LARK_LISTENER_HOME
    assert "LARK_LISTENER_HOME" not in xml


def test_build_plist_passes_dev_env_to_launchd(monkeypatch):
    # 开发隔离态：plist 必须把 LARK_LISTENER_HOME/LABEL 写进 EnvironmentVariables，
    # 否则 launchd 起的 run 服务读不到 dev 配置（会回退到生产路径）。
    monkeypatch.setenv("LARK_LISTENER_HOME", "/tmp/devhome")
    monkeypatch.setenv("LARK_LISTENER_LABEL", "com.larklistener.dev")
    try:
        importlib.reload(service)
        xml = service.build_plist("/tmp/devhome/venv/bin/lark-listener", [])
        assert "<key>LARK_LISTENER_HOME</key>" in xml
        assert "<string>/tmp/devhome</string>" in xml
        assert "<key>LARK_LISTENER_LABEL</key>" in xml
        assert "<string>com.larklistener.dev</string>" in xml
    finally:
        monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)
        monkeypatch.delenv("LARK_LISTENER_LABEL", raising=False)
        importlib.reload(service)


# --- _is_running exact label match (review 🟢) ---


def test_is_running_exact_match_not_prefix():
    """A dev job `com.larklistener.dev` must NOT make prod LABEL `com.larklistener`
    report running — launchctl list lines are matched by exact trailing label."""
    assert service.LABEL == "com.larklistener"  # default (no env)
    out = MagicMock(stdout="-\t0\tcom.larklistener.dev\n123\t0\tcom.apple.foo\n")
    with patch("lark_listener.service.subprocess.run", return_value=out):
        assert service._is_running() is False

    out2 = MagicMock(stdout="123\t0\tcom.larklistener\n-\t0\tcom.larklistener.dev\n")
    with patch("lark_listener.service.subprocess.run", return_value=out2):
        assert service._is_running() is True


# --- build_plist XML escaping (review 🟢) ---


def test_build_plist_escapes_xml_special_chars(monkeypatch):
    monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)
    xml = service.build_plist("/Users/a&b/bin/lark-listener", ["/x<y>/bin"])
    assert "&amp;" in xml
    assert "/Users/a&b/" not in xml  # raw ampersand must not leak (would be invalid XML)
    assert "&lt;y&gt;" in xml


# --- cmd_start idempotent load (review #5) ---


def test_cmd_start_unloads_before_load(tmp_path, monkeypatch):
    """load must be preceded by an unload even when not currently running, so a
    job stuck in 'loaded-but-not-running' (throttled) state loads cleanly."""
    plist = tmp_path / "x.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "_is_running", lambda: False)
    monkeypatch.setattr(service.time, "sleep", lambda *a: None)

    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="")

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    service.cmd_start()

    launchctl = [c for c in calls if c[:1] == ["launchctl"]]
    unload_idx = next(i for i, c in enumerate(launchctl) if c[:2] == ["launchctl", "unload"])
    load_idx = next(i for i, c in enumerate(launchctl) if c[:2] == ["launchctl", "load"])
    assert unload_idx < load_idx


# --- cmd_uninstall cleanup (review test blind spot) ---


def test_cmd_uninstall_removes_recorded_shim_plist_and_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    record = home / "shim_link"
    venv_exe = tmp_path / "venv" / "bin" / "lark-listener"
    venv_exe.parent.mkdir(parents=True)
    venv_exe.write_text("#!/bin/sh\n")
    shim = tmp_path / "elsewhere" / "lark-listener"
    shim.parent.mkdir(parents=True)
    shim.symlink_to(venv_exe)
    record.write_text(str(shim) + "\n")
    plist = tmp_path / "com.larklistener.plist"
    plist.write_text("<plist/>")
    default_link = tmp_path / "default" / "lark-listener"

    monkeypatch.setattr(service, "LISTENER_HOME", home)
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "SHIM_RECORD", record)
    monkeypatch.setattr(service, "SHIM_LINK", default_link)
    monkeypatch.setattr(service, "stop_service", lambda: None)

    with patch("builtins.input", return_value="y"):
        service.cmd_uninstall()

    assert not plist.exists()
    assert not shim.exists()        # recorded shim (in another dir) removed
    assert not home.exists()        # data dir (incl. record) rmtree'd


def test_collect_status_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "PLIST_PATH", tmp_path / "nope.plist")
    monkeypatch.setattr(service, "LISTENER_HOME", tmp_path)
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "_pids", lambda pat: [])
    monkeypatch.setattr(service, "_recorded_shim", lambda: None)
    st = service.collect_status()
    assert st["state"] == "not_installed"
    assert st["main_pids"] == []
    assert st["files"]["config"]["exists"] is False
    assert st["last_poll_time"] is None


def test_collect_status_running_reads_last_poll(tmp_path, monkeypatch):
    plist = tmp_path / "svc.plist"; plist.write_text("x")
    (tmp_path / "state.json").write_text('{"last_poll_time": "2026-06-09T10:00:00+08:00"}')
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "LISTENER_HOME", tmp_path)
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "_is_running", lambda: True)
    monkeypatch.setattr(service, "_pids", lambda pat: ["123"])
    monkeypatch.setattr(service, "_recorded_shim", lambda: None)
    st = service.collect_status()
    assert st["state"] == "running"
    assert st["main_pids"] == ["123"]
    assert st["last_poll_time"] == "2026-06-09T10:00:00+08:00"


def test_cmd_status_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(service, "collect_status",
                        lambda: {"state": "running", "main_pids": ["1"], "event_pids": [],
                                 "files": {}, "last_poll_time": None})
    assert service.cmd_status() == 0
    monkeypatch.setattr(service, "collect_status",
                        lambda: {"state": "stopped", "main_pids": [], "event_pids": [],
                                 "files": {}, "last_poll_time": None})
    assert service.cmd_status() == 3
    monkeypatch.setattr(service, "collect_status",
                        lambda: {"state": "not_installed", "main_pids": [], "event_pids": [],
                                 "files": {}, "last_poll_time": None})
    assert service.cmd_status() == 4


def test_cmd_status_json_output(monkeypatch, capsys):
    monkeypatch.setattr(service, "collect_status",
                        lambda: {"state": "running", "main_pids": ["7"], "event_pids": [],
                                 "files": {}, "last_poll_time": "2026-06-09T10:00:00+08:00"})
    code = service.cmd_status(as_json=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["state"] == "running" and data["main_pids"] == ["7"]
    assert code == 0


def test_cmd_status_collect_failure_returns_1(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(service, "collect_status", boom)
    assert service.cmd_status() == 1


def test_render_status_text_content(capsys):
    st = {"state": "running", "main_pids": ["7"], "event_pids": ["8"],
          "files": {"config": {"path": "/h/config.yaml", "exists": True},
                    "logs": {"path": "/h/logs", "exists": True}},
          "last_poll_time": "2026-06-09T10:00:00+08:00"}
    service._render_status_text(st)
    out = capsys.readouterr().out
    assert "● 服务运行中" in out
    assert "7" in out and "8" in out
    assert "/h/config.yaml" in out
    assert "/h/logs/" in out          # 目录尾部斜杠
    assert "上次轮询：2026-06-09T10:00:00+08:00" in out


# --- Task 3: commands return exit codes ---


def test_cmd_start_returns_1_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "PLIST_PATH", tmp_path / "nope.plist")
    assert service.cmd_start() == 1


def test_cmd_start_returns_0_when_running(monkeypatch, tmp_path):
    plist = tmp_path / "svc.plist"; plist.write_text("x")
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(service.time, "sleep", lambda *_: None)
    monkeypatch.setattr(service, "_is_running", lambda: True)
    assert service.cmd_start() == 0


def test_cmd_stop_and_restart_return_int(monkeypatch):
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr(service, "cmd_start", lambda: 0)
    assert service.cmd_stop() == 0
    assert service.cmd_restart() == 0


def test_cmd_config_missing_returns_1(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "LISTENER_HOME", tmp_path)
    assert service.cmd_config() == 1


def test_cmd_start_returns_1_when_fails_to_run(monkeypatch, tmp_path):
    plist = tmp_path / "svc.plist"; plist.write_text("x")
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "LISTENER_HOME", tmp_path)
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(service.time, "sleep", lambda *_: None)
    monkeypatch.setattr(service, "_is_running", lambda: False)
    assert service.cmd_start() == 1


def test_cmd_restart_propagates_start_failure(monkeypatch):
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr(service, "cmd_start", lambda: 1)
    assert service.cmd_restart() == 1


def test_cmd_config_opens_when_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "LISTENER_HOME", tmp_path)
    (tmp_path / "config.yaml").write_text("x")
    calls = []
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: calls.append(a))
    assert service.cmd_config() == 0
    assert calls  # open editor was invoked


def test_cmd_uninstall_calls_agent_skills(monkeypatch, tmp_path):
    plist = tmp_path / "svc.plist"; plist.write_text("x")
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "LISTENER_HOME", home)
    monkeypatch.setattr(service, "SHIM_LINK", tmp_path / "shim")
    monkeypatch.setattr(service, "SHIM_RECORD", home / "shim_link")
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    monkeypatch.delenv("LARK_LISTENER_HOME", raising=False)
    called = {"n": 0}
    import lark_listener.agent_adapters as aa
    monkeypatch.setattr(aa, "uninstall_agent_skills", lambda: called.__setitem__("n", called["n"] + 1) or 0)
    assert service.cmd_uninstall() == 0
    assert called["n"] == 1


def test_cmd_uninstall_skips_agent_skills_in_dev(monkeypatch, tmp_path):
    plist = tmp_path / "svc.plist"; plist.write_text("x")
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(service, "PLIST_PATH", plist)
    monkeypatch.setattr(service, "LISTENER_HOME", home)
    monkeypatch.setattr(service, "SHIM_LINK", tmp_path / "shim")
    monkeypatch.setattr(service, "SHIM_RECORD", home / "shim_link")
    monkeypatch.setattr(service, "stop_service", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    monkeypatch.setenv("LARK_LISTENER_HOME", str(home))   # dev 隔离态
    called = {"n": 0}
    import lark_listener.agent_adapters as aa
    monkeypatch.setattr(aa, "uninstall_agent_skills", lambda: called.__setitem__("n", called["n"] + 1) or 0)
    assert service.cmd_uninstall() == 0
    assert called["n"] == 0   # dev 态不应触碰真机 ~/.claude
