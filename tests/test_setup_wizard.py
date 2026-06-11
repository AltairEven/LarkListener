import sys
import yaml
from unittest.mock import patch, MagicMock
from lark_listener import setup_wizard


def test_ai_packages_for_maps_backend_to_sdk():
    assert setup_wizard.ai_packages_for("claude") == ["anthropic>=0.30.0"]
    assert setup_wizard.ai_packages_for("openai") == ["openai>=1.30.0"]
    assert setup_wizard.ai_packages_for("ollama") == []   # urllib 直连，无需 SDK
    assert setup_wizard.ai_packages_for("unknown") == []


@patch("lark_listener.setup_wizard.subprocess.run")
def test_pip_install_ai_falls_back_to_sys_executable(mock_run, tmp_path, monkeypatch):
    """服务 venv 不存在时回退当前解释器。必须把 VENV_DIR 指到不存在的路径——
    否则本机装有生产 venv（~/.lark_listener/venv）时该测试会环境性失败。"""
    monkeypatch.setattr(setup_wizard.service, "VENV_DIR", tmp_path / "no-venv")
    mock_run.return_value = MagicMock(returncode=0)
    setup_wizard._pip_install_ai("claude")
    argv = mock_run.call_args[0][0]
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-m", "pip", "install"]
    assert "anthropic>=0.30.0" in argv


@patch("lark_listener.setup_wizard.subprocess.run")
def test_pip_install_ai_prefers_venv_python_when_present(mock_run, tmp_path, monkeypatch):
    """When the venv python exists, install into it (not the interpreter running
    setup) — covers `pip install` direct installs where sys.executable is the
    system/CLT python, which would put the SDK outside the venv the daemon runs in."""
    venv_py = tmp_path / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("")
    monkeypatch.setattr(setup_wizard.service, "VENV_DIR", tmp_path / "venv")
    mock_run.return_value = MagicMock(returncode=0)

    setup_wizard._pip_install_ai("claude")

    argv = mock_run.call_args[0][0]
    assert argv[0] == str(venv_py)
    assert argv[1:4] == ["-m", "pip", "install"]


@patch("lark_listener.setup_wizard.subprocess.run")
def test_pip_install_ai_noop_for_ollama(mock_run):
    setup_wizard._pip_install_ai("ollama")
    mock_run.assert_not_called()


@patch("lark_listener.setup_wizard.subprocess.run")
def test_pip_install_ai_survives_failure(mock_run):
    mock_run.return_value = MagicMock(returncode=1)
    # 安装失败只警告，不抛
    setup_wizard._pip_install_ai("openai")


def test_build_config_dict_shape():
    cfg = setup_wizard.build_config_dict(
        poll_interval=600, appid="cli_x", keywords=["部署", "故障"],
        ai_provider="openai", ai_model="gpt-4o", ai_key="sk-1", ai_base_url="",
        user_id="ou_me", bot_chat_id="oc_bot",
    )
    assert cfg["poll_interval"] == 600
    assert cfg["lark_cli_appid"] == "cli_x"
    assert cfg["keywords"] == ["部署", "故障"]
    assert cfg["ai"] == {"provider": "openai", "model": "gpt-4o", "api_key": "sk-1", "base_url": ""}
    assert cfg["notify"] == {"user_id": "ou_me", "bot_chat_id": "oc_bot"}
    # bot 自身会话默认排除，避免汇总自己的推送
    assert cfg["exclude_chats"] == [{"chat_id": "oc_bot", "name": "LarkListener Bot"}]
    assert "include_at_all" not in cfg
    assert "exclude_chat_ids" not in cfg


def test_write_config_file_roundtrips(tmp_path):
    cfg = setup_wizard.build_config_dict(
        poll_interval=300, appid="cli_y", keywords=[],
        ai_provider="claude", ai_model="claude-sonnet-4-6", ai_key="", ai_base_url="http://x",
        user_id="ou_a", bot_chat_id="oc_b",
    )
    path = tmp_path / "config.yaml"
    setup_wizard.write_config_file(str(path), cfg)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded == cfg


# --- 二轮 review：权限/退出码/交互健壮性 ---

import os as _os
import stat as _stat


def test_write_config_file_sets_0600(tmp_path):
    p = tmp_path / "config.yaml"
    setup_wizard.write_config_file(str(p), {"ai": {"api_key": "secret"}})
    assert _stat.S_IMODE(p.stat().st_mode) == 0o600


def test_parse_poll_input():
    """轮询间隔输入解析：非数字回退默认而非裸 ValueError 终止向导。"""
    assert setup_wizard._parse_poll("") == 300
    assert setup_wizard._parse_poll("600") == 600
    assert setup_wizard._parse_poll("0") == 0
    assert setup_wizard._parse_poll("-5") == 0
    assert setup_wizard._parse_poll("五分钟") == 300


def test_cmd_setup_returns_1_without_lark_cli(monkeypatch):
    monkeypatch.setattr("lark_listener.binaries.resolve_executable",
                        lambda n: "/nonexistent/lark-cli")
    assert setup_wizard.cmd_setup() == 1


def test_cmd_setup_eof_cancels(monkeypatch, tmp_path):
    """stdin 关闭（管道/非交互）时不得裸 EOFError traceback。"""
    monkeypatch.setattr("lark_listener.binaries.resolve_executable", lambda n: sys.executable)
    monkeypatch.setattr(setup_wizard, "_detect_active_appid", lambda: ("", "", ""))
    monkeypatch.setattr(setup_wizard.service, "LISTENER_HOME", tmp_path)

    def _eof(_prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    assert setup_wizard.cmd_setup() == 1


def test_build_config_dict_new_format():
    cfg = setup_wizard.build_config_dict(
        poll_interval=300, appid="cli_x", keywords=["SDK"],
        ai_provider="claude", ai_model="m", ai_key="k", ai_base_url="",
        user_id="ou_me", bot_chat_id="oc_bot")
    assert "include_at_all" not in cfg
    assert "exclude_chat_ids" not in cfg
    assert cfg["exclude_chats"] == [{"chat_id": "oc_bot", "name": "LarkListener Bot"}]
    assert cfg["special_focus"] == {"enabled": False, "max_messages": 20, "chats": []}
