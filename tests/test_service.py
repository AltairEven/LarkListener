import os
from pathlib import Path
from unittest.mock import patch
from lark_listener import service


def test_shim_path_points_into_venv():
    p = service.shim_path()
    assert os.path.isabs(p)
    assert p.endswith("/.lark_listener/venv/bin/lark-listener")


def test_ensure_shim_link_creates_symlink(tmp_path, monkeypatch):
    # 用临时目录模拟 venv 入口与 ~/.local/bin，验证软链建立且指向 venv 入口。
    venv_exe = tmp_path / "venv" / "bin" / "lark-listener"
    venv_exe.parent.mkdir(parents=True)
    venv_exe.write_text("#!/bin/sh\n")
    link = tmp_path / ".local" / "bin" / "lark-listener"
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "SHIM_LINK", link)

    service.ensure_shim_link()

    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(venv_exe)


def test_ensure_shim_link_noop_when_venv_missing(tmp_path, monkeypatch):
    # venv 入口不存在时不建软链（不抛）。
    link = tmp_path / ".local" / "bin" / "lark-listener"
    monkeypatch.setattr(service, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(service, "SHIM_LINK", link)

    service.ensure_shim_link()

    assert not link.exists()


def test_node_bin_dir_returns_dirname():
    with patch("lark_listener.service.shutil.which", return_value="/Users/x/.nvm/versions/node/v20/bin/node"):
        assert service.node_bin_dir() == "/Users/x/.nvm/versions/node/v20/bin"


def test_node_bin_dir_none_when_missing():
    with patch("lark_listener.service.shutil.which", return_value=None):
        assert service.node_bin_dir() is None


def test_build_plist_uses_absolute_program_and_run():
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
