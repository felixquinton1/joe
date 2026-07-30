from pathlib import Path

import pytest

from joe import skills
from joe.skills import (
    create_skill,
    import_skill,
    list_global_skills,
    list_skills,
    parse_skill_request,
    promote_skill,
)


@pytest.mark.parametrize(
    ("prompt", "name", "scope", "has_instructions"),
    [
        ("crée un skill test", "test", "project", True),
        ("Crée un skill commun test", "test", "global", True),
        (
            "créer un skill « revue-python » qui vérifie les tests",
            "revue-python",
            "project",
            True,
        ),
        ("explique les skills", None, None, False),
    ],
)
def test_parse_skill_creation_request(prompt, name, scope, has_instructions):
    parsed = parse_skill_request(prompt)
    if name is None:
        assert parsed is None
        return
    assert parsed["name"] == name
    assert parsed["scope"] == scope
    assert bool(parsed["instructions"]) is has_instructions


def test_create_project_skill_from_instructions(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    created = create_skill(
        project,
        "Conventions Python",
        "Écrire des fonctions courtes et ajouter des tests.",
    )

    target = Path(created["path"])
    assert created["scope"] == "project"
    assert target == project / ".agentflow/skills/conventions-python/SKILL.md"
    assert target.read_text() == (
        "# Conventions Python\n\n"
        "Écrire des fonctions courtes et ajouter des tests.\n"
    )


def test_create_and_import_global_skills(tmp_path, monkeypatch):
    global_root = tmp_path / "global-skills"
    monkeypatch.setattr(skills, "global_skills_root", lambda: global_root)

    created = create_skill(
        None,
        "Revue",
        "Relire les modifications avant livraison.",
        global_scope=True,
    )
    source = tmp_path / "existing"
    source.mkdir()
    (source / "SKILL.md").write_text("Documenter les décisions.")
    imported = import_skill(None, source, global_scope=True)

    assert created["scope"] == "global"
    assert imported["scope"] == "global"
    assert [item["name"] for item in list_global_skills()] == ["existing", "revue"]


def test_create_skill_refuses_empty_or_existing_content(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError):
        create_skill(project, "vide", " ")
    create_skill(project, "unique", "Une règle.")
    with pytest.raises(FileExistsError):
        create_skill(project, "unique", "Une autre règle.")


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
