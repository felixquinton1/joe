from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any


PLAN_STATUSES = {
    "scheduled", "running", "waiting", "blocked", "completed", "cancelled"
}


START_MODES = {"at", "quota_reset"}


class AutomationStore:
    """Durable, deliberately small sequential automation plans."""

    def __init__(self, root: Path):
        self.path = root / "automations.json"
        self.backup_path = root / "automations.json.bak"
        self.lock = threading.RLock()

    def ensure(self) -> None:
        with self.lock:
            if not self.path.exists():
                self._write({"version": 1, "plans": []})
            else:
                self._read()

    def create(
        self,
        *,
        title: str,
        project_id: str,
        conversation_id: str,
        steps: list[str],
        scheduled_for: float,
        start_mode: str = "at",
        provider: str = "",
        mode: str = "review",
        execution_mode: str = "workspace-write",
        max_retries: int = 2,
        auto_integrate: bool = True,
    ) -> dict[str, Any]:
        clean_steps = [" ".join(str(step).split()).strip() for step in steps]
        clean_steps = [step for step in clean_steps if step][:24]
        if not clean_steps:
            raise ValueError("Ajoute au moins une étape au plan.")
        now = time.time()
        plan = {
            "id": uuid.uuid4().hex,
            "title": title.strip()[:80] or clean_steps[0][:80],
            "project_id": project_id,
            "conversation_id": conversation_id,
            "status": "scheduled",
            "scheduled_for": max(now, float(scheduled_for)),
            # « quota_reset » ne peut pas être converti en date à la création :
            # l'échéance n'est parfois pas encore exposée. Le planificateur la
            # résout quand elle apparaît.
            "start_mode": start_mode if start_mode in START_MODES else "at",
            "provider": provider,
            "mode": mode if mode in {"fast", "review", "consensus"} else "review",
            "execution_mode": execution_mode,
            "max_retries": max(0, min(5, int(max_retries))),
            "auto_integrate": bool(auto_integrate),
            "current_step": 0,
            "current_run_id": None,
            "error": None,
            "steps": [
                {"id": index + 1, "prompt": prompt, "status": "pending", "attempts": 0}
                for index, prompt in enumerate(clean_steps)
            ],
            "report": None,
            "created_at": now,
            "updated_at": now,
        }
        with self.lock:
            payload = self._read()
            payload["plans"].append(plan)
            self._write(payload)
        return dict(plan)

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted(
                (dict(plan) for plan in self._read()["plans"]),
                key=lambda plan: float(plan.get("updated_at", 0)),
                reverse=True,
            )

    def get(self, plan_id: str) -> dict[str, Any] | None:
        with self.lock:
            plan = self._find(self._read(), plan_id)
            return dict(plan) if plan else None

    def update(self, plan_id: str, **changes: Any) -> dict[str, Any] | None:
        with self.lock:
            payload = self._read()
            plan = self._find(payload, plan_id)
            if not plan:
                return None
            allowed = {
                "status", "scheduled_for", "current_step", "current_run_id", "error",
                "start_mode", "report",
            }
            plan.update({key: value for key, value in changes.items() if key in allowed})
            if plan.get("status") not in PLAN_STATUSES:
                plan["status"] = "blocked"
            plan["updated_at"] = time.time()
            self._write(payload)
            return dict(plan)

    def update_step(self, plan_id: str, index: int, **changes: Any) -> dict[str, Any] | None:
        with self.lock:
            payload = self._read()
            plan = self._find(payload, plan_id)
            if not plan or not 0 <= index < len(plan["steps"]):
                return None
            step = plan["steps"][index]
            step.update(
                {
                    key: value
                    for key, value in changes.items()
                    if key in {"status", "attempts", "run_id", "error"}
                }
            )
            plan["updated_at"] = time.time()
            self._write(payload)
            return dict(plan)

    def cancel(self, plan_id: str) -> dict[str, Any] | None:
        return self.update(plan_id, status="cancelled", error=None)

    def _read(self) -> dict[str, Any]:
        for path in (self.path, self.backup_path):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("plans"), list):
                return payload
        return {"version": 1, "plans": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        if self.path.exists():
            try:
                current = json.loads(self.path.read_text())
                if isinstance(current, dict) and isinstance(current.get("plans"), list):
                    shutil.copy2(self.path, self.backup_path)
            except (OSError, json.JSONDecodeError):
                pass
        os.replace(temporary, self.path)

    @staticmethod
    def _find(payload: dict[str, Any], plan_id: str) -> dict[str, Any] | None:
        return next(
            (plan for plan in payload["plans"] if plan.get("id") == plan_id),
            None,
        )
