"""Lire les evenements JSON terminaux de Codex avant le remplacement JSONL."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from .models import ProviderFailure


def _field(value: Any, names: tuple[str, ...]) -> Any:
    if not isinstance(value, dict):
        return None
    for name in names:
        if name in value:
            return value[name]
    for child in value.values():
        found = _field(child, names)
        if found is not None:
            return found
    return None


def codex_failure(output: str, returncode: int = 0) -> ProviderFailure | None:
    error: Any = None
    terminal_error: Any = None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.failed":
            terminal_error = event.get("error") or event
        elif event.get("type") == "error":
            error = event.get("error") or event
        elif event.get("type") == "turn.completed" and returncode == 0:
            # Un `error` de reconnexion n'est pas terminal : le tour qui se
            # termine ensuite normalement l'emporte.
            error = None
    if terminal_error is not None:
        error = terminal_error
    if error is None:
        return None
    message = error if isinstance(error, str) else _field(error, ("message",))
    message = str(message or "Codex turn failed")[:2000]
    status = _field(error, ("http_status", "http_status_code", "status_code", "status"))
    if not isinstance(status, int):
        match = re.search(r"(?i)(?:status(?:\s+code)?[\s:=]+|HTTP\s+)([45]\d\d)\b", message)
        status = int(match[1]) if match else None
    retry = _field(error, ("retry_after_seconds", "retry_after"))
    retry_after: float | None = None
    if status == 429 and isinstance(retry, (int, float)) and not isinstance(retry, bool) and math.isfinite(retry) and retry >= 0:
        retry_after = float(retry)
    return ProviderFailure(message, status, retry_after)
