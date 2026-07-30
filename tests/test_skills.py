from pathlib import Path

from joe.skills import import_skill, list_skills


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
    assert list_skills(project)[0]["name"] == "claude-skill"
