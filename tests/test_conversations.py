import json

import pytest

from joe.conversations import CURRENT_SCHEMA_VERSION, ConversationStore
from conftest import assert_mode


def test_conversation_messages_settings_and_pin_persist(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)

    conversation = store.create()
    store.append_message(conversation["id"], "user", "Analyse ce dépôt")
    store.append_message(conversation["id"], "assistant", "Terminé")
    store.update(
        conversation["id"],
        {
            "pinned": True,
            "title": "Conversation renommée",
            "settings": {"agent": "claude", "model": "sonnet"},
        },
    )

    loaded = store.get(conversation["id"])
    assert loaded["title"] == "Conversation renommée"
    assert loaded["pinned"] is True
    assert loaded["settings"]["agent"] == "claude"
    assert [message["role"] for message in loaded["messages"]] == [
        "user",
        "assistant",
    ]
    assert "Analyse ce dépôt" in store.context(conversation["id"])


def test_assistant_result_is_idempotent_per_run(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()

    store.append_message(
        conversation["id"], "assistant", "Première version", "run-1"
    )
    store.append_message(
        conversation["id"], "assistant", "Version finale", "run-1"
    )

    messages = store.get(conversation["id"])["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Version finale"


def test_conversations_default_to_pinned_then_most_recent(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    older = store.create()
    newer = store.create()
    store.update(older["id"], {"pinned": True})

    listed = store.list()

    assert listed[0]["id"] == older["id"]
    assert listed[1]["id"] == newer["id"]
    assert listed[0]["last_call_at"] == older["created_at"]


def test_conversation_summaries_do_not_include_message_bodies(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()
    store.append_message(conversation["id"], "user", "contenu volumineux")

    summary = store.list_summaries()[0]

    assert "messages" not in summary
    assert summary["message_count"] == 1


def test_unscoped_conversations_use_the_free_project(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)

    conversation = store.create()

    assert conversation["project_id"] == "free"
    assert store.get_project("free")["name"] == "Conversation libre"


def test_global_preferences_apply_only_to_new_conversations(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    existing = store.create()

    preferences = store.update_preferences(
        {"agent": "claude", "mode": "review"}
    )
    created = store.create()

    assert preferences == {"agent": "claude", "mode": "review"}
    assert store.get(existing["id"])["settings"]["agent"] == ""
    assert created["settings"]["agent"] == "claude"
    assert created["settings"]["mode"] == "review"


def test_projects_enable_quota_automation_by_default(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)

    project = store.create_project("Autonome")
    disabled = store.update_project(project["id"], {"quota_automation": False})

    assert project["quota_automation"] is True
    assert disabled["quota_automation"] is False


def test_project_can_reserve_a_quota_provider(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    project = store.create_project("Night jobs")

    updated = store.update_project(project["id"], {"quota_provider": "claude"})

    assert updated["quota_provider"] == "claude"
    assert store.update_project(project["id"], {"quota_provider": "invalid"})[
        "quota_provider"
    ] == ""


def test_invalid_global_preferences_fall_back_to_automatic(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)

    preferences = store.update_preferences(
        {"agent": "unknown", "mode": "expensive"}
    )

    assert preferences == {"agent": "", "mode": ""}


def test_manual_positions_and_project_collapse_persist(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    first_project = store.create_project("First")
    second_project = store.create_project("Second")
    store.update_project("main", {"position": 2})
    store.update_project(first_project["id"], {"position": 1})
    store.update_project(
        second_project["id"],
        {"position": 0, "collapsed": True},
    )
    first = store.create(first_project["id"])
    second = store.create(first_project["id"])
    store.update(first["id"], {"position": 1})
    store.update(second["id"], {"position": 0})

    custom_projects = [
        project for project in store.list_projects()
        if project["id"] != "free"
    ]
    assert custom_projects[0]["id"] == second_project["id"]
    assert custom_projects[0]["collapsed"] is True
    within_project = [
        item for item in store.list()
        if item["project_id"] == first_project["id"]
    ]
    assert [item["id"] for item in within_project] == [
        second["id"],
        first["id"],
    ]


def test_run_summary_persists_with_assistant_message(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()
    summary = {
        "route": {"mode": "review"},
        "workflow": [{"stage": "review", "status": "complete"}],
    }

    store.append_message(
        conversation["id"],
        "assistant",
        "Terminé",
        "run-1",
        run_summary=summary,
    )

    assert store.get(conversation["id"])["messages"][0]["run_summary"] == summary


def test_search_and_analytics_are_local_and_bounded(tmp_path):
    store = ConversationStore(tmp_path, tmp_path / "runs")
    store.ensure()
    conversation = store.create()
    store.append_message(conversation["id"], "user", "Analyse le worktree", "run-1")
    store.append_message(
        conversation["id"],
        "assistant",
        "Worktree isolé terminé",
        "run-1",
        run_summary={
            "route": {"mode": "fast", "intent": "analyze"},
            "attempts": [{
                "provider": "codex",
                "status": "complete",
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "cost_usd": 0.002,
                    "cost_status": "estimated_api",
                },
            }],
        },
    )
    results = store.search("worktree")
    assert results and results[0]["conversation_id"] == conversation["id"]
    report = store.analytics()
    assert report["total_runs"] == 1
    assert report["by_provider"]["codex"]["successes"] == 1
    assert report["runs"][0]["tokens"] == 150
    assert report["runs"][0]["cost_usd"] == 0.002
    assert report["runs"][0]["cost_statuses"] == ["estimated_api"]


def test_subproject_context_is_shared_by_its_conversations(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    project = store.create_project("Phase D")
    store.update_project(project["id"], {"context": "Utiliser uniquement H100."})
    store.update_project(
        project["id"],
        {
            "workspace_root": "/tmp/project",
            "additional_roots": ["/tmp/data"],
            "remote_access": True,
            "auto_commit_push": True,
            "default_execution_mode": "danger-full-access",
        },
    )
    first = store.create(project["id"])
    second = store.create(project["id"])

    assert first["project_id"] == project["id"]
    assert second["project_id"] == project["id"]
    assert "Utiliser uniquement H100." in store.context(first["id"])
    assert "Utiliser uniquement H100." in store.context(second["id"])
    loaded_project = store.get_project(project["id"])
    assert loaded_project["workspace_root"] == "/tmp/project"
    assert loaded_project["additional_roots"] == ["/tmp/data"]
    assert loaded_project["remote_access"] is True
    assert loaded_project["auto_commit_push"] is True
    assert loaded_project["default_execution_mode"] == "danger-full-access"


def test_project_context_is_preserved_when_history_is_truncated(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    project = store.create_project("Vision")
    store.update_project(project["id"], {"context": "RÈGLE_STABLE"})
    conversation = store.create(project["id"])
    for index in range(20):
        store.append_message(
            conversation["id"],
            "assistant",
            f"message-{index}-" + "x" * 500,
        )

    context = store.context(conversation["id"], limit=1000)

    assert "RÈGLE_STABLE" in context
    assert "message-19" in context
    assert len(context) <= 1000


def test_semantic_compaction_preserves_full_history_and_reduces_prompt(
    tmp_path,
):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()
    for index in range(12):
        store.append_message(
            conversation["id"],
            "assistant",
            f"old-{index}-" + "x" * 100,
        )

    candidate = store.compaction_candidate(
        conversation["id"],
        threshold_chars=200,
        keep_recent=4,
    )

    assert candidate is not None
    assert candidate["message_count"] == 8
    assert store.save_compaction(
        conversation["id"],
        "Décision compacte vérifiée.",
        candidate["message_count"],
    )
    loaded = store.get(conversation["id"])
    assert len(loaded["messages"]) == 12
    context = store.context(conversation["id"])
    assert "Décision compacte vérifiée." in context
    assert "old-11" in context
    assert "old-0" not in context


def test_context_marks_previous_answers_as_untrusted_history(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()
    store.append_message(
        conversation["id"],
        "assistant",
        "Les worktrees ne sont pas câblés.",
    )

    context = store.context(conversation["id"])

    assert "untrusted historical context" in context
    assert "Re-check mutable facts" in context
    assert "Les worktrees ne sont pas câblés." in context


def test_old_runs_are_imported_once(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    (runs / "one.json").write_text(
        json.dumps({"request": "Ancienne demande", "final": "Ancienne réponse"})
    )

    store = ConversationStore(root, runs)
    conversations = store.list()

    assert len(conversations) == 1
    assert conversations[0]["title"] == "Historique importé"
    assert len(conversations[0]["messages"]) == 2


def test_cancelled_run_messages_are_removed(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()
    store.append_message(conversation["id"], "user", "Prompt à modifier", "run-1")

    store.remove_run(conversation["id"], "run-1")

    assert store.get(conversation["id"])["messages"] == []


def test_conversation_can_be_deleted(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()

    assert store.delete(conversation["id"]) is True
    assert store.get(conversation["id"]) is None
    assert store.delete(conversation["id"]) is False


def test_assistant_completion_is_unread_until_conversation_is_opened(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()

    store.append_message(conversation["id"], "assistant", "Terminé")

    assert store.get(conversation["id"])["unread_completion"] is True
    store.update(conversation["id"], {"unread_completion": False})
    assert store.get(conversation["id"])["unread_completion"] is False


def test_history_recovers_from_atomic_backup(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    conversation = store.create()
    store.append_message(conversation["id"], "user", "Message préservé")
    store.path.write_text("{broken")

    recovered = ConversationStore(root, runs).get(conversation["id"])

    assert recovered["messages"][0]["content"] == "Message préservé"
    assert_mode(store.backup_path, 0o600)

    store.path.unlink()
    restored = ConversationStore(root, runs).get(conversation["id"])
    assert restored["messages"][0]["content"] == "Message préservé"


def test_old_history_is_migrated_with_a_pre_migration_snapshot(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    backup = tmp_path / "external" / "conversations.json"
    root.joinpath("conversations.json").write_text(
        json.dumps({"version": 2, "conversations": []})
    )

    store = ConversationStore(root, runs, backup_path=backup)
    store.ensure()

    assert json.loads(store.path.read_text())["version"] == CURRENT_SCHEMA_VERSION
    snapshots = list((backup.parent / "migrations").glob("*.json"))
    assert len(snapshots) == 1
    assert json.loads(snapshots[0].read_text())["version"] == 2


def test_newer_history_schema_is_never_overwritten(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    path = root / "conversations.json"
    path.write_text(
        json.dumps(
            {"version": CURRENT_SCHEMA_VERSION + 1, "conversations": []}
        )
    )

    with pytest.raises(RuntimeError, match="version plus récente"):
        ConversationStore(root, runs).ensure()

    assert json.loads(path.read_text())["version"] == CURRENT_SCHEMA_VERSION + 1


def test_one_daily_backup_is_kept_outside_the_project(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    backup = tmp_path / "external" / "conversations.json"
    store = ConversationStore(root, runs, backup_path=backup)

    store.create()
    store.create()

    daily = list((backup.parent / "daily").glob("*.json"))
    assert len(daily) == 1
    assert_mode(daily[0], 0o600)
def test_project_trash_is_reversible_and_never_deletes_workspace(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    project = store.create_project("À restaurer")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("safe", encoding="utf-8")
    store.update_project(project["id"], {"workspace_root": str(workspace)})
    conversation = store.create(project_id=project["id"])

    trashed = store.trash_project(project["id"])

    assert trashed["trashed_at"]
    assert store.get_project(project["id"]) is None
    assert project["id"] not in {item["id"] for item in store.list_projects()}
    assert store.list_trashed_projects()[0]["conversation_count"] == 1
    assert marker.read_text(encoding="utf-8") == "safe"

    restored = store.restore_project(project["id"])
    assert restored["trashed_at"] is None
    assert store.get(conversation["id"])["project_id"] == project["id"]


def test_permanent_project_delete_requires_trash(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    project = store.create_project("Suppression")
    conversation = store.create(project_id=project["id"])

    assert store.delete_project_permanently(project["id"]) is False
    store.trash_project(project["id"])
    assert store.delete_project_permanently(project["id"]) is True
    assert store.get(conversation["id"]) is None
    with pytest.raises(ValueError):
        store.trash_project("main")
