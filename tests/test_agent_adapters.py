from importlib import resources


def test_skill_resource_is_packaged():
    src = resources.files("lark_listener").joinpath("skills", "lark-listener", "SKILL.md")
    text = src.read_text(encoding="utf-8")
    assert "LarkListener" in text
    assert "doctor" in text  # 必须指向 doctor 作为事实源
