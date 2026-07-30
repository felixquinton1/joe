from pathlib import Path

import pytest

from joe import skills
from joe.skills import (
    import_skill,
    list_global_skills,
    list_skills,
    promote_skill,
)


def test_import_skill_creates_provider_neutral_project_skill(tmp_path):
    source = tmp_path / "claude-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("Use Claude's plan mode before editing.")
    project = tmp_path / "project"
    project.mkdir()

    result = import_skill(project, source, source_provider="claude")

    target = Path(result["path"])
    assert target.exists()
    content = target.read_text()
    assert "provider-specific commands must be translated" in content
    assert "Claude's plan mode" in content
    listed = list_skills(project)
    assert listed[0]["name"] == "claude-skill"
    assert listed[0]["active"] is True


def test_promote_skill_copies_project_skill_to_global(tmp_path, monkeypatch):
    global_root = tmp_path / "global-skills"
    monkeypatch.setattr(skills, "global_skills_root", lambda: global_root)
    project = tmp_path / "project"
    source = tmp_path / "rules"
    source.mkdir()
    (source / "SKILL.md").write_text("Toujours écrire des tests ciblés.")
    import_skill(project, source, name="conventions")

    assert list_global_skills() == []

    promoted = promote_skill(project, "conventions")

    assert promoted["scope"] == "global"
    assert Path(promoted["path"]).read_text() == "Toujours écrire des tests ciblés."
    common = list_global_skills()
    assert [item["name"] for item in common] == ["conventions"]
    assert common[0]["scope"] == "global"


def test_promote_skill_rejects_unknown_project_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "global_skills_root", lambda: tmp_path / "global")
    with pytest.raises(FileNotFoundError):
        promote_skill(tmp_path / "project", "absent")
