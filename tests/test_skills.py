import time
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
        ("créé un skill test pour ce projet pour voir si ça marche", "test", "project", True),
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
    assert target.read_text(encoding="utf-8") == (
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
    (source / "SKILL.md").write_text("Documenter les décisions.", encoding="utf-8")
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
    (source / "SKILL.md").write_text("Use Claude's plan mode before editing.", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    result = import_skill(project, source, source_provider="claude")

    target = Path(result["path"])
    assert target.exists()
    content = target.read_text(encoding="utf-8")
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
    (source / "SKILL.md").write_text("Toujours écrire des tests ciblés.", encoding="utf-8")
    import_skill(project, source, name="conventions")

    assert list_global_skills() == []

    promoted = promote_skill(project, "conventions")

    assert promoted["scope"] == "global"
    assert Path(promoted["path"]).read_text(encoding="utf-8") == "Toujours écrire des tests ciblés."
    common = list_global_skills()
    assert [item["name"] for item in common] == ["conventions"]
    assert common[0]["scope"] == "global"


def test_promote_skill_rejects_unknown_project_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "global_skills_root", lambda: tmp_path / "global")
    with pytest.raises(FileNotFoundError):
        promote_skill(tmp_path / "project", "absent")


def test_local_skill_run_reports_routing_and_closes_the_stage(tmp_path):
    """Une action locale doit dire qu'aucun modèle ne tourne, et se clôturer.

    Sans ces événements l'interface affichait « joe en cours » indéfiniment,
    avec une carte d'agent sans modèle ni statut.
    """
    from joe.web_runs import RunManager

    manager = RunManager(tmp_path)
    conversation = manager.conversations.create(None)
    run = manager.start_local_skill(
        "crée un skill test pour ce projet",
        conversation["id"],
        name="test",
        instructions="Répondre avec le marqueur exact SKILL_TEST_ACTIF.",
        global_scope=False,
        classification=None,
    )

    _await_run(run)

    by_type = {}
    for event in run.events:
        by_type.setdefault(event["type"], []).append(event)

    route = by_type["route"][0]
    assert route["primary"] == "joe"
    assert route["local_action"] == "create_skill"
    assert route["decided_by"] == "lexical"
    assert "détection lexicale" in route["reason"]
    assert "classifier" in route

    # La carte d'agent tire son modèle de cet événement.
    model_activity = [
        event for event in by_type["activity"] if event.get("kind") == "model"
    ]
    assert model_activity
    assert "aucun modèle" in model_activity[0]["label"]

    # Clôture explicite : sinon l'étape reste « En cours ».
    end = by_type["provider_end"][0]
    assert end["provider"] == "joe"
    assert end["ok"] is True
    assert by_type["complete"]


def test_local_skill_run_surfaces_the_llm_router_decision(tmp_path):
    from joe.route_classifier import RouteClassification
    from joe.web_runs import RunManager

    manager = RunManager(tmp_path)
    conversation = manager.conversations.create(None)
    classification = RouteClassification(
        intent="modify",
        action="create_skill",
        complexity="simple",
        workflow="fast",
        provider="codex",
        model_tier="light",
        effort="low",
        confidence=0.93,
        classifier_provider="gemini",
        classifier_model="gemini-2.5-flash",
        latency_ms=412,
    )

    run = manager.start_local_skill(
        "ajoute une compétence de revue",
        conversation["id"],
        name="revue",
        instructions="Vérifier les tests.",
        global_scope=False,
        classification=classification,
    )

    _await_run(run)
    route = next(event for event in run.events if event["type"] == "route")
    assert route["decided_by"] == "classifier"
    assert "routeur LLM" in route["reason"]
    assert route["routing_ms"] == 412
    assert route["classifier"]["classifier_model"] == "gemini-2.5-flash"
    assert route["classifier"]["confidence"] == 0.93

def _await_run(run, timeout: float = 10.0):
    """Une action locale suit désormais le cycle de vie asynchrone commun."""
    deadline = time.monotonic() + timeout
    with run.condition:
        while not run.done and time.monotonic() < deadline:
            run.condition.wait(timeout=0.1)
    assert run.done, "le run local ne s'est pas terminé"
    return run


def test_a_local_action_produces_a_run_summary_like_any_run(tmp_path):
    """Sans cela, la carte de pipeline disparaissait au rechargement."""
    from joe.web_runs import RunManager

    manager = RunManager(tmp_path)
    conversation = manager.conversations.create(None)
    run = manager.start_local_skill(
        "crée un skill resume pour ce projet",
        conversation["id"],
        name="resume",
        instructions="Vérifier les tests.",
        global_scope=False,
    )
    _await_run(run)

    messages = manager.conversations.get(conversation["id"])["messages"]
    assistant = messages[-1]
    assert assistant["provider"] == "joe"
    # Le résumé de run est ce que relit l'interface après un rechargement.
    assert assistant["run_summary"]
    assert assistant["run_summary"]["route"]["primary"] == "joe"
    # Et la tâche est close proprement, comme pour un run fournisseur.
    task = manager.tasks.get(run.run_id)
    assert task["status"] in {"completed", "review"}


def test_a_local_action_can_be_cancelled_and_leaves_no_pending(tmp_path):
    """L'ancien pipeline parallèle n'écrivait ni pending ni thread annulable."""
    from joe.web_runs import RunManager

    manager = RunManager(tmp_path)
    conversation = manager.conversations.create(None)
    run = manager.start_local_skill(
        "crée un skill jetable pour ce projet",
        conversation["id"],
        name="jetable",
        instructions="Rien de particulier.",
        global_scope=False,
    )
    _await_run(run)

    # Une action locale ne doit pas être rejouée comme un run fournisseur
    # après un redémarrage.
    assert manager._read_pending() == {}
    assert manager.get_run(run.run_id) is run


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("utf-8", "Écrire des tests ciblés.".encode("utf-8")),
        # Le Bloc-notes de Windows ajoutait une marque d'ordre des octets.
        ("utf-8 avec BOM", "Écrire des tests ciblés.".encode("utf-8-sig")),
        # Encodage par défaut des anciens éditeurs Windows.
        ("cp1252", "Écrire des tests ciblés.".encode("cp1252")),
    ],
)
def test_a_skill_keeps_its_accents_whatever_editor_saved_it(tmp_path, label, payload):
    """Un skill en français arrivait déformé chez tous les modèles sous Windows.

    Lu sans encodage, il était décodé selon la locale ; lu en UTF-8 strict, un
    fichier hérité en cp1252 aurait levé une erreur. La lecture accepte les
    trois formes qu'un utilisateur peut réellement produire.
    """
    skill = tmp_path / ".agentflow" / "skills" / "conventions" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(payload)

    content = skills.read_skill(tmp_path, "conventions")["content"]

    assert content == "Écrire des tests ciblés.", label


def test_promotion_strips_the_import_header_from_a_windows_file(tmp_path, monkeypatch):
    """Sous Windows, `write_text` enregistre des `\r\n` en fin de ligne.

    Relu octet par octet, l'en-tête d'import gardait ses `\r\n` et le motif
    ancré sur `\n` ne le retirait plus : il partait avec le skill promu.
    """
    global_root = tmp_path / "global-skills"
    monkeypatch.setattr(skills, "global_skills_root", lambda: global_root)
    project = tmp_path / "project"
    source = tmp_path / "rules"
    source.mkdir()
    (source / "SKILL.md").write_text("Toujours écrire des tests ciblés.", encoding="utf-8")
    import_skill(project, source, name="conventions")
    imported = project / ".agentflow" / "skills" / "conventions" / "SKILL.md"
    imported.write_bytes(imported.read_bytes().replace(b"\n", b"\r\n"))

    promoted = promote_skill(project, "conventions")

    assert Path(promoted["path"]).read_text(encoding="utf-8") == (
        "Toujours écrire des tests ciblés."
    )
