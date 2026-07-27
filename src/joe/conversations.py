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
        self._write({"version": 1, "conversations": conversations})

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

    def create(self) -> dict[str, Any]:
        now = time.time()
        conversation = {
            "id": uuid.uuid4().hex,
            "title": "Nouvelle conversation",
            "pinned": False,
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

    def _read(self) -> dict[str, Any]:
        self.ensure()
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "conversations": []}

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

    def _write(self, payload: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        tmp.chmod(0o600)
        os.replace(tmp, self.path)
