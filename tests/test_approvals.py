from joe.approvals import ApprovalStore


def test_approval_survives_reload_and_is_consumed_once(tmp_path):
    store = ApprovalStore(tmp_path)
    store.ensure()
    item = store.create(
        "full-access",
        "conversation",
        "project",
        {"request": "Implement", "conversation_id": "conversation"},
        "Permission required",
    )

    reloaded = ApprovalStore(tmp_path)
    assert reloaded.list("pending")[0]["id"] == item["id"]
    assert reloaded.decide(item["id"], "approved")["status"] == "approved"
    assert reloaded.consume(item["id"], "conversation", "Implement") is True
    assert reloaded.consume(item["id"], "conversation", "Implement") is False


def test_refused_approval_cannot_be_consumed(tmp_path):
    store = ApprovalStore(tmp_path)
    store.ensure()
    item = store.create(
        "full-access",
        "conversation",
        "project",
        {"request": "Implement"},
        "Permission required",
    )

    store.decide(item["id"], "refused")

    assert store.list("pending") == []
    assert store.consume(item["id"], "conversation", "Implement") is False
