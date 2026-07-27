from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS = {
    "agent": "",
    "mode": "",
    "model": "",
    "effort": "",
    "execution_mode": "",
}
DEFAULT_PROJECT_ID = "main"


class ConversationStore:
    def __init__(self, root: Path, runs: Path):
        self.path = root / "conversations.json"
        self.runs = runs
        self.lock = threading.Lock()

    def ensure(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conversations = self._legacy_conversation()
        self._write(
            {
                "version": 2,
                "projects": [
                    {
                        "id": DEFAULT_PROJECT_ID,
                        "name": "Projet principal",
                        "context": "",
                        "created_at": time.time(),
                    }
                ],
                "conversations": conversations,
            }
        )

    def list_projects(self) -> list[dict[str, Any]]:
        with self.lock:
            return self._read()["projects"]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self._find_project(self._read(), project_id)

    def create_project(self, name: str = "Nouveau sous-projet") -> dict[str, Any]:
        project = {
            "id": uuid.uuid4().hex,
            "name": name.strip()[:64] or "Nouveau sous-projet",
            "context": "",
            "created_at": time.time(),
        }
        with self.lock:
            payload = self._read()
            payload["projects"].append(project)
            self._write(payload)
        return project

    def update_project(
        self, project_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self.lock:
            payload = self._read()
            project = self._find_project(payload, project_id)
            if not project:
                return None
            if "name" in changes:
                project["name"] = str(changes["name"]).strip()[:64] or project["name"]
            if "context" in changes:
                project["context"] = str(changes["context"])[:16000]
            self._write(payload)
            return project

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            conversations = self._read()["conversations"]
            return sorted(
                conversations,
                key=lambda item: (not item["pinned"], -item["updated_at"]),
            )

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self._find(self._read(), conversation_id)

    def create(self, project_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        conversation = {
            "id": uuid.uuid4().hex,
            "title": "Nouvelle conversation",
            "pinned": False,
            "project_id": project_id or DEFAULT_PROJECT_ID,
            "created_at": now,
            "updated_at": now,
            "settings": dict(DEFAULT_SETTINGS),
            "messages": [],
        }
        with self.lock:
            payload = self._read()
            payload["conversations"].append(conversation)
            self._write(payload)
        return conversation

    def update(self, conversation_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock:
            payload = self._read()
            conversation = self._find(payload, conversation_id)
            if not conversation:
                return None
            if "pinned" in changes:
                conversation["pinned"] = bool(changes["pinned"])
            if "title" in changes:
                title = str(changes["title"]).strip()[:64]
                if title:
                    conversation["title"] = title
            if "project_id" in changes and self._find_project(
                payload, str(changes["project_id"])
            ):
                conversation["project_id"] = str(changes["project_id"])
            if isinstance(changes.get("settings"), dict):
                allowed = {
                    key: str(value or "")
                    for key, value in changes["settings"].items()
                    if key in DEFAULT_SETTINGS
                }
                conversation["settings"].update(allowed)
            conversation["updated_at"] = time.time()
            self._write(payload)
            return conversation

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
        provider: str | None = None,
    ) -> None:
        with self.lock:
            payload = self._read()
            conversation = self._find(payload, conversation_id)
            if not conversation:
                return
            conversation["messages"].append(
                {
                    "role": role,
                    "content": content,
                    "run_id": run_id,
                    "provider": provider,
                    "at": time.time(),
                }
            )
            if role == "user" and conversation["title"] == "Nouvelle conversation":
                conversation["title"] = content.strip().splitlines()[0][:64]
            conversation["updated_at"] = time.time()
            self._write(payload)

    def context(self, conversation_id: str, limit: int = 12000) -> str:
        conversation = self.get(conversation_id)
        if not conversation:
            return ""
        messages = conversation["messages"]
        if messages and messages[-1]["role"] == "user":
            messages = messages[:-1]
        lines = ["# Active conversation history"]
        project = self.get_project(conversation.get("project_id", DEFAULT_PROJECT_ID))
        if project and project.get("context"):
            lines.append(
                f"# Sub-project: {project['name']}\n{project['context']}"
            )
        for message in messages[-20:]:
            label = "User" if message["role"] == "user" else "Assistant"
            lines.append(f"## {label}\n{message['content']}")
        return "\n\n".join(lines)[-limit:]

    def previous_provider(self, conversation_id: str) -> str | None:
        conversation = self.get(conversation_id)
        if not conversation:
            return None
        for message in reversed(conversation["messages"]):
            if message["role"] == "assistant" and message.get("provider"):
                return str(message["provider"])
        return None

    def remove_run(self, conversation_id: str, run_id: str) -> None:
        with self.lock:
            payload = self._read()
            conversation = self._find(payload, conversation_id)
            if not conversation:
                return
            conversation["messages"] = [
                message
                for message in conversation["messages"]
                if message.get("run_id") != run_id
            ]
            conversation["updated_at"] = time.time()
            self._write(payload)

    def _read(self) -> dict[str, Any]:
        self.ensure()
        try:
            payload = json.loads(self.path.read_text())
            return self._normalize(payload)
        except (OSError, json.JSONDecodeError):
            return self._normalize({"version": 2, "conversations": []})

    def _legacy_conversation(self) -> list[dict[str, Any]]:
        messages = []
        for path in sorted(self.runs.glob("*.json")):
            try:
                run = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if run.get("request"):
                messages.append({"role": "user", "content": run["request"], "run_id": path.stem, "at": path.stat().st_mtime})
            if run.get("final"):
                messages.append({"role": "assistant", "content": run["final"], "run_id": path.stem, "at": path.stat().st_mtime})
        if not messages:
            return []
        now = time.time()
        return [{
            "id": uuid.uuid4().hex,
            "title": "Historique importé",
            "pinned": False,
            "project_id": DEFAULT_PROJECT_ID,
            "created_at": messages[0]["at"],
            "updated_at": now,
            "settings": dict(DEFAULT_SETTINGS),
            "messages": messages,
        }]

    @staticmethod
    def _find(payload: dict[str, Any], conversation_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in payload["conversations"] if item["id"] == conversation_id),
            None,
        )

    @staticmethod
    def _find_project(payload: dict[str, Any], project_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in payload["projects"] if item["id"] == project_id),
            None,
        )

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        payload.setdefault(
            "projects",
            [
                {
                    "id": DEFAULT_PROJECT_ID,
                    "name": "Projet principal",
                    "context": "",
                    "created_at": time.time(),
                }
            ],
        )
        for conversation in payload.setdefault("conversations", []):
            conversation.setdefault("project_id", DEFAULT_PROJECT_ID)
        payload["version"] = 2
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        tmp.chmod(0o600)
        os.replace(tmp, self.path)
