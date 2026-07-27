from __future__ import annotations

import json
import os
import selectors
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Mode, Route

_CACHE_SECONDS = 60
_LOW_REMAINING_PERCENT = 20
_FAR_RESET_SECONDS = 24 * 60 * 60
_cache: tuple[float, list[dict[str, Any]]] | None = None
_lock = threading.Lock()


def usage_status(force: bool = False) -> list[dict[str, Any]]:
    """Return personal usage exposed by each installed provider CLI."""
    global _cache
    with _lock:
        if not force and _cache and time.monotonic() - _cache[0] < _CACHE_SECONDS:
            return _cache[1]
        providers = [
            _codex_status(),
            _claude_status(),
            _unavailable("gemini", "Non exposé par la CLI Gemini"),
            _unavailable(
                "copilot",
                "Disponible uniquement dans la session interactive Copilot",
            ),
        ]
        _cache = (time.monotonic(), providers)
        return providers


def balance_route(
    route: Route,
    statuses: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> Route:
    """Move a FAST automatic route away from a constrained main provider."""
    if route.mode is not Mode.FAST or route.primary not in {"codex", "claude"}:
        return route
    alternative = "claude" if route.primary == "codex" else "codex"
    by_provider = {item.get("provider"): item for item in statuses}
    current = by_provider.get(route.primary)
    other = by_provider.get(alternative)
    timestamp = time.time() if now is None else now
    pressure = _quota_pressure(current, timestamp)
    alternative_pressure = _quota_pressure(other, timestamp)
    if not pressure or alternative_pressure is None or alternative_pressure:
        return route
    return Route(
        route.intent,
        route.mode,
        alternative,
        route.reviewer,
        (
            f"{route.reason}; quota-switch={route.primary}->{alternative}; "
            f"{pressure}"
        ),
    )


def _quota_pressure(
    status: dict[str, Any] | None,
    now: float,
) -> str | None | bool:
    if not status or not status.get("available") or not status.get("windows"):
        return None
    pressures = []
    for window in status["windows"]:
        remaining = float(window.get("remaining_percent", 100))
        reset = window.get("resets_at")
        seconds = float(reset) - now if isinstance(reset, (int, float)) else None
        constrained = remaining <= 5 or (
            remaining <= _LOW_REMAINING_PERCENT
            and (seconds is None or seconds >= _FAR_RESET_SECONDS)
        )
        if constrained:
            pressures.append((remaining, seconds, window))
    if not pressures:
        return False
    remaining, seconds, limiting = min(pressures, key=lambda item: item[0])
    reset_text = (
        "reset inconnu"
        if seconds is None
        else f"reset dans {max(0, int(seconds // 60))} min"
    )
    return f"{remaining:g} % restant sur {limiting.get('name', 'quota')}, {reset_text}"


def _claude_status(path: Path | None = None) -> dict[str, Any]:
    cache_path = path or Path.home() / ".claude.json"
    try:
        payload = json.loads(cache_path.read_text())
        cached = payload["cachedUsageUtilization"]
        fetched_at = float(cached["fetchedAtMs"]) / 1000
        utilization = cached["utilization"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _unavailable("claude", "Quota non encore mis en cache par Claude")

    age_seconds = time.time() - fetched_at
    if age_seconds > 15 * 60:
        return _unavailable(
            "claude",
            "Données périmées · ouvre /usage dans Claude pour les actualiser",
        )

    windows = []
    for name, key, duration in (
        ("5 heures", "five_hour", 300),
        ("7 jours", "seven_day", 10080),
        ("Sonnet · 7 jours", "seven_day_sonnet", 10080),
        ("Opus · 7 jours", "seven_day_opus", 10080),
    ):
        item = utilization.get(key)
        if not isinstance(item, dict) or item.get("utilization") is None:
            continue
        used = max(0.0, min(100.0, float(item["utilization"])))
        windows.append(
            {
                "name": name,
                "used_percent": round(used, 1),
                "remaining_percent": round(100 - used, 1),
                "resets_at": _iso_timestamp(item.get("resets_at")),
                "duration_minutes": duration,
            }
        )
    return {
        "provider": "claude",
        "available": bool(windows),
        "plan": None,
        "windows": windows,
        "message": None if windows else "Aucune limite communiquée par Claude",
    }


def _iso_timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).timestamp()
    except ValueError:
        return None


def _codex_status() -> dict[str, Any]:
    initialize = {
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "joe", "version": "0.10.3"},
            "capabilities": {"experimentalApi": True},
        },
    }
    try:
        process = subprocess.Popen(
            ["codex", "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if process.stdin is None or process.stdout is None:
            raise OSError("Codex stdio unavailable")
        _send(process, initialize)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = b""
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not selector.select(timeout=0.25):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if message.get("id") == 1:
                    _send(process, {"method": "initialized", "params": {}})
                    _send(
                        process,
                        {
                            "id": 2,
                            "method": "account/rateLimits/read",
                            "params": None,
                        },
                    )
                if message.get("id") == 2 and isinstance(message.get("result"), dict):
                    return normalize_codex_usage(message["result"])
    except OSError:
        return _unavailable("codex", "Quota Codex temporairement indisponible")
    finally:
        if "process" in locals():
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
    return _unavailable("codex", "Quota Codex temporairement indisponible")


def _send(process: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write((json.dumps(message) + "\n").encode())
    process.stdin.flush()


def normalize_codex_usage(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("rateLimits") or {}
    windows = []
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if not isinstance(window, dict):
            continue
        duration = window.get("windowDurationMins")
        used = max(0.0, min(100.0, float(window.get("usedPercent", 0))))
        windows.append(
            {
                "name": _window_name(duration),
                "used_percent": round(used, 1),
                "remaining_percent": round(100 - used, 1),
                "resets_at": window.get("resetsAt"),
                "duration_minutes": window.get("windowDurationMins"),
            }
        )
    individual = snapshot.get("individualLimit")
    if isinstance(individual, dict) and individual.get("remainingPercent") is not None:
        remaining = max(0.0, min(100.0, float(individual["remainingPercent"])))
        windows.append(
            {
                "name": "Limite personnelle",
                "remaining_percent": round(remaining, 1),
                "used_percent": round(100 - remaining, 1),
                "resets_at": individual.get("resetsAt"),
                "duration_minutes": None,
            }
        )
    return {
        "provider": "codex",
        "available": bool(windows),
        "plan": snapshot.get("planType"),
        "windows": windows,
        "message": None if windows else "Aucune limite communiquée",
    }


def _window_name(duration: Any) -> str:
    if not isinstance(duration, (int, float)) or duration <= 0:
        return "Fenêtre de quota"
    if duration % 1440 == 0:
        days = int(duration / 1440)
        return f"{days} jour{'s' if days > 1 else ''}"
    if duration % 60 == 0:
        hours = int(duration / 60)
        return f"{hours} heure{'s' if hours > 1 else ''}"
    return f"{int(duration)} minutes"


def _unavailable(provider: str, message: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "available": False,
        "plan": None,
        "windows": [],
        "message": message,
    }
