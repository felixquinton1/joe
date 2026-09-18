from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from .text_encoding import read_utf8_compatible

MAX_FILE_BYTES = 8 * 1024 * 1024


class FileLibrary:
    """Project-scoped local attachments with small JSON metadata."""

    def __init__(self, root: Path):
        self.root = root / "library"
        self.index = self.root / "index.json"
        self.lock = threading.RLock()

    def ensure(self) -> None:
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self.index.exists():
                self._write([])

    def add(
        self,
        project_id: str,
        name: str,
        content_type: str,
        data: str,
    ) -> dict[str, Any]:
        raw = base64.b64decode(data, validate=True)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("Fichier trop volumineux (maximum 8 Mio).")
        safe_name = (
            re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name)[:120]
            or "file"
        )
        item_id = uuid.uuid4().hex
        folder = self.root / _safe(project_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{item_id}-{safe_name}"
        path.write_bytes(raw)
        item = {
            "id": item_id,
            "project_id": project_id,
            "name": safe_name,
            "content_type": (
                content_type
                or mimetypes.guess_type(safe_name)[0]
                or "application/octet-stream"
            ),
            "size": len(raw),
            "path": str(path),
            "created_at": time.time(),
        }
        with self.lock:
            items = self._read()
            items.append(item)
            self._write(items)
        return {key: value for key, value in item.items() if key != "path"}

    def list(self, project_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {key: value for key, value in item.items() if key != "path"}
                for item in reversed(self._read())
                if item.get("project_id") == project_id
            ]

    def resolve(
        self,
        identifiers: list[str],
        project_id: str,
    ) -> list[dict[str, Any]]:
        wanted = set(identifiers)
        with self.lock:
            return [
                dict(item)
                for item in self._read()
                if item.get("id") in wanted
                and item.get("project_id") == project_id
            ]

    def get(
        self,
        item_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return one attachment, scoped to its project when one is given."""
        with self.lock:
            item = next(
                (
                    item
                    for item in self._read()
                    if item.get("id") == item_id
                    and (project_id is None or item.get("project_id") == project_id)
                ),
                None,
            )
            return dict(item) if item else None

    def delete(self, item_id: str, project_id: str | None = None) -> bool:
        with self.lock:
            items = self._read()
            item = next(
                (
                    item
                    for item in items
                    if item.get("id") == item_id
                    and (project_id is None or item.get("project_id") == project_id)
                ),
                None,
            )
            if not item:
                return False
            path = Path(str(item["path"]))
            if path.is_relative_to(self.root):
                path.unlink(missing_ok=True)
            self._write(
                [
                    candidate
                    for candidate in items
                    if candidate.get("id") != item_id
                ]
            )
            return True

    def _read(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(read_utf8_compatible(self.index))
            return payload if isinstance(payload, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write(self, payload: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.index.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.index)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value)[:80] or "free"
