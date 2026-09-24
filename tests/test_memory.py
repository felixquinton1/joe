import json
import os
import threading

from joe.memory import ProjectMemory, redact
from conftest import assert_mode


def test_memory_is_created_and_active_files_are_rewritten(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.ensure()
    assert (tmp_path / ".agentflow" / "config.yaml").exists()
    memory.update_active(
        request="first", provider="codex", mode="fast", response="old"
    )
    memory.update_active(
        request="second", provider="claude", mode="fast", response="new"
    )
    text = (tmp_path / ".agentflow" / "session.md").read_text(encoding="utf-8")
    assert "second" in text and "new" in text
    assert "first" not in text and "old" not in text
    assert memory.previous_provider() == "claude"


def test_context_is_bounded_and_includes_shared_project_instructions(tmp_path):
    (tmp_path / "AGENTS.md").write_text("important rule", encoding="utf-8")
    memory = ProjectMemory(tmp_path)
    memory.ensure()
    config_path = tmp_path / ".agentflow" / "config.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_context_chars"] = 180
    config_path.write_text(json.dumps(config), encoding="utf-8")
    context = memory.context("current request")
    assert len(context) <= 180
    assert "current request" in context


def test_context_includes_project_shared_skills_for_all_providers(tmp_path):
    skill = tmp_path / ".agentflow" / "skills" / "jz" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Use the canonical JZ wrapper.", encoding="utf-8")

    context = ProjectMemory(tmp_path).context("inspect the project")

    assert "Shared skill: jz/SKILL.md" in context
    assert "canonical JZ wrapper" in context


def test_context_includes_global_skills_shared_across_projects(tmp_path, monkeypatch):
    from joe import skills

    global_root = tmp_path / "global-skills"
    skill = global_root / "conventions" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Toujours écrire des tests ciblés.", encoding="utf-8")
    monkeypatch.setattr(skills, "global_skills_root", lambda: global_root)

    project = tmp_path / "project"
    context = ProjectMemory(project).context("inspect the project")

    assert "tests ciblés" in context


def test_context_keeps_progress_out_of_the_final_answer(tmp_path):
    memory = ProjectMemory(tmp_path)

    context = memory.context("organise la réponse")

    assert "Do not include a plan" in context
    assert "Ce que je vais faire" not in context
    assert "`Résultat :`" in context
    assert "self-contained" in context


def test_the_response_headings_follow_the_selected_language(tmp_path):
    """Les intertitres appartiennent à la réponse, donc à la langue choisie.

    Écrits en dur en français, ils pesaient plus lourd que la consigne de
    langue : une interface en anglais recevait « Ce que je vais faire : ».
    """
    memory = ProjectMemory(tmp_path)

    english = memory.context("organise the answer", "en")

    assert "Do not include a plan" in english
    assert "What I will do" not in english
    assert "`Result:`" in english
    assert "Ce que je vais faire" not in english
    assert "Résultat" not in english


def test_logs_redact_common_secret_shapes(tmp_path):
    memory = ProjectMemory(tmp_path)
    path = memory.save_run(
        "one", {"stderr": 'API_KEY="super-secret"', "nested": ["token:abc"]}
    )
    assert "super-secret" not in path.read_text(encoding="utf-8")
    assert "abc" not in path.read_text(encoding="utf-8")
    assert "[REDACTED]" in redact("token:abc")
    assert_mode(path, 0o600)
    assert_mode(memory.root, 0o700)


def test_active_memory_redacts_request(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.update_active(
        request="use token:private-value",
        provider="codex",
        mode="fast",
        response="done",
    )
    assert "private-value" not in (memory.root / "session.md").read_text(encoding="utf-8")


def test_project_memory_gitignore_separates_shared_and_local_state(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.ensure()

    rules = (memory.root / ".gitignore").read_text(encoding="utf-8")
    assert "conversations.json" in rules
    assert "runs/" in rules
    assert "session.md" in rules
    assert "project.md" not in rules
    assert "config.yaml" not in rules


def test_run_retention_removes_old_and_excess_logs(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.ensure()
    config_path = memory.root / "config.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["run_retention"] = {
        "max_runs": 2,
        "max_age_days": 1,
        "max_total_mb": 10,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    old = memory.runs / "old.json"
    old.write_text("{}", encoding="utf-8")
    os.utime(old, (1, 1))
    memory.save_run("new-1", {"final": "one"})
    memory.save_run("new-2", {"final": "two"})

    assert not old.exists()
    assert sorted(path.stem for path in memory.runs.glob("*.json")) == [
        "new-1",
        "new-2",
    ]


def test_concurrent_active_updates_keep_session_and_handoff_together(tmp_path):
    memories = [ProjectMemory(tmp_path), ProjectMemory(tmp_path)]
    barrier = threading.Barrier(2)

    def update(memory, marker):
        barrier.wait()
        memory.update_active(
            request=marker,
            provider="codex",
            mode="fast",
            response=f"response-{marker}",
        )

    threads = [
        threading.Thread(target=update, args=(memory, marker))
        for memory, marker in zip(memories, ("alpha", "beta"))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    session = (memories[0].root / "session.md").read_text(encoding="utf-8")
    handoff = (memories[0].root / "handoff.md").read_text(encoding="utf-8")
    final_marker = "alpha" if "Objective: alpha" in session else "beta"
    assert f"Request: {final_marker}" in handoff


def test_an_older_ignore_list_is_completed_without_losing_user_lines(tmp_path):
    """Le fichier n'était écrit qu'à la création du dossier.

    Une installation antérieure à l'ajout d'une entrée gardait l'ancienne
    liste : les campagnes et les automatisations, qui portent le contenu de
    l'utilisateur, partaient sans prévenir dans son dépôt.
    """
    root = tmp_path / ".agentflow"
    root.mkdir()
    (root / ".gitignore").write_text(
        "conversations.json\nruns/\nmes-notes-perso.md\n", encoding="utf-8"
    )

    ProjectMemory(tmp_path).ensure()

    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "autonomous.json" in lines
    assert "automations.json" in lines
    assert "backups/" in lines
    # Ce que l'utilisateur avait ajouté lui-même reste en place.
    assert "mes-notes-perso.md" in lines
    assert lines.count("conversations.json") == 1
