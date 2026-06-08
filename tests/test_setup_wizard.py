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
def test_pip_install_ai_installs_into_venv_python(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    setup_wizard._pip_install_ai("claude")
    argv = mock_run.call_args[0][0]
    assert argv[0] == sys.executable          # 装进当前 venv 的 python
    assert argv[1:4] == ["-m", "pip", "install"]
    assert "anthropic>=0.30.0" in argv


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
    assert cfg["exclude_chat_ids"] == ["oc_bot"]


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
