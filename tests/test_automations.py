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


def test_a_plan_can_start_one_minute_after_the_quota_window_reloads(
    tmp_path, monkeypatch
):
    """Départ calé sur le rechargement : l'échéance est résolue, pas devinée."""
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    plan = manager.create_automation(
        {
            "title": "Reprise après reset",
            "conversation_id": conversation["id"],
            "steps": ["Coder"],
            "start_mode": "quota_reset",
        }
    )
    assert manager.automations.get(plan["id"])["start_mode"] == "quota_reset"
    calls = []
    monkeypatch.setattr(
        manager, "start", lambda *a, **k: calls.append(a) or SimpleNamespace(
            run_id="run"
        )
    )

    # Tant que l'échéance n'est pas publiée, le plan attend au lieu de partir.
    monkeypatch.setattr("joe.web_runs.next_window_reset", lambda *a, **k: None)
    manager._advance_automations(now=time.time())
    assert manager.automations.get(plan["id"])["status"] == "waiting"
    assert not calls

    reset = time.time() + 4000
    monkeypatch.setattr("joe.web_runs.next_window_reset", lambda *a, **k: reset)
    manager._advance_automations(now=time.time())
    resolved = manager.automations.get(plan["id"])
    assert resolved["scheduled_for"] == reset + 60
    assert resolved["start_mode"] == "at"
    assert not calls

    manager._advance_automations(now=reset + 61)
    assert calls


def test_a_finished_plan_reports_back_into_the_conversation(tmp_path, monkeypatch):
    """Un travail autonome se termine par un compte rendu, comme un run normal."""
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    plan = manager.create_automation(
        {
            "title": "Campagne",
            "conversation_id": conversation["id"],
            "steps": ["Coder", "Tester"],
        }
    )
    manager.automations.update_step(plan["id"], 0, status="completed")
    manager.automations.update_step(
        plan["id"], 1, status="completed", attempts=3, error=None
    )
    manager.automations.update(plan["id"], current_step=2)

    manager._advance_automations(now=time.time() + 1)

    stored = manager.automations.get(plan["id"])
    assert stored["status"] == "completed"
    messages = manager.conversations.get(conversation["id"])["messages"]
    report = messages[-1]["content"]
    assert messages[-1]["role"] == "assistant"
    assert "Plan autonome terminé — Campagne" in report
    assert "2/2 étapes menées à leur terme" in report
    assert "3 tentatives" in report

    # Le rapport est publié une seule fois, même si le planificateur repasse.
    manager._advance_automations(now=time.time() + 2)
    assert manager.conversations.get(conversation["id"])["messages"] == messages


def test_an_unknown_start_mode_is_refused(tmp_path):
    import pytest

    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()

    with pytest.raises(ValueError):
        manager.create_automation(
            {
                "conversation_id": conversation["id"],
                "steps": ["Coder"],
                "start_mode": "un-jour-peut-etre",
            }
        )
