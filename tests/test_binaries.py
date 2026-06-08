import os
from unittest.mock import patch

from lark_listener import binaries
from lark_listener.binaries import lark_cli, resolve_executable, set_lark_profile


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
