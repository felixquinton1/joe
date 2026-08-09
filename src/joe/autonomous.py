from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .autonomous_schedule import normalize_schedule
from .autonomous_state import (
    AutonomousState,
    infer_state,
    legacy_fields,
    validate_transition,
)


TERMINAL_STATUSES = {"completed", "cancelled", "blocked"}
STATUSES = TERMINAL_STATUSES | {
    "scheduled", "paused", "researching", "planning", "experimenting", "evaluating"
}


def build_autonomous_skill(values: dict[str, Any]) -> str:
    """Build the immutable campaign charter injected into every autonomous turn."""
    return (
        "# Skill — Autonomous campaign charter\n\n"
        "This charter is authoritative for the whole campaign. Re-read it before "
        "every decision, tool call, implementation, experiment analysis and resume.\n\n"
        f"## Primary objective\n{values.get('objective') or 'Not specified.'}\n\n"
        f"## Durable context and constraints\n{values.get('campaign_context') or 'None.'}\n\n"
        f"## Research protocol\n{values.get('research_protocol') or 'Use relevant public documentation.'}\n\n"
        f"## Data policy\n{values.get('data_policy') or 'Do not disclose private data.'}\n\n"
        f"## Resource policy\n{json.dumps(values.get('resource_policy') or {'mode': 'auto'}, ensure_ascii=False)}\n\n"
        f"## Model-call and token budget\n{json.dumps(values.get('token_budget') or {}, ensure_ascii=False)}\n\n"
        "## Invariants\n"
        "- Never replace, weaken or silently reinterpret the primary objective.\n"
        "- Optimize for the best rigorously validated primary metric achievable within the remaining wall-clock, compute and model-token budgets; activity alone is not progress.\n"
        "- Before acting, verify that the next step directly advances the objective.\n"
        "- Treat previous summaries as fallible; check the repository and structured results.\n"
        "- Distinguish verified facts, hypotheses, implemented changes and measured results.\n"
        "- Use the configured local runner for main experiments; detect completion or crash.\n"
        "- Use progressive experimental scale: smoke test, intermediate validation, then full runs once stable.\n"
        "- Scale run duration and model size to the verified hardware, active window and remaining budget.\n"
        "- Inspect available CPU, RAM and accelerators at startup, then obey the resource policy exactly. When automatic selection is allowed and an accelerator is relevant, benchmark at least one suitable accelerated approach instead of leaving it idle without a measured reason.\n"
        "- Keep simple baselines short: use them to validate data, splits and metrics, then move promptly to the model families most likely to win. Do not exhaustively tune a clearly capacity-limited baseline.\n"
        "- Distinguish reuse of a public architecture from reuse of pretrained weights: an architecture can be implemented and trained locally while weight provenance and licensing are audited separately.\n"
        "- Minimize model calls. Batch related diagnosis, implementation and short tests into one coherent turn, and make the local runner evaluate a checkpointed batch of informative variants when safe.\n"
        "- Parallelize independent preparation or experiments only when resources, data isolation and metric validity remain controlled; avoid GPU oversubscription.\n"
        "- Prefer fewer high-information experiments over many tiny prompts or low-impact tweaks. Timebox plumbing and repeated failures, then change strategy.\n"
        "- Long campaigns must perform substantive runs; do not remain indefinitely in toy-test mode.\n"
        "- Revisit public literature whenever results plateau, contradict assumptions, reveal uncertainty or repeat failures.\n"
        "- Research refreshes are event-driven by new evidence; do not spend a model call on a periodic refresh without a decision it can change.\n"
        "- Preserve resumable checkpoints and the Git history after coherent changes.\n"
        "- Respect the data policy and all explicit prohibitions for every iteration.\n"
        "- Report meaningful transitions: step start, experiment start, result, analysis and next decision.\n"
        "- If a proposed action conflicts with this charter, do not perform it; explain the conflict.\n"
    )[:30000]


class AutonomousStore:
    """Durable state for bounded research/experiment campaigns."""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "autonomous.json"
        self.backup_path = root / "autonomous.json.bak"
        self.lock = threading.RLock()

    def ensure(self) -> None:
        with self.lock:
            if not self.path.exists():
                self._write({"version": 1, "campaigns": []})
            else:
                self._read()

    def create(self, **values: Any) -> dict[str, Any]:
        objective = " ".join(str(values.get("objective", "")).split()).strip()
        command = values.get("command")
        resume_command = values.get("resume_command") or []
        if not objective:
            raise ValueError("Décris l'objectif de la campagne.")
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ValueError("La commande d'expérience doit être une liste non vide.")
        if not isinstance(resume_command, list) or not all(
            isinstance(part, str) and part for part in resume_command
        ):
            raise ValueError("La commande de reprise Autonomous est invalide.")
        schedule = normalize_schedule(values.get("schedule"))
        checkpoint_path = str(values.get("checkpoint_path", ""))
        if schedule["windows"] and (not resume_command or not checkpoint_path):
            raise ValueError(
                "Une campagne programmée exige un checkpoint et une commande de reprise."
            )
        now = time.time()
        campaign = {
            "id": uuid.uuid4().hex,
            "title": str(values.get("title", "")).strip()[:100] or objective[:100],
            "project_id": str(values["project_id"]),
            "conversation_id": str(values["conversation_id"]),
            "objective": objective[:8000],
            "research_protocol": str(values.get("research_protocol", ""))[:8000],
            "data_policy": str(values.get("data_policy", ""))[:4000],
            "campaign_context": str(values.get("campaign_context", ""))[:12000],
            "research_refresh_interval": max(
                0, min(20, int(values.get("research_refresh_interval", 0)))
            ),
            "command": command[:32],
            "working_directory": str(values.get("working_directory", ".")),
            "metrics_path": str(values.get("metrics_path", "metrics.json")),
            "metric_name": str(values.get("metric_name", "score"))[:100],
            "metric_direction": "min" if values.get("metric_direction") == "min" else "max",
            "timeout_seconds": max(5, min(86400, int(values.get("timeout_seconds", 600)))),
            "max_iterations": max(1, min(50, int(values.get("max_iterations", 3)))),
            "iteration_chunk": max(1, min(50, int(values.get("max_iterations", 3)))),
            "max_duration_seconds": max(
                60, min(604800, int(values.get("max_duration_seconds", 3600)))
            ),
            "restricted_data": bool(values.get("restricted_data", False)),
            "preflight": dict(values.get("preflight") or {}),
            "preflight_command": list(values.get("preflight_command") or [])[:32],
            "preflight_metrics_path": str(
                values.get("preflight_metrics_path", "artifacts/preflight.json")
            ),
            "preflight_timeout_seconds": max(
                5, min(1800, int(values.get("preflight_timeout_seconds", 300)))
            ),
            "resource_policy": dict(values.get("resource_policy") or {"mode": "auto"}),
            "token_budget": dict(values.get("token_budget") or {}),
            "schedule": schedule,
            "resume_command": resume_command[:32],
            "checkpoint_path": checkpoint_path,
            "stop_signal_path": str(
                values.get("stop_signal_path", "artifacts/STOP_REQUESTED")
            ),
            "stop_grace_seconds": max(
                1, min(120, int(values.get("stop_grace_seconds", 30)))
            ),
            "mode": (
                str(values.get("mode"))
                if str(values.get("mode")) in {"fast", "review", "consensus"}
                else "review"
            ),
            "execution_mode": str(values.get("execution_mode", "workspace-write")),
            "status": "scheduled",
            "phase": "preparing",
            "state": AutonomousState.PREPARING.value,
            "state_history": [],
            "iteration": 0,
            "current_run_id": None,
            "current_experiment_id": None,
            "started_at": None,
            "deadline_at": None,
            "active_elapsed_seconds": 0.0,
            "active_window_started_at": None,
            "next_start_at": None,
            "best_metric": None,
            "history": [],
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        campaign["autonomous_skill"] = build_autonomous_skill(campaign)
        campaign["skill_path"] = f"autonomous/{campaign['id']}/SKILL.md"
        with self.lock:
            payload = self._read()
            payload["campaigns"].append(campaign)
            self._write(payload)
        skill_path = self.root / str(campaign["skill_path"])
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(campaign["autonomous_skill"], encoding="utf-8")
        return dict(campaign)

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted(
                (dict(item) for item in self._read()["campaigns"]),
                key=lambda item: float(item.get("updated_at", 0)), reverse=True,
            )

    def get(self, campaign_id: str) -> dict[str, Any] | None:
        with self.lock:
            item = self._find(self._read(), campaign_id)
            return dict(item) if item else None

    def update(self, campaign_id: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {
            "status", "phase", "iteration", "current_run_id",
            "current_experiment_id", "best_metric", "history", "error",
            "started_at", "deadline_at",
            "active_elapsed_seconds", "active_window_started_at", "next_start_at",
            "max_iterations", "resume_count", "resumed_at",
            "state", "state_history",
        }
        with self.lock:
            payload = self._read()
            item = self._find(payload, campaign_id)
            if not item:
                return None
            item.update({key: value for key, value in changes.items() if key in allowed})
            if item.get("status") not in STATUSES:
                item["status"] = "blocked"
            item["updated_at"] = time.time()
            self._write(payload)
            return dict(item)

    def transition(
        self,
        campaign_id: str,
        target: AutonomousState | str,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
        phase: str | None = None,
        **changes: Any,
    ) -> dict[str, Any] | None:
        """Atomically validate, journal and apply one canonical state transition."""
        target_state = AutonomousState(str(target))
        with self.lock:
            payload = self._read()
            item = self._find(payload, campaign_id)
            if not item:
                return None
            current = infer_state(item)
            validate_transition(current, target_state)
            history = list(item.get("state_history") or [])
            if current != target_state:
                history.append({
                    "at": time.time(),
                    "from": current.value,
                    "to": target_state.value,
                    "reason": str(reason)[:500],
                    "metadata": dict(metadata or {}),
                })
            item.update(legacy_fields(target_state, phase))
            item["state"] = target_state.value
            item["state_history"] = history[-200:]
            allowed = {
                "iteration", "current_run_id", "current_experiment_id",
                "best_metric", "error", "started_at", "deadline_at",
                "active_elapsed_seconds", "active_window_started_at", "next_start_at",
                "max_iterations", "resume_count", "resumed_at",
                "preflight", "resource_policy",
                "token_budget",
            }
            item.update({key: value for key, value in changes.items() if key in allowed})
            item["updated_at"] = time.time()
            self._write(payload)
            return dict(item)

    def add_event(self, campaign_id: str, kind: str, details: dict[str, Any]) -> None:
        item = self.get(campaign_id)
        if not item:
            return
        history = list(item.get("history") or [])
        history.append({"at": time.time(), "kind": kind, **details})
        self.update(campaign_id, history=history[-1000:])

    def cancel(self, campaign_id: str) -> dict[str, Any] | None:
        item = self.get(campaign_id)
        if not item:
            return None
        if infer_state(item) == AutonomousState.CANCELLED:
            return item
        return self.transition(
            campaign_id, AutonomousState.CANCELLED, "user_cancelled", error=None
        )

    def delete(self, campaign_id: str) -> bool:
        with self.lock:
            payload = self._read()
            before = len(payload["campaigns"])
            payload["campaigns"] = [
                item for item in payload["campaigns"] if item.get("id") != campaign_id
            ]
            if len(payload["campaigns"]) == before:
                return False
            self._write(payload)
        shutil.rmtree(self.root / "autonomous" / campaign_id, ignore_errors=True)
        return True

    def resume(self, campaign_id: str) -> dict[str, Any] | None:
        item = self.get(campaign_id)
        if not item:
            return None
        if item.get("status") not in TERMINAL_STATUSES:
            raise ValueError("Cette campagne est déjà active.")
        iteration = int(item.get("iteration", 0))
        maximum = int(item.get("max_iterations", 1))
        chunk = int(item.get("iteration_chunk", maximum or 1))
        if iteration >= maximum:
            maximum = iteration + max(1, chunk)
        history = item.get("history") or []
        previous = next(
            (event for event in reversed(history) if event.get("kind") == "experiment"),
            {},
        )
        phase = (
            "experiment"
            if previous.get("status") == "interrupted"
            and previous.get("checkpoint_available")
            and item.get("resume_command")
            else "planning"
        )
        return self.transition(
            campaign_id,
            AutonomousState.READY,
            "user_resumed",
            phase=phase,
            error=None,
            current_run_id=None,
            current_experiment_id=None,
            active_elapsed_seconds=0.0,
            active_window_started_at=None,
            next_start_at=None,
            started_at=None,
            max_iterations=maximum,
            resume_count=int(item.get("resume_count", 0)) + 1,
            resumed_at=time.time(),
        )

    def _read(self) -> dict[str, Any]:
        for path in (self.path, self.backup_path):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("campaigns"), list):
                return payload
        return {"version": 1, "campaigns": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.path.exists():
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(current, dict) and isinstance(current.get("campaigns"), list):
                    shutil.copy2(self.path, self.backup_path)
            except (OSError, json.JSONDecodeError):
                pass
        os.replace(temporary, self.path)

    @staticmethod
    def _find(payload: dict[str, Any], campaign_id: str) -> dict[str, Any] | None:
        return next((item for item in payload["campaigns"] if item.get("id") == campaign_id), None)
