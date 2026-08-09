from __future__ import annotations

from enum import StrEnum
from typing import Any


class AutonomousState(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    RESEARCHING = "researching"
    IMPLEMENTING = "implementing"
    EXPERIMENTING = "experimenting"
    EVALUATING = "evaluating"
    CHECKPOINTING = "checkpointing"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


TERMINAL_STATES = {
    AutonomousState.COMPLETED,
    AutonomousState.BLOCKED,
    AutonomousState.CANCELLED,
}

ALLOWED_TRANSITIONS: dict[AutonomousState, set[AutonomousState]] = {
    AutonomousState.PREPARING: {
        AutonomousState.READY, AutonomousState.COMPLETED,
        AutonomousState.BLOCKED, AutonomousState.CANCELLED,
    },
    AutonomousState.READY: {
        AutonomousState.RESEARCHING, AutonomousState.IMPLEMENTING,
        AutonomousState.EXPERIMENTING, AutonomousState.PAUSED,
        AutonomousState.COMPLETED, AutonomousState.BLOCKED, AutonomousState.CANCELLED,
    },
    AutonomousState.RESEARCHING: {
        AutonomousState.READY, AutonomousState.PAUSED,
        AutonomousState.COMPLETED, AutonomousState.BLOCKED, AutonomousState.CANCELLED,
    },
    AutonomousState.IMPLEMENTING: {
        AutonomousState.READY, AutonomousState.PAUSED,
        AutonomousState.COMPLETED, AutonomousState.BLOCKED, AutonomousState.CANCELLED,
    },
    AutonomousState.EXPERIMENTING: {
        AutonomousState.EVALUATING, AutonomousState.PAUSED,
        AutonomousState.COMPLETED, AutonomousState.BLOCKED, AutonomousState.CANCELLED,
    },
    AutonomousState.EVALUATING: {
        AutonomousState.CHECKPOINTING, AutonomousState.PAUSED,
        AutonomousState.COMPLETED, AutonomousState.BLOCKED, AutonomousState.CANCELLED,
    },
    AutonomousState.CHECKPOINTING: {
        AutonomousState.READY, AutonomousState.COMPLETED,
        AutonomousState.PAUSED, AutonomousState.BLOCKED, AutonomousState.CANCELLED,
    },
    AutonomousState.PAUSED: {
        AutonomousState.READY, AutonomousState.BLOCKED, AutonomousState.CANCELLED,
    },
    # A terminal campaign can only leave its state through an explicit user resume.
    AutonomousState.COMPLETED: {AutonomousState.READY},
    AutonomousState.BLOCKED: {AutonomousState.READY},
    AutonomousState.CANCELLED: {AutonomousState.READY},
}


class InvalidAutonomousTransition(ValueError):
    pass


def validate_transition(current: AutonomousState, target: AutonomousState) -> None:
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidAutonomousTransition(
            f"Transition Autonomous interdite : {current.value} -> {target.value}."
        )


def infer_state(campaign: dict[str, Any]) -> AutonomousState:
    value = campaign.get("state")
    if value:
        try:
            return AutonomousState(str(value))
        except ValueError:
            pass
    status = str(campaign.get("status", "scheduled"))
    phase = str(campaign.get("phase", "research"))
    if status in {"completed", "cancelled", "blocked", "paused"}:
        return AutonomousState(status)
    if status == "researching":
        return AutonomousState.RESEARCHING
    if status == "planning":
        return AutonomousState.IMPLEMENTING
    if status == "experimenting":
        return AutonomousState.EXPERIMENTING
    if status == "evaluating":
        return (
            AutonomousState.CHECKPOINTING
            if phase == "checkpointing" else AutonomousState.EVALUATING
        )
    return AutonomousState.READY


def legacy_fields(state: AutonomousState, phase: str | None = None) -> dict[str, str]:
    if state == AutonomousState.READY:
        return {"status": "scheduled", "phase": phase or "planning"}
    if state == AutonomousState.RESEARCHING:
        return {"status": "researching", "phase": "research"}
    if state == AutonomousState.IMPLEMENTING:
        return {"status": "planning", "phase": "planning"}
    if state == AutonomousState.EXPERIMENTING:
        return {"status": "experimenting", "phase": "experiment"}
    if state == AutonomousState.EVALUATING:
        return {"status": "evaluating", "phase": "evaluation"}
    if state == AutonomousState.CHECKPOINTING:
        return {"status": "evaluating", "phase": "checkpointing"}
    if state == AutonomousState.PAUSED:
        return {"status": "paused", "phase": phase or "planning"}
    if state == AutonomousState.PREPARING:
        return {"status": "scheduled", "phase": "preparing"}
    return {"status": state.value, "phase": "done" if state == AutonomousState.COMPLETED else (phase or "planning")}
