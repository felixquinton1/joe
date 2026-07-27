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
        {"pinned": True, "settings": {"agent": "claude", "model": "sonnet"}},
    )

    loaded = store.get(conversation["id"])
    assert loaded["title"] == "Analyse ce dépôt"
    assert loaded["pinned"] is True
    assert loaded["settings"]["agent"] == "claude"
    assert [message["role"] for message in loaded["messages"]] == [
        "user",
        "assistant",
    ]
    assert "Analyse ce dépôt" in store.context(conversation["id"])


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
