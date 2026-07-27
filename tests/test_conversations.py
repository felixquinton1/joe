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


def test_subproject_context_is_shared_by_its_conversations(tmp_path):
    root = tmp_path / ".agentflow"
    runs = root / "runs"
    runs.mkdir(parents=True)
    store = ConversationStore(root, runs)
    project = store.create_project("Phase D")
    store.update_project(project["id"], {"context": "Utiliser uniquement H100."})
    first = store.create(project["id"])
    second = store.create(project["id"])

    assert first["project_id"] == project["id"]
    assert second["project_id"] == project["id"]
    assert "Utiliser uniquement H100." in store.context(first["id"])
    assert "Utiliser uniquement H100." in store.context(second["id"])


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
