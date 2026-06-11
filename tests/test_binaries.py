import os
from unittest.mock import patch, MagicMock

from lark_listener import binaries
from lark_listener.binaries import lark_cli, resolve_executable, set_lark_profile, get_chat_name


def teardown_function():
    # Profile is module-level global state; reset after each test so ordering
    # never leaks a pin into an unrelated test.
    set_lark_profile(None)


def test_resolve_executable_does_not_cache_failure(tmp_path, monkeypatch):
    """A failed resolution (binary not yet installed at startup) must NOT be cached
    forever — once the binary appears, the next call must find it without a restart."""
    name = "ll_probe_tool"
    binaries._resolve_cache.pop(name, None)
    monkeypatch.setattr(binaries, "_COMMON_BIN_DIRS", (str(tmp_path),))

    with patch("lark_listener.binaries.shutil.which", return_value=None):
        # Not present yet → falls back to bare name (must not be cached).
        assert resolve_executable(name) == name

        # Now it appears in a known dir.
        exe = tmp_path / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        assert resolve_executable(name) == str(exe)

    binaries._resolve_cache.pop(name, None)


def test_lark_cli_without_profile_appends_nothing():
    cmd = lark_cli("im", "+messages-send", "--as", "bot")
    assert cmd[1:] == ["im", "+messages-send", "--as", "bot"]
    assert "--profile" not in cmd


def test_lark_cli_with_profile_appends_flag_last():
    set_lark_profile("cli_abc123")
    cmd = lark_cli("im", "+messages-send", "--as", "bot")
    assert cmd[-2:] == ["--profile", "cli_abc123"]


def test_set_lark_profile_empty_string_clears_pin():
    set_lark_profile("cli_abc123")
    set_lark_profile("")
    assert "--profile" not in lark_cli("im", "x")


def test_set_lark_profile_none_clears_pin():
    set_lark_profile("cli_abc123")
    set_lark_profile(None)
    assert "--profile" not in lark_cli("im", "x")


def test_lark_cli_first_element_is_resolved_binary():
    cmd = lark_cli("im", "x")
    assert cmd[0].endswith("lark-cli")


# --- 简洁性重构：event 订阅子进程 pkill 模式唯一事实源 ---

from lark_listener.binaries import event_subscriber_pkill_pattern


# --- get_chat_name ---


@patch("lark_listener.binaries.subprocess.run")
def test_get_chat_name_user_identity_first(mock_run):
    mock = MagicMock(); mock.returncode = 0; mock.stdout = '"技术群"\n'
    mock_run.return_value = mock
    assert get_chat_name("oc_x") == "技术群"
    assert mock_run.call_count == 1
    args = mock_run.call_args_list[0][0][0]
    assert "--as" in args and args[args.index("--as") + 1] == "user"


@patch("lark_listener.binaries.subprocess.run")
def test_get_chat_name_falls_back_to_bot(mock_run):
    fail = MagicMock(); fail.returncode = 1; fail.stdout = ""
    ok = MagicMock(); ok.returncode = 0; ok.stdout = '"Bot群"\n'
    mock_run.side_effect = [fail, ok]
    assert get_chat_name("oc_x") == "Bot群"
    args = mock_run.call_args_list[1][0][0]
    assert args[args.index("--as") + 1] == "bot"


@patch("lark_listener.binaries.subprocess.run")
def test_get_chat_name_failure_returns_empty(mock_run):
    mock_run.side_effect = OSError("no cli")
    assert get_chat_name("oc_x") == ""


def test_event_subscriber_pkill_pattern():
    """三要素缺一不可：subscribe（不误杀 event consume）、--as bot、
    --profile + 结尾锚定（cli_abc 不匹配 cli_abc123）；appid 经 re.escape。"""
    import re
    p = event_subscriber_pkill_pattern("cli_mine")
    assert "subscribe" in p and "--as bot" in p
    assert p.endswith("( |$)")
    assert re.search(p, "node /x/lark-cli event +subscribe --as bot --force --profile cli_mine")
    assert not re.search(p, "node /x/lark-cli event +subscribe --as bot --force --profile cli_mine123")
    assert not re.search(p, "node /x/lark-cli event consume Key --as bot --profile cli_mine")
