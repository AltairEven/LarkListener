import json
import pytest

from lark_listener import config_cli

FULL = {
    "poll_interval": 300, "keywords": ["上线"],
    "ai": {"provider": "claude", "model": "m", "api_key": "secret", "base_url": ""},
    "notify": {"user_id": "ou_x", "bot_chat_id": "oc_y"},
    "lark_cli_appid": "cli_x",
}


def test_config_get_masks_api_key(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get(as_json=True, path="/tmp/x.yaml")
    data = json.loads(capsys.readouterr().out)
    assert data["ai"]["api_key"] == "***"
    assert data["ai"]["model"] == "m"
    assert code == 0


def test_config_get_dotted_key(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get("ai.provider", as_json=True, path="/tmp/x.yaml")
    assert json.loads(capsys.readouterr().out) == "claude"
    assert code == 0


def test_config_get_unknown_key(monkeypatch):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    assert config_cli.config_get("ai.nope", path="/tmp/x.yaml") == 1


def test_config_get_load_failure(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad")
    monkeypatch.setattr(config_cli.config_mod, "load_config", boom)
    assert config_cli.config_get(path="/tmp/x.yaml") == 1


def test_config_get_yaml_output_masks(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get(path="/tmp/x.yaml")  # no key, not json → yaml
    out = capsys.readouterr().out
    assert "provider: claude" in out
    assert "***" in out and "secret" not in out
    assert code == 0


def test_config_get_scalar_print(monkeypatch, capsys):
    monkeypatch.setattr(config_cli.config_mod, "load_config", lambda *a, **k: dict(FULL))
    code = config_cli.config_get("poll_interval", path="/tmp/x.yaml")  # scalar, not json
    assert "300" in capsys.readouterr().out
    assert code == 0


def test_config_path_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LARK_LISTENER_HOME", str(tmp_path))
    assert config_cli._config_path() == tmp_path / "config.yaml"


def test_config_path_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("LARK_LISTENER_HOME", "/somewhere")
    from pathlib import Path
    assert config_cli._config_path("/tmp/explicit.yaml") == Path("/tmp/explicit.yaml")


def _write_cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "poll_interval: 300\n"
        "keywords:\n  - 上线\n"
        "exclude_chats:\n  - chat_id: oc_bot\n    name: ''\n"
        "special_focus:\n  enabled: false\n  max_messages: 20\n  chats: []\n"
        "ai:\n  provider: claude\n  model: m\n  api_key: secret\n  base_url: ''\n"
        "notify:\n  user_id: ou_x\n  bot_chat_id: oc_bot\n"
        "lark_cli_appid: cli_x\n",
        encoding="utf-8",
    )
    return p


def test_config_set_scalar(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("poll_interval", "600", path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["poll_interval"] == 600


def test_config_set_list_replace_add_remove(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("keywords", "故障,告警", path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["keywords"] == ["故障", "告警"]
    assert config_cli.config_set("keywords", "上线", add=True, path=p) == 0
    assert "上线" in config_cli.config_mod.load_config(str(p))["keywords"]
    assert config_cli.config_set("keywords", "故障", remove=True, path=p) == 0
    assert "故障" not in config_cli.config_mod.load_config(str(p))["keywords"]


def test_config_set_protected_needs_force(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("ai.model", "m2", path=p) == 1          # 无 force 拒绝
    assert config_cli.config_set("ai.model", "m2", force=True, path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["ai"]["model"] == "m2"


def test_config_set_validation_rollback(tmp_path):
    p = _write_cfg(tmp_path)
    # 把必填的 ai.model 清空 → _validate 失败 → 应回滚到原值。
    # 注意：不能字节比对原文件（ruamel 回滚会重新 dump，格式不保证逐字节一致），
    # 改为校验「值已回到原值」。
    assert config_cli.config_set("ai.model", "", force=True, path=p) == 1
    assert config_cli.config_mod.load_config(str(p))["ai"]["model"] == "m"


def test_config_set_add_on_scalar_errors(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("poll_interval", "5", add=True, path=p) == 1


def test_config_set_preserves_comments(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("# 轮询秒数\npoll_interval: 300\nkeywords: []\n"
                 "ai:\n  provider: claude\n  model: m\n  api_key: k\n  base_url: ''\n"
                 "notify:\n  user_id: ou\n  bot_chat_id: oc\n"
                 "lark_cli_appid: cli\n", encoding="utf-8")
    config_cli.config_set("poll_interval", "600", path=p)
    assert "# 轮询秒数" in p.read_text(encoding="utf-8")


def test_config_set_unknown_top_level_key(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("nope", "5", path=p) == 1


def test_config_set_add_and_remove_conflict(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("keywords", "x", add=True, remove=True, path=p) == 1


def test_config_set_keywords_empty_clears(tmp_path):
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("keywords", "", path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["keywords"] == []


def test_config_set_api_key_not_echoed(tmp_path, capsys):
    p = _write_cfg(tmp_path)  # 该 fixture 的 api_key 值为 'secret'
    assert config_cli.config_set("ai.api_key", "supersecret", force=True, path=p) == 0
    out = capsys.readouterr().out
    assert "supersecret" not in out   # 新值不回显
    assert "secret" not in out        # 旧值（'secret'）也不回显
    assert "已隐藏" in out
    # 但确实写进了文件
    assert config_cli.config_mod.load_config(str(p))["ai"]["api_key"] == "supersecret"


# --- 二轮 review：null 列表字段 ---

NULL_LIST_CFG = ("poll_interval: 300\nkeywords:\n"
                 "ai:\n  provider: claude\n  model: m\n  api_key: k\n  base_url: ''\n"
                 "notify:\n  user_id: ou\n  bot_chat_id: oc\n"
                 "lark_cli_appid: cli\n")


def test_config_set_add_to_null_list(tmp_path):
    """手编 `keywords:`（null）后 --add 应按空列表处理，而不是被拒。"""
    p = tmp_path / "config.yaml"
    p.write_text(NULL_LIST_CFG, encoding="utf-8")
    assert config_cli.config_set("keywords", "上线", add=True, path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["keywords"] == ["上线"]


def test_config_set_null_list_plain_set_makes_list(tmp_path):
    """null 列表字段整体 set 必须写成列表，绝不能落成字符串
    （字符串会被 fetcher 逐字符当关键词搜索）。"""
    p = tmp_path / "config.yaml"
    p.write_text(NULL_LIST_CFG, encoding="utf-8")
    assert config_cli.config_set("keywords", "上线,故障", path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["keywords"] == ["上线", "故障"]


def test_config_set_remove_bot_chat_blocked_without_force(tmp_path):
    """CLI 路径不能把 bot 会话移出 exclude_chats；--force 保留 owner 逃生口。"""
    p = _write_cfg(tmp_path)
    assert config_cli.config_set("exclude_chats", "oc_bot", remove=True, path=p) == 1
    assert config_cli.config_mod.load_config(str(p))["exclude_chats"] == [{"chat_id": "oc_bot", "name": ""}]
    # --force 放行
    assert config_cli.config_set("exclude_chats", "oc_bot", remove=True, force=True, path=p) == 0
    assert config_cli.config_mod.load_config(str(p))["exclude_chats"] == []


def test_config_set_exclude_chats_add(tmp_path):
    p = _write_cfg(tmp_path)
    rc = config_cli.config_set("exclude_chats", "oc_new", add=True, path=p)
    assert rc == 0
    cfg = config_cli.config_mod.load_config(str(p))
    assert {"chat_id": "oc_new", "name": ""} in cfg["exclude_chats"]


def test_config_set_exclude_chats_remove_bot_guarded(tmp_path):
    p = _write_cfg(tmp_path)
    rc = config_cli.config_set("exclude_chats", "oc_bot", remove=True, path=p)
    assert rc == 1   # 防自反馈守卫


def test_config_set_exclude_chats_remove_bot_forced(tmp_path):
    p = _write_cfg(tmp_path)
    rc = config_cli.config_set("exclude_chats", "oc_bot", remove=True, force=True, path=p)
    assert rc == 0


def test_config_set_special_focus_enabled(tmp_path):
    p = _write_cfg(tmp_path)
    rc = config_cli.config_set("special_focus.enabled", "true", path=p)
    assert rc == 0
    cfg = config_cli.config_mod.load_config(str(p))
    assert cfg["special_focus"]["enabled"] is True


def test_config_set_special_focus_chats_rejected(tmp_path):
    p = _write_cfg(tmp_path)
    rc = config_cli.config_set("special_focus.chats", "oc_x", add=True, path=p)
    assert rc == 1
