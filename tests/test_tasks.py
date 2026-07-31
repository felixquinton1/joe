import time

from joe.tasks import TaskStore
from joe.web_runs import LiveRun, RunManager, _task_pipeline
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


def test_quota_wait_is_persisted_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    refreshed = []
    monkeypatch.setattr(
        "joe.web_runs.usage_status",
        lambda force=False: refreshed.append(force) or [],
    )
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    run = LiveRun("quota-run", "Long travail", conversation["id"])
    manager.tasks.create(
        run.run_id,
        run.request,
        conversation["id"],
        conversation["project_id"],
        workspace=tmp_path,
        base_workspace=tmp_path,
        isolated=False,
    )
    manager._write_pending(run, None, None, None, None, None)

    manager._wait_for_quota_window(
        run,
        time.time() + 0.02,
        "Quota Claude épuisé",
    )

    assert refreshed == [True]
    assert manager.tasks.get(run.run_id)["status"] == "running"
    assert manager.tasks.get(run.run_id)["scheduled_for"] is None
    assert manager._read_pending()[run.run_id]["not_before"] is None
    assert any(event["type"] == "quota_scheduled" for event in run.events)


def test_waiting_quota_pipeline_is_visible():
    pipeline = _task_pipeline({"status": "waiting_quota"})

    assert pipeline[1]["status"] == "waiting"


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


def test_task_pipeline_exposes_delivery_progress():
    review = _task_pipeline({"status": "review", "files": 2})
    integrated = _task_pipeline({"status": "integrated", "files": 2})

    assert [stage["status"] for stage in review] == [
        "complete",
        "complete",
        "complete",
        "waiting",
        "pending",
    ]
    assert integrated[-1]["status"] == "complete"


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
