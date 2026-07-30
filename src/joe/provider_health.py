from __future__ import annotations

import threading
import time
from typing import Any

from .models import ProviderResult

_COOLDOWN_SECONDS = {
    "quota": 10 * 60,
    "timeout": 5 * 60,
    "authentication": 5 * 60,
    "unavailable": 2 * 60,
}
_failures: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


def record_result(result: ProviderResult) -> None:
    with _lock:
        duration = _COOLDOWN_SECONDS.get(result.error_kind or "")
        if duration:
            _failures[result.provider] = (
                time.monotonic() + duration,
                str(result.error_kind),
            )
        elif result.ok:
            _failures.pop(result.provider, None)


def recent_failure(provider: str) -> tuple[str, int] | None:
    with _lock:
        failure = _failures.get(provider)
        if not failure:
            return None
        until, kind = failure
        remaining = int(max(0, until - time.monotonic()))
        if remaining <= 0:
            _failures.pop(provider, None)
            return None
        return kind, remaining


def apply_cooldowns(statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    adjusted = []
    for status in statuses:
        item = dict(status)
        failure = recent_failure(str(item.get("provider", "")))
        if failure:
            kind, remaining = failure
            item["available"] = False
            item["availability_state"] = (
                "quota_exhausted" if kind == "quota" else "temporarily_unavailable"
            )
            item["cooldown_seconds"] = remaining
            item["message"] = (
                f"Pause temporaire après {kind} · nouvel essai dans "
                f"{max(1, (remaining + 59) // 60)} min"
            )
        adjusted.append(item)
    return adjusted


def clear_cooldowns() -> None:
    with _lock:
        _failures.clear()
