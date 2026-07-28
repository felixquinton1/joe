import json

from joe.conversations import ConversationStore


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

    assert store.list_projects()[0]["id"] == second_project["id"]
    assert store.list_projects()[0]["collapsed"] is True
    within_project = [
        item for item in store.list()
        if item["project_id"] == first_project["id"]
    ]
    assert [item["id"] for item in within_project] == [
        second["id"],
        first["id"],
    ]


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
    assert store.backup_path.stat().st_mode & 0o777 == 0o600

    store.path.unlink()
    restored = ConversationStore(root, runs).get(conversation["id"])
    assert restored["messages"][0]["content"] == "Message préservé"
