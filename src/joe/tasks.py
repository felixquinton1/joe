from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any


class TaskStore:
    """Small durable execution registry backed by one atomic JSON file."""

    def __init__(self, root: Path):
        self.path = root / "tasks.json"
        self.backup_path = root / "tasks.json.bak"
        self.lock = threading.RLock()

    def ensure(self) -> None:
        with self.lock:
            if not self.path.exists():
                self._write({"version": 1, "tasks": []})
            else:
                self._read()

    def create(
        self,
        task_id: str,
        request: str,
        conversation_id: str,
        project_id: str,
        *,
        workspace: Path,
        base_workspace: Path,
        isolated: bool,
        branch: str | None = None,
        base_commit: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            payload = self._read()
            existing = self._find(payload, task_id)
            if existing:
                existing.update({
                    "status": "running",
                    "updated_at": time.time(),
                    "workspace": str(workspace),
                    "base_workspace": str(base_workspace),
                    "isolated": isolated,
                    "branch": branch,
                    "base_commit": base_commit,
                })
                self._write(payload)
                return dict(existing)
            now = time.time()
            task = {
                "id": task_id,
                "title": _title(request),
                "request": request,
                "conversation_id": conversation_id,
                "project_id": project_id,
                "status": "running",
                "workspace": str(workspace),
                "base_workspace": str(base_workspace),
                "isolated": isolated,
                "branch": branch,
                "base_commit": base_commit,
                "provider": None,
                "model": None,
                "mode": None,
                "files": 0,
                "insertions": 0,
                "deletions": 0,
                "error": None,
                "integrated_commit": None,
                "scheduled_for": None,
                "wait_reason": None,
                "attempt": 0,
                "created_at": now,
                "updated_at": now,
            }
            payload["tasks"].append(task)
            self._write(payload)
            return dict(task)

    def update(self, task_id: str, **changes: Any) -> dict[str, Any] | None:
        with self.lock:
            payload = self._read()
            task = self._find(payload, task_id)
            if not task:
                return None
            allowed = {
                "status", "provider", "model", "mode", "files",
                "insertions", "deletions", "error", "integrated_commit",
                "workspace", "branch",
                "scheduled_for", "wait_reason", "attempt",
            }
            task.update({key: value for key, value in changes.items() if key in allowed})
            task["updated_at"] = time.time()
            self._write(payload)
            return dict(task)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            task = self._find(self._read(), task_id)
            return dict(task) if task else None

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            tasks = sorted(
                self._read()["tasks"],
                key=lambda item: float(item.get("updated_at", 0)),
                reverse=True,
            )
            return [dict(item) for item in tasks[:limit]]

    def delete(self, task_id: str) -> bool:
        with self.lock:
            payload = self._read()
            before = len(payload["tasks"])
            payload["tasks"] = [
                item for item in payload["tasks"] if item.get("id") != task_id
            ]
            if len(payload["tasks"]) == before:
                return False
            self._write(payload)
            return True

    def _read(self) -> dict[str, Any]:
        for path in (self.path, self.backup_path):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("tasks"), list):
                return payload
        return {"version": 1, "tasks": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if self.path.exists():
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(current, dict) and isinstance(current.get("tasks"), list):
                    shutil.copy2(self.path, self.backup_path)
            except (OSError, json.JSONDecodeError):
                pass
        os.replace(temporary, self.path)

    @staticmethod
    def _find(payload: dict[str, Any], task_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in payload["tasks"] if item.get("id") == task_id),
            None,
        )


def _title(request: str) -> str:
    compact = " ".join(request.split())
    return compact[:77] + ("…" if len(compact) > 77 else "")
