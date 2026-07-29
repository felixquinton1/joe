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
CURRENT_SCHEMA_VERSION = 4


class ConversationStore:
    def __init__(
        self,
        root: Path,
        runs: Path,
        backup_path: Path | None = None,
    ):
        self.path = root / "conversations.json"
        self.legacy_backup_path = root / "conversations.json.bak"
        self.backup_path = backup_path or self.legacy_backup_path
        self.runs = runs
        self.lock = threading.Lock()

    def ensure(self) -> None:
        if self.path.exists():
            self._read()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for backup in self._backup_candidates():
            if backup.exists():
                try:
                    self._write(json.loads(backup.read_text()))
                    return
                except (OSError, json.JSONDecodeError):
                    continue
        conversations = self._legacy_conversation()
        self._write(
            {
                "version": CURRENT_SCHEMA_VERSION,
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
            return sorted(
                self._read()["projects"],
                key=lambda item: (
                    int(item.get("position", 1_000_000)),
                    float(item.get("created_at", 0)),
                ),
            )

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self._find_project(self._read(), project_id)

    def create_project(self, name: str = "Nouveau sous-projet") -> dict[str, Any]:
        existing = self.list_projects()
        project = {
            "id": uuid.uuid4().hex,
            "name": name.strip()[:64] or "Nouveau sous-projet",
            "context": "",
            "workspace_root": "",
            "additional_roots": [],
            "remote_access": False,
            "default_execution_mode": "",
            "collapsed": False,
            "position": len(existing),
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
            if "workspace_root" in changes:
                project["workspace_root"] = str(changes["workspace_root"]).strip()
            if isinstance(changes.get("additional_roots"), list):
                project["additional_roots"] = [
                    str(root).strip()
                    for root in changes["additional_roots"]
                    if str(root).strip()
                ][:8]
            if "remote_access" in changes:
                project["remote_access"] = bool(changes["remote_access"])
            if "default_execution_mode" in changes:
                value = str(changes["default_execution_mode"])
                project["default_execution_mode"] = (
                    value
                    if value
                    in {"", "read-only", "workspace-write", "danger-full-access"}
                    else ""
                )
            if "collapsed" in changes:
                project["collapsed"] = bool(changes["collapsed"])
            if "position" in changes:
                project["position"] = max(0, int(changes["position"]))
            self._write(payload)
            return project

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            conversations = self._read()["conversations"]
            return sorted(
                conversations,
                key=lambda item: (
                    not item["pinned"],
                    item.get("position") is not None,
                    (
                        int(item["position"])
                        if item.get("position") is not None
                        else -float(item.get("last_call_at", item["updated_at"]))
                    ),
                ),
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
            "last_call_at": now,
            "position": None,
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
            if "position" in changes:
                value = changes["position"]
                conversation["position"] = (
                    max(0, int(value)) if value is not None else None
                )
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

    def delete(self, conversation_id: str) -> bool:
        with self.lock:
            payload = self._read()
            before = len(payload["conversations"])
            payload["conversations"] = [
                item
                for item in payload["conversations"]
                if item["id"] != conversation_id
            ]
            if len(payload["conversations"]) == before:
                return False
            self._write(payload)
            return True

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
        provider: str | None = None,
        git_report: dict[str, Any] | None = None,
        run_summary: dict[str, Any] | None = None,
    ) -> None:
        with self.lock:
            payload = self._read()
            conversation = self._find(payload, conversation_id)
            if not conversation:
                return
            message = {
                "role": role,
                "content": content,
                "run_id": run_id,
                "provider": provider,
                "at": time.time(),
            }
            if git_report:
                message["git_report"] = git_report
            if run_summary:
                message["run_summary"] = run_summary
            conversation["messages"].append(message)
            conversation["last_call_at"] = message["at"]
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
        project = self.get_project(conversation.get("project_id", DEFAULT_PROJECT_ID))
        project_section = ""
        if project and project.get("context"):
            project_section = (
                f"# Sub-project: {project['name']}\n{project['context']}"
            )[: min(4000, limit // 3)]
        summary = str(conversation.get("context_summary", "")).strip()
        summary_section = (
            "# Earlier conversation summary\n" + summary[:4000]
            if summary
            else ""
        )
        history_budget = max(
            0,
            limit - len(project_section) - len(summary_section) - 80,
        )
        summarized = min(
            int(conversation.get("summarized_message_count", 0)),
            len(messages),
        )
        recent = []
        for message in messages[max(summarized, len(messages) - 20):]:
            label = "User" if message["role"] == "user" else "Assistant"
            recent.append(f"## {label}\n{message['content']}")
        history = "\n\n".join(recent)[-history_budget:]
        return "\n\n".join(
            section
            for section in (
                project_section,
                summary_section,
                "# Active conversation history\n" + history,
            )
            if section
        )[:limit]

    def compaction_candidate(
        self,
        conversation_id: str,
        *,
        threshold_chars: int = 30000,
        keep_recent: int = 8,
    ) -> dict[str, Any] | None:
        conversation = self.get(conversation_id)
        if not conversation:
            return None
        messages = conversation["messages"]
        start = min(
            int(conversation.get("summarized_message_count", 0)),
            len(messages),
        )
        end = max(start, len(messages) - keep_recent)
        pending = messages[start:end]
        if sum(len(str(item.get("content", ""))) for item in pending) < threshold_chars:
            return None
        transcript = "\n\n".join(
            f"{item.get('role', 'unknown').upper()}:\n{item.get('content', '')}"
            for item in pending
        )
        return {
            "previous_summary": str(conversation.get("context_summary", "")),
            "transcript": transcript,
            "message_count": end,
        }

    def save_compaction(
        self,
        conversation_id: str,
        summary: str,
        message_count: int,
    ) -> bool:
        with self.lock:
            payload = self._read()
            conversation = self._find(payload, conversation_id)
            if not conversation or message_count > len(conversation["messages"]):
                return False
            if message_count <= int(
                conversation.get("summarized_message_count", 0)
            ):
                return False
            conversation["context_summary"] = summary.strip()[:8000]
            conversation["summarized_message_count"] = message_count
            self._write(payload)
            return True

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
        if not self.path.exists():
            self.ensure()
        for path in (self.path, *self._backup_candidates()):
            try:
                payload = json.loads(path.read_text())
                return self._normalize(payload)
            except (OSError, json.JSONDecodeError):
                continue
        raise RuntimeError(
            f"Historique Joe illisible : {self.path} et sa sauvegarde"
        )

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

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        version = int(payload.get("version", 1))
        if version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "Historique Joe créé par une version plus récente "
                f"(schéma {version}, supporté {CURRENT_SCHEMA_VERSION})"
            )
        if version < CURRENT_SCHEMA_VERSION:
            self._migration_backup(payload, version)
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
            conversation.setdefault(
                "last_call_at",
                conversation.get("updated_at", conversation.get("created_at", 0)),
            )
            conversation.setdefault("position", None)
            conversation.setdefault("context_summary", "")
            conversation.setdefault("summarized_message_count", 0)
        for project in payload["projects"]:
            project.setdefault("workspace_root", "")
            project.setdefault("additional_roots", [])
            project.setdefault("remote_access", False)
            project.setdefault("default_execution_mode", "")
            project.setdefault("collapsed", False)
            project.setdefault("position", payload["projects"].index(project))
        payload["version"] = CURRENT_SCHEMA_VERSION
        if version < CURRENT_SCHEMA_VERSION:
            self._write(payload)
        return payload

    def _migration_backup(
        self, payload: dict[str, Any], source_version: int
    ) -> None:
        migration_dir = self.backup_path.parent / "migrations"
        migration_dir.mkdir(parents=True, exist_ok=True)
        migration_dir.chmod(0o700)
        target = migration_dir / (
            f"conversations-v{source_version}-"
            f"{time.strftime('%Y%m%dT%H%M%S')}.json"
        )
        if target.exists():
            return
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        )
        target.chmod(0o600)

    def _write(self, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        self._daily_backup(content)
        for path in (self.path, self.backup_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(content)
            tmp.chmod(0o600)
            os.replace(tmp, path)

    def _daily_backup(self, content: str) -> None:
        daily = self.backup_path.parent / "daily"
        target = daily / f"conversations-{time.strftime('%Y-%m-%d')}.json"
        if target.exists():
            return
        daily.mkdir(parents=True, exist_ok=True)
        daily.chmod(0o700)
        target.write_text(content)
        target.chmod(0o600)

    def _backup_candidates(self) -> tuple[Path, ...]:
        if self.backup_path == self.legacy_backup_path:
            return (self.backup_path,)
        return self.backup_path, self.legacy_backup_path
