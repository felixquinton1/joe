from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provider_registry import default_fallbacks
from .text_encoding import read_user_text, read_utf8_compatible

DEFAULT_CONFIG = {
    "version": 1,
    "timeout_seconds": 900,
    "max_context_chars": 16000,
    "max_active_file_chars": 8000,
    "run_retention": {
        "max_runs": 500,
        "max_age_days": 30,
        "max_total_mb": 500,
    },
    "semantic_compaction": {
        "enabled": True,
        "threshold_chars": 30000,
        "keep_recent_messages": 8,
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
    },
    "shared_skill_paths": [],
    "fallbacks": default_fallbacks(),
}
SECRET_PATTERN = re.compile(
    r"""(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s]+)"""
)
_MEMORY_LOCK = threading.RLock()
RESPONSE_ORGANIZATION = """# Organisation de la réponse
Sépare clairement les annonces de progression de la réponse finale.

- Avant d'agir, indique brièvement ce que tu vas faire sous
  `Ce que je vais faire :`.
- Réserve les mises à jour intermédiaires aux informations de progression utiles.
- Une fois le travail terminé, commence la réponse finale par `Résultat :`.
- Rends la réponse finale autonome et centrée sur le résultat. Ne répète pas le
  plan initial et ne mélange pas les intentions futures avec le travail terminé.
"""


def redact(text: str) -> str:
    return SECRET_PATTERN.sub(r"\1\2[REDACTED]", text)


def redact_values(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_values(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_values(item) for key, item in value.items()}
    return value


class ProjectMemory:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.root = self.project / ".agentflow"
        self.runs = self.root / "runs"

    def ensure(self) -> None:
        with _MEMORY_LOCK:
            self.runs.mkdir(parents=True, exist_ok=True)
            self.root.chmod(0o700)
            self.runs.chmod(0o700)
            initial = {
                ".gitignore": (
                    "conversations.json\n"
                    "conversations.json.bak\n"
                    "runs/\n"
                    "session.md\n"
                    "handoff.md\n"
                    "pending_runs.json\n"
                    "automations.json\n"
                    "automations.json.bak\n"
                    "autonomous.json\n"
                    "autonomous.json.bak\n"
                    "autonomous/\n"
                    "backups/\n"
                    "migrations/\n"
                    "*.reject.json\n"
                    "*.reject.patch\n"
                    "*.tmp\n"
                ),
                "project.md": "# Project\n\nStable conventions and project context.\n",
                "session.md": "# Session\n\nNo active work recorded yet.\n",
                "handoff.md": "# Handoff\n\nNo previous handoff.\n",
                "config.yaml": (
                    json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n"
                ),
            }
            for name, content in initial.items():
                path = self.root / name
                if not path.exists():
                    self._atomic_write(path, content)

    def config(self) -> dict[str, Any]:
        with _MEMORY_LOCK:
            self.ensure()
            try:
                loaded = json.loads(read_utf8_compatible(self.root / "config.yaml"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            return _deep_merge(DEFAULT_CONFIG, loaded)

    def context(self, request: str) -> str:
        with _MEMORY_LOCK:
            self.ensure()
            limit = int(self.config()["max_context_chars"])
            sections = [
                f"# Current request\n{request.strip()}",
                RESPONSE_ORGANIZATION.strip(),
            ]
            for filename in ("project.md", "session.md", "handoff.md"):
                text = (self.root / filename).read_text(encoding="utf-8", errors="replace")
                sections.append(f"# {filename}\n{text.strip()}")
            instructions = self._instructions()
            if instructions:
                sections.append("# Project instructions\n" + instructions)
            return "\n\n".join(sections)[:limit]

    def previous_provider(self) -> str | None:
        with _MEMORY_LOCK:
            path = self.root / "session.md"
            if not path.exists():
                return None
            match = re.search(
                r"^Provider:\s*(\w+)",
                read_utf8_compatible(path),
                re.MULTILINE,
            )
            return match.group(1) if match else None

    def save_run(self, run_id: str, payload: dict[str, Any]) -> Path:
        with _MEMORY_LOCK:
            self.ensure()
            path = self.runs / f"{run_id}.json"
            safe = redact_values(payload)
            self._atomic_write(
                path,
                json.dumps(safe, indent=2, ensure_ascii=False) + "\n",
            )
            self.prune_runs()
            return path

    def prune_runs(self, now: float | None = None) -> list[Path]:
        with _MEMORY_LOCK:
            policy = self.config().get("run_retention", {})
            max_runs = max(1, int(policy.get("max_runs", 500)))
            max_age = max(1, int(policy.get("max_age_days", 30))) * 86400
            max_bytes = max(1, int(policy.get("max_total_mb", 500))) * 1024 * 1024
            timestamp = (
                datetime.now(timezone.utc).timestamp() if now is None else now
            )
            entries = sorted(
                (path for path in self.runs.glob("*.json") if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            kept_bytes = 0
            removed = []
            for index, path in enumerate(entries):
                stat = path.stat()
                expired = timestamp - stat.st_mtime > max_age
                oversized = kept_bytes + stat.st_size > max_bytes
                if index >= max_runs or expired or oversized:
                    path.unlink(missing_ok=True)
                    removed.append(path)
                else:
                    kept_bytes += stat.st_size
            return removed

    def update_active(
        self,
        *,
        request: str,
        provider: str,
        mode: str,
        response: str,
        files: list[str] | None = None,
    ) -> None:
        with _MEMORY_LOCK:
            limit = int(self.config()["max_active_file_chars"])
            now = datetime.now(timezone.utc).isoformat()
            clean_request = redact(request.strip())
            clean_response = redact(response.strip())
            files_text = ", ".join(files or []) or "not detected"
            session = (
                "# Session\n\n"
                f"Updated: {now}\nProvider: {provider}\nMode: {mode}\n"
                f"Objective: {clean_request}\nFiles: {files_text}\n\n"
                f"## Latest result\n{clean_response}\n"
            )[:limit]
            handoff = (
                "# Handoff\n\n"
                f"Request: {clean_request}\nLast provider: {provider}\n"
                f"Mode: {mode}\nRelevant files: {files_text}\n\n"
                f"## Compact prior response\n{clean_response}\n"
            )[:limit]
            self._atomic_write(self.root / "session.md", session)
            self._atomic_write(self.root / "handoff.md", handoff)

    def _instructions(self) -> str:
        chunks = []
        # AGENTS.md is Joe's provider-neutral repository contract. Provider-
        # specific files remain native to their CLI and are not copied into
        # every provider's prompt.
        agents = self.project / "AGENTS.md"
        if agents.exists():
            chunks.append(f"## AGENTS.md\n{read_user_text(agents)}")
        from .skills import global_skills_root

        skill_roots = [
            self.project / ".agentflow" / "skills",
            self.project / "skills",
            Path.home() / ".joe" / "global-skills",
        ]
        configured = self.config().get("shared_skill_paths", [])
        if isinstance(configured, list):
            skill_roots.extend(Path(str(path)).expanduser() for path in configured)
        skill_roots.append(global_skills_root())
        seen: set[Path] = set()
        total = 0
        for root in skill_roots:
            root = root.resolve()
            if root in seen or not root.is_dir():
                continue
            seen.add(root)
            for path in sorted(root.rglob("*.md")):
                if not path.is_file() or path.name.startswith("."):
                    continue
                try:
                    content = read_user_text(path)
                except OSError:
                    continue
                remaining = 12000 - total
                if remaining <= 0:
                    break
                content = content[:remaining]
                chunks.append(f"## Shared skill: {path.relative_to(root).as_posix()}\n{content}")
                total += len(content)
        return "\n\n".join(chunks)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, path)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
