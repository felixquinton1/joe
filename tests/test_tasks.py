import time

from joe.tasks import TaskStore
from joe.web_runs import RunManager
from joe.worktrees import WorktreeError, WorktreeManager


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


def test_run_manager_integrates_task_in_background(tmp_path, monkeypatch):
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    manager.tasks.create(
        "run1",
        "Intègre la tâche",
        conversation["id"],
        conversation["project_id"],
        workspace=tmp_path / "worktree",
        base_workspace=tmp_path,
        isolated=True,
        branch="joe/run1",
        base_commit="abc",
    )
    manager.tasks.update("run1", status="review")
    monkeypatch.setattr(
        WorktreeManager,
        "integrate",
        lambda self, worktree, message, resolver=None: "f" * 40,
    )

    accepted = manager.integrate_task("run1")
    assert accepted["status"] == "integrating"
    for _ in range(100):
        task = manager.tasks.get("run1")
        if task["status"] == "integrated":
            break
        time.sleep(0.01)

    assert task["integrated_commit"] == "f" * 40
    assert task["workspace"] is None


def test_run_manager_keeps_conflicted_task_retryable(tmp_path, monkeypatch):
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    manager.tasks.create(
        "run1",
        "Préserve les deux changements",
        conversation["id"],
        conversation["project_id"],
        workspace=tmp_path / "worktree",
        base_workspace=tmp_path,
        isolated=True,
        branch="joe/run1",
        base_commit="abc",
    )
    manager.tasks.update("run1", status="review")

    def fail(self, worktree, message, resolver=None):
        raise WorktreeError("Conflit conservé")

    monkeypatch.setattr(WorktreeManager, "integrate", fail)
    manager.integrate_task("run1")
    for _ in range(100):
        task = manager.tasks.get("run1")
        if task["status"] == "conflict":
            break
        time.sleep(0.01)

    assert task["error"] == "Conflit conservé"
    assert task["workspace"].endswith("worktree")
