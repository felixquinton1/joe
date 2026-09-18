from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from .text_encoding import read_utf8_compatible


class ApprovalStore:
    """Durable, project-local approval requests."""

    def __init__(self, root: Path):
        self.path = root / "approvals.json"
        self.lock = threading.RLock()

    def ensure(self) -> None:
        with self.lock:
            if not self.path.exists():
                self._write([])

    def create(
        self,
        kind: str,
        conversation_id: str,
        project_id: str,
        payload: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        with self.lock:
            items = self._read()
            existing = next(
                (
                    item
                    for item in items
                    if item["status"] == "pending"
                    and item["conversation_id"] == conversation_id
                    and item["kind"] == kind
                    and item["payload"].get("request") == payload.get("request")
                ),
                None,
            )
            if existing:
                return dict(existing)
            now = time.time()
            item = {
                "id": uuid.uuid4().hex,
                "kind": kind,
                "conversation_id": conversation_id,
                "project_id": project_id,
                "payload": payload,
                "message": message,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            items.append(item)
            self._write(items)
            return dict(item)

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            items = self._read()
            if status:
                items = [item for item in items if item["status"] == status]
            return sorted(
                (dict(item) for item in items),
                key=lambda item: item["updated_at"],
                reverse=True,
            )

    def get(self, approval_id: str) -> dict[str, Any] | None:
        with self.lock:
            item = next(
                (
                    item
                    for item in self._read()
                    if item["id"] == approval_id
                ),
                None,
            )
            return dict(item) if item else None

    def decide(self, approval_id: str, decision: str) -> dict[str, Any] | None:
        if decision not in {"approved", "refused"}:
            raise ValueError("Décision invalide.")
        with self.lock:
            items = self._read()
            item = next(
                (item for item in items if item["id"] == approval_id),
                None,
            )
            if not item:
                return None
            if item["status"] == "pending":
                item["status"] = decision
                item["updated_at"] = time.time()
                self._write(items)
            return dict(item)

    def consume(
        self,
        approval_id: str,
        conversation_id: str,
        request: str,
    ) -> bool:
        with self.lock:
            items = self._read()
            item = next(
                (item for item in items if item["id"] == approval_id),
                None,
            )
            if (
                not item
                or item["status"] != "approved"
                or item["conversation_id"] != conversation_id
                or item["payload"].get("request") != request
            ):
                return False
            item["status"] = "consumed"
            item["updated_at"] = time.time()
            self._write(items)
            return True

    def allows(
        self,
        approval_id: str,
        conversation_id: str,
        request: str,
    ) -> bool:
        item = self.get(approval_id)
        return bool(
            item
            and item["status"] == "approved"
            and item["conversation_id"] == conversation_id
            and item["payload"].get("request") == request
        )

    def _read(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(read_utf8_compatible(self.path))
            return payload if isinstance(payload, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, payload: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)
