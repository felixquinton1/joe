from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pexpect

from .models import Mode, Route

_CACHE_SECONDS = 60
_STALE_USAGE_SECONDS = 6 * 60 * 60
_LOW_REMAINING_PERCENT = 20
_FAR_RESET_SECONDS = 24 * 60 * 60
_cache: tuple[float, list[dict[str, Any]]] | None = None
_last_available: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def usage_status(force: bool = False) -> list[dict[str, Any]]:
    """Return personal usage exposed by each installed provider CLI."""
    global _cache
    with _lock:
        if not force and _cache and time.monotonic() - _cache[0] < _CACHE_SECONDS:
            return _cache[1]
    fresh = [
        _codex_status(),
        _refresh_claude_status() if force else _claude_status(),
        _gemini_status(),
        _unavailable(
            "copilot",
            "Disponible uniquement dans la session interactive Copilot",
        ),
    ]
    with _lock:
        providers = [_with_last_available(item) for item in fresh]
        _cache = (time.monotonic(), providers)
        return providers


def cached_usage_status() -> list[dict[str, Any]]:
    """Return quota data immediately without probing provider CLIs."""
    with _lock:
        return list(_cache[1]) if _cache else []


def record_gemini_usage(
    raw_output: str,
    path: Path | None = None,
    *,
    now: float | None = None,
) -> None:
    stats = _gemini_stats(raw_output)
    if not stats:
        return
    timestamp = time.time() if now is None else now
    target = path or _gemini_usage_path()
    try:
        payload = json.loads(target.read_text()) if target.exists() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    day = datetime.fromtimestamp(timestamp).date().isoformat()
    days = payload.setdefault("days", {})
    current = days.setdefault(day, {"tokens": 0, "requests": 0, "models": {}})
    current["tokens"] += stats["tokens"]
    current["requests"] += stats["requests"]
    for model, model_stats in stats["models"].items():
        item = current["models"].setdefault(
            model, {"tokens": 0, "requests": 0}
        )
        item["tokens"] += model_stats["tokens"]
        item["requests"] += model_stats["requests"]
    payload["last"] = {"at": timestamp, **stats}
    payload["days"] = dict(sorted(days.items())[-30:])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        target.chmod(0o600)
    except OSError:
        return


def _gemini_stats(raw_output: str) -> dict[str, Any] | None:
    payloads = []
    for line in raw_output.splitlines():
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not payloads:
        start = raw_output.find("{")
        if start >= 0:
            try:
                payloads.append(json.loads(raw_output[start:]))
            except json.JSONDecodeError:
                pass
    stats = next(
        (
            payload.get("stats")
            for payload in reversed(payloads)
            if isinstance(payload, dict)
            and isinstance(payload.get("stats"), dict)
        ),
        None,
    )
    if not stats:
        return None
    models = {}
    for model, values in stats.get("models", {}).items():
        if not isinstance(values, dict):
            continue
        tokens = values.get("tokens", {})
        api = values.get("api", {})
        models[str(model)] = {
            "tokens": int(
                tokens.get("total", values.get("total_tokens", 0)) or 0
            ),
            "requests": int(api.get("totalRequests", 1) or 0),
        }
    if not models:
        return None
    return {
        "tokens": sum(item["tokens"] for item in models.values()),
        "requests": sum(item["requests"] for item in models.values()),
        "models": models,
    }


def _gemini_usage_path() -> Path:
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    )
    return data_home / "joe" / "gemini_usage.json"


def _gemini_status(
    path: Path | None = None,
    settings_path: Path | None = None,
) -> dict[str, Any]:
    target = path or _gemini_usage_path()
    auth_type = _gemini_auth_type(settings_path)
    quota_description = _gemini_quota_description(auth_type)
    try:
        payload = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return {
            "provider": "gemini",
            "available": True,
            "plan": None,
            "windows": [],
            "metrics": [{"name": "Quota", "value": quota_description}],
            "message": (
                "Aucun appel Joe mesuré · pas de réserve globale de tokens exposée"
            ),
        }
    day = datetime.now().date().isoformat()
    current = payload.get("days", {}).get(
        day, {"tokens": 0, "requests": 0, "models": {}}
    )
    last = payload.get("last", {})
    models = ", ".join(sorted(current.get("models", {}))) or "aucun"
    return {
        "provider": "gemini",
        "available": True,
        "plan": None,
        "windows": [],
        "metrics": [
            {"name": "Tokens aujourd’hui", "value": f"{current['tokens']:,}"},
            {"name": "Requêtes aujourd’hui", "value": str(current["requests"])},
            {"name": "Quota", "value": quota_description},
            {"name": "Modèles utilisés", "value": models},
            {
                "name": "Dernier appel",
                "value": (
                    datetime.fromtimestamp(float(last["at"])).strftime("%H:%M")
                    if last.get("at")
                    else "aucun"
                ),
            },
        ],
        "message": (
            "Consommation Joe uniquement · Gemini limite surtout les requêtes "
            "selon le modèle et l’offre · détail via /stats model"
        ),
    }


def _gemini_auth_type(path: Path | None = None) -> str | None:
    target = path or Path.home() / ".gemini" / "settings.json"
    try:
        payload = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    selected = payload.get("security", {}).get("auth", {}).get("selectedType")
    return str(selected) if selected else None


def _gemini_quota_description(auth_type: str | None) -> str:
    if auth_type == "gemini-api-key":
        return "Clé API · variable par modèle/offre"
    if auth_type:
        return "Compte Google · plafond en requêtes"
    return "Limites variables par modèle/offre"


def _with_last_available(status: dict[str, Any]) -> dict[str, Any]:
    provider = str(status["provider"])
    now = time.monotonic()
    if status.get("available"):
        _last_available[provider] = (now, status)
        return status
    previous = _last_available.get(provider)
    if not previous or now - previous[0] > _STALE_USAGE_SECONDS:
        return status
    fallback = dict(previous[1])
    fallback["stale"] = True
    fallback["message"] = (
        f"Dernière mesure connue · {status.get('message', 'actualisation indisponible')}"
    )
    return fallback


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
    age_seconds = time.time() - fetched_at
    if age_seconds > _STALE_USAGE_SECONDS:
        return _unavailable(
            "claude",
            "Données trop anciennes · ouvre /usage dans Claude pour les actualiser",
        )
    stale = age_seconds > 15 * 60
    return {
        "provider": "claude",
        "available": bool(windows),
        "stale": stale,
        "plan": None,
        "windows": windows,
        "message": (
            "Dernière mesure connue · ouvre /usage dans Claude pour l’actualiser"
            if windows and stale
            else None if windows else "Aucune limite communiquée par Claude"
        ),
    }


def _refresh_claude_status(timeout: int = 18) -> dict[str, Any]:
    output = ""
    child = None
    try:
        env = os.environ.copy()
        env["DISABLE_AUTOUPDATER"] = "1"
        child = pexpect.spawn(
            "claude",
            ["--ax-screen-reader", "--permission-mode", "plan"],
            env=env,
            encoding="utf-8",
            timeout=timeout,
        )
        child.expect("/effort")
        child.send("/usage\r")
        child.expect("Current session")
        output = child.before + child.after
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                output += child.read_nonblocking(8192, 0.5)
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                break
    except (OSError, pexpect.ExceptionPexpect):
        output = ""
    finally:
        if child is not None:
            if child.isalive():
                child.sendcontrol("c")
            child.close(force=True)
    live = _parse_claude_usage_screen(output)
    if live:
        return live
    fallback = _claude_status()
    fallback["message"] = (
        "Actualisation /usage indisponible · dernière mesure locale"
        if fallback.get("available")
        else "Actualisation /usage Claude indisponible"
    )
    fallback["stale"] = True
    return fallback


def _parse_claude_usage_screen(
    output: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    text = _strip_terminal_codes(output)
    current = now or datetime.now().astimezone()
    patterns = (
        ("5 heures", r"Current session\s+(\d+(?:\.\d+)?)%.*?used\s+Resets ([^\r\n]+)", 300),
        (
            "7 jours",
            r"Current week \(all models\)\s+(\d+(?:\.\d+)?)%.*?used\s+Resets ([^\r\n]+)",
            10080,
        ),
    )
    windows = []
    for name, pattern, duration in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            continue
        used = max(0.0, min(100.0, float(match.group(1))))
        windows.append(
            {
                "name": name,
                "used_percent": round(used, 1),
                "remaining_percent": round(100 - used, 1),
                "resets_at": _claude_reset_timestamp(match.group(2), current),
                "duration_minutes": duration,
            }
        )
    if not windows:
        return None
    return {
        "provider": "claude",
        "available": True,
        "stale": False,
        "plan": None,
        "windows": windows,
        "message": "Actualisé via /usage Claude",
    }


def _strip_terminal_codes(value: str) -> str:
    value = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", value)
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def _claude_reset_timestamp(value: str, now: datetime) -> float | None:
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip().replace(" ", "")
    for pattern in ("%I:%M%p", "%b%d,%I:%M%p"):
        try:
            parsed = datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
        if pattern == "%I:%M%p":
            target = now.replace(
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
            if target <= now:
                target += timedelta(days=1)
        else:
            target = now.replace(
                month=parsed.month,
                day=parsed.day,
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
            if target < now - timedelta(days=2):
                target = target.replace(year=target.year + 1)
        return target.timestamp()
    return None


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
            "clientInfo": {"name": "joe", "version": "0.18.1"},
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
