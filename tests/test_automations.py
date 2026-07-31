import time
from types import SimpleNamespace

from joe.automations import AutomationStore
from joe.auth import required_role
from joe.web_runs import RunManager


def test_automation_store_persists_bounded_sequential_plan(tmp_path):
    store = AutomationStore(tmp_path)
    store.ensure()
    plan = store.create(
        title="Travail nocturne",
        project_id="main",
        conversation_id="conversation",
        steps=["Coder", "Tester", "Corriger"],
        scheduled_for=time.time() + 3600,
        max_retries=9,
    )

    persisted = AutomationStore(tmp_path).get(plan["id"])

    assert persisted["status"] == "scheduled"
    assert [step["prompt"] for step in persisted["steps"]] == [
        "Coder", "Tester", "Corriger"
    ]
    assert persisted["max_retries"] == 5


def test_automation_mutations_require_maintainer():
    assert required_role("GET", "/api/automations") == "viewer"
    assert required_role("POST", "/api/automations") == "maintainer"
    assert required_role("POST", "/api/automations/plan/cancel") == "maintainer"


def test_automation_store_updates_steps_and_cancels(tmp_path):
    store = AutomationStore(tmp_path)
    store.ensure()
    plan = store.create(
        title="Plan",
        project_id="main",
        conversation_id="conversation",
        steps=["Tester"],
        scheduled_for=time.time(),
    )

    store.update_step(plan["id"], 0, status="running", attempts=1, run_id="run1")
    cancelled = store.cancel(plan["id"])

    assert cancelled["status"] == "cancelled"
    assert cancelled["steps"][0]["run_id"] == "run1"


def test_scheduler_starts_only_the_current_step(tmp_path, monkeypatch):
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    scheduled_for = time.time() + 3600
    plan = manager.create_automation(
        {
            "title": "Plan borné",
            "conversation_id": conversation["id"],
            "steps": ["Coder", "Tester"],
            "scheduled_for": scheduled_for,
            "execution_mode": "workspace-write",
        }
    )
    calls = []

    def fake_start(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(run_id="scheduledrun")

    monkeypatch.setattr(manager, "start", fake_start)

    manager._advance_automations(now=scheduled_for + 1)

    updated = manager.automations.get(plan["id"])
    assert len(calls) == 1
    assert "étape 1/2" in calls[0][0][0]
    assert updated["current_run_id"] == "scheduledrun"
    assert updated["steps"][0]["attempts"] == 1
    assert updated["steps"][1]["status"] == "pending"
