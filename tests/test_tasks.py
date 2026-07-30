from joe.tasks import TaskStore


def test_task_store_persists_and_updates_runs(tmp_path):
    store = TaskStore(tmp_path)
    store.ensure()
    task = store.create(
        "run1",
        "Implémente une tâche durable",
        "conversation1",
        "project1",
        workspace=tmp_path / "worktree",
        base_workspace=tmp_path,
        isolated=True,
        branch="joe/run1",
        base_commit="abc",
    )

    assert task["status"] == "running"
    updated = store.update(
        "run1",
        status="review",
        files=2,
        insertions=12,
        deletions=3,
    )
    assert updated["status"] == "review"
    assert TaskStore(tmp_path).get("run1")["files"] == 2
    assert store.delete("run1") is True
    assert store.get("run1") is None


def test_task_store_recovers_from_atomic_backup(tmp_path):
    store = TaskStore(tmp_path)
    store.ensure()
    store.create(
        "run1",
        "Conserver cette tâche",
        "conversation1",
        "project1",
        workspace=tmp_path,
        base_workspace=tmp_path,
        isolated=False,
    )
    store.update("run1", status="completed")
    store.path.write_text("{broken")

    assert TaskStore(tmp_path).get("run1")["id"] == "run1"
