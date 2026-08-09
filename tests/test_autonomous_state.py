from pathlib import Path

import pytest

from joe.autonomous import AutonomousStore
from joe.autonomous_state import (
    AutonomousState,
    InvalidAutonomousTransition,
    infer_state,
    validate_transition,
)


def _campaign() -> dict:
    return {
        "title": "State machine",
        "project_id": "p1",
        "conversation_id": "c1",
        "objective": "Test durable transitions",
        "command": ["python", "experiment.py"],
    }


def test_state_machine_rejects_impossible_transition() -> None:
    with pytest.raises(InvalidAutonomousTransition):
        validate_transition(AutonomousState.EXPERIMENTING, AutonomousState.RESEARCHING)


def test_store_transitions_are_atomic_journaled_and_legacy_compatible(tmp_path: Path) -> None:
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**_campaign())

    researching = store.transition(
        campaign["id"], AutonomousState.RESEARCHING, "research_started",
        current_run_id="run-1",
    )
    ready = store.transition(
        campaign["id"], AutonomousState.READY, "research_finished",
        phase="planning", current_run_id=None,
    )

    assert researching["state"] == "researching"
    assert researching["status"] == "researching"
    assert ready["state"] == "ready"
    assert ready["status"] == "scheduled"
    assert [event["reason"] for event in ready["state_history"][-2:]] == [
        "research_started", "research_finished",
    ]


def test_legacy_campaign_state_is_inferred() -> None:
    assert infer_state({"status": "planning", "phase": "planning"}) == AutonomousState.IMPLEMENTING
    assert infer_state({"status": "evaluating", "phase": "checkpointing"}) == AutonomousState.CHECKPOINTING
