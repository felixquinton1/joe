from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = {
    "version": 1,
    "timeout_seconds": 900,
    "max_context_chars": 16000,
    "max_active_file_chars": 8000,
    "fallbacks": {
        "codex": ["gemini", "claude", "copilot"],
        "claude": ["gemini", "codex", "copilot"],
        "gemini": ["codex", "claude", "copilot"],
        "copilot": ["gemini", "codex", "claude"],
    },
}
SECRET_PATTERN = re.compile(
    r"""(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s]+)"""
)


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
        self.runs.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.runs.chmod(0o700)
        initial = {
            "project.md": "# Project\n\nStable conventions and project context.\n",
            "session.md": "# Session\n\nNo active work recorded yet.\n",
            "handoff.md": "# Handoff\n\nNo previous handoff.\n",
            "config.yaml": json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n",
        }
        for name, content in initial.items():
            path = self.root / name
            if not path.exists():
                self._atomic_write(path, content)

    def config(self) -> dict[str, Any]:
        self.ensure()
        try:
            loaded = json.loads((self.root / "config.yaml").read_text())
        except (OSError, json.JSONDecodeError):
            loaded = {}
        return _deep_merge(DEFAULT_CONFIG, loaded)

    def context(self, request: str) -> str:
        self.ensure()
        limit = int(self.config()["max_context_chars"])
        sections = [f"# Current request\n{request.strip()}"]
        for filename in ("project.md", "session.md", "handoff.md"):
            text = (self.root / filename).read_text(errors="replace")
            sections.append(f"# {filename}\n{text.strip()}")
        instructions = self._instructions()
        if instructions:
            sections.append("# Project instructions\n" + instructions)
        return "\n\n".join(sections)[:limit]

    def previous_provider(self) -> str | None:
        path = self.root / "session.md"
        if not path.exists():
            return None
        match = re.search(r"^Provider:\s*(\w+)", path.read_text(), re.MULTILINE)
        return match.group(1) if match else None

    def save_run(self, run_id: str, payload: dict[str, Any]) -> Path:
        self.ensure()
        path = self.runs / f"{run_id}.json"
        safe = redact_values(payload)
        self._atomic_write(path, json.dumps(safe, indent=2, ensure_ascii=False) + "\n")
        return path

    def update_active(
        self,
        *,
        request: str,
        provider: str,
        mode: str,
        response: str,
        files: list[str] | None = None,
    ) -> None:
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
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = self.project / name
            if path.exists():
                chunks.append(f"## {name}\n{path.read_text(errors='replace')}")
        return "\n\n".join(chunks)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(content)
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
