from importlib import resources
from pathlib import Path
from lark_listener import agent_adapters


def test_skill_resource_is_packaged():
    src = resources.files("lark_listener").joinpath("skills", "lark-listener", "SKILL.md")
    text = src.read_text(encoding="utf-8")
    assert "LarkListener" in text
    assert "doctor" in text  # 必须指向 doctor 作为事实源


def test_claude_adapter_detect(tmp_path):
    claude = tmp_path / ".claude"
    ad = agent_adapters.ClaudeCodeAdapter(skills_dir=claude / "skills")
    assert ad.detect() is False
    claude.mkdir()
    assert ad.detect() is True


def test_claude_adapter_install_uninstall(tmp_path):
    claude = tmp_path / ".claude"; claude.mkdir()
    ad = agent_adapters.ClaudeCodeAdapter(skills_dir=claude / "skills")
    ad.install()
    skill = claude / "skills" / "lark-listener" / "SKILL.md"
    assert skill.is_file() and "LarkListener" in skill.read_text(encoding="utf-8")
    ad.uninstall()
    assert not (claude / "skills" / "lark-listener").exists()


def test_install_agent_skills_skips_undetected(tmp_path, capsys):
    ad = agent_adapters.ClaudeCodeAdapter(skills_dir=tmp_path / "absent" / "skills")
    code = agent_adapters.install_agent_skills(adapters=[ad])
    assert code == 0
    assert "跳过" in capsys.readouterr().out


def test_install_agent_skills_best_effort(tmp_path, monkeypatch, capsys):
    claude = tmp_path / ".claude"; claude.mkdir()
    ad = agent_adapters.ClaudeCodeAdapter(skills_dir=claude / "skills")
    monkeypatch.setattr(ad, "install", lambda: (_ for _ in ()).throw(OSError("denied")))
    code = agent_adapters.install_agent_skills(adapters=[ad])
    assert code == 0  # 失败不阻断
    assert "失败" in capsys.readouterr().out


def test_uninstall_agent_skills_best_effort():
    class _Bad:
        name = "bad"

        def uninstall(self):
            raise OSError("denied")

    assert agent_adapters.uninstall_agent_skills(adapters=[_Bad()]) == 0
