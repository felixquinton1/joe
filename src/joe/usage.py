from __future__ import annotations

import os
import selectors
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pexpect

from . import __version__
from .models import Mode, Route
from .usage_claude import (
    claude_reset_timestamp as _claude_reset_timestamp,
    claude_status as _claude_status_impl,
    iso_timestamp as _iso_timestamp,
    parse_claude_usage_screen as _parse_claude_usage_screen,
    refresh_claude_status as _refresh_claude_status_impl,
    refresh_claude_via_tmux as _refresh_claude_via_tmux_impl,
    strip_terminal_codes as _strip_terminal_codes,
)
from .usage_codex import (
    codex_status as _codex_status_impl,
    normalize_codex_usage,
    send as _send_impl,
    window_name as _window_name,
)
from .usage_gemini import (
    gemini_auth_type as _gemini_auth_type_impl,
    gemini_quota_description as _gemini_quota_description_impl,
    gemini_stats as _gemini_stats,
    gemini_status as _gemini_status_impl,
    gemini_usage_path as _gemini_usage_path_impl,
    record_usage as _record_gemini_usage_impl,
)

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
    timestamp = time.time() if now is None else now
    target = path or _gemini_usage_path()
    with _lock:
        try:
            _record_gemini_usage_impl(raw_output, target, now=timestamp)
        except OSError:
            return


def _gemini_usage_path() -> Path:
    return _gemini_usage_path_impl()


def _gemini_status(
    path: Path | None = None,
    settings_path: Path | None = None,
) -> dict[str, Any]:
    return _gemini_status_impl(path=path, settings_path=settings_path)


def _gemini_auth_type(path: Path | None = None) -> str | None:
    return _gemini_auth_type_impl(path)


def _gemini_quota_description(auth_type: str | None) -> str:
    return _gemini_quota_description_impl(auth_type)


def _with_last_available(status: dict[str, Any]) -> dict[str, Any]:
    provider = str(status["provider"])
    now = time.monotonic()
    status = _without_expired_windows(status, time.time())
    if status.get("available"):
        previous = _last_available.get(provider)
        if (
            provider == "claude"
            and status.get("stale")
            and previous
            and not previous[1].get("stale")
            and now - previous[0] <= _STALE_USAGE_SECONDS
        ):
            retained = _without_expired_windows(previous[1], time.time())
            if retained.get("available"):
                age = now - previous[0]
                retained["stale"] = age > 15 * 60
                retained["message"] = (
                    "Dernière actualisation /usage Claude"
                    if age <= 15 * 60
                    else "Dernière actualisation /usage Claude · mesure ancienne"
                )
                return retained
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


def _without_expired_windows(
    status: dict[str, Any],
    now: float,
) -> dict[str, Any]:
    windows = status.get("windows")
    if not isinstance(windows, list):
        return status
    valid = [
        window
        for window in windows
        if not isinstance(window.get("resets_at"), (int, float))
        or float(window["resets_at"]) > now
    ]
    if len(valid) == len(windows):
        return status
    result = dict(status)
    result["windows"] = valid
    result["available"] = bool(valid)
    if not valid:
        result["message"] = "Quota expiré · actualisation en attente"
    return result


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


def admit_route(
    route: Route,
    statuses: list[dict[str, Any]],
    *,
    forced_agent: bool = False,
    forced_mode: bool = False,
    now: float | None = None,
) -> tuple[Route, dict[str, Any] | None]:
    """Select an affordable workflow without treating unknown quota as empty."""
    if not statuses:
        return route, None
    timestamp = time.time() if now is None else now
    threshold = _workflow_threshold(route)
    by_provider = {str(item.get("provider")): item for item in statuses}
    capacity = {
        provider: _provider_capacity(
            by_provider.get(provider), threshold, timestamp
        )
        for provider in ("codex", "claude", "gemini")
    }
    constrained = {
        provider: detail
        for provider, (eligible, _, detail) in capacity.items()
        if eligible is False
    }
    if forced_mode:
        relevant = {
            name: detail
            for name, detail in constrained.items()
            if name in {route.primary, route.reviewer}
        }
        if relevant:
            return route, {
                "level": "warning",
                "message": (
                    "Workflow forcé malgré les quotas connus : "
                    + ", ".join(
                        f"{name} ({detail})"
                        for name, detail in relevant.items()
                    )
                ),
                "forced": True,
            }
        return route, None
    forced_provider_warning = (
        constrained.get(route.primary) if forced_agent else None
    )

    eligible = [
        provider
        for provider in ("codex", "claude", "gemini")
        if capacity[provider][0] is not False
    ]
    eligible.sort(
        key=lambda provider: (
            provider == route.primary,
            capacity[provider][1] if capacity[provider][1] is not None else -1,
            provider != "gemini",
        ),
        reverse=True,
    )
    if forced_agent and route.primary not in eligible:
        eligible.insert(0, route.primary)

    needed = 2 if route.mode in {Mode.REVIEW, Mode.CONSENSUS} else 1
    if len(eligible) < needed:
        if not eligible:
            fast_threshold = 3 if route.intent.value == "answer" else 8
            affordable_single = [
                provider
                for provider in ("codex", "claude", "gemini")
                if _provider_capacity(
                    by_provider.get(provider), fast_threshold, timestamp
                )[0]
                is not False
            ]
            if affordable_single:
                affordable_single.sort(
                    key=lambda provider: (
                        _provider_capacity(
                            by_provider.get(provider),
                            fast_threshold,
                            timestamp,
                        )[1]
                        or -1
                    ),
                    reverse=True,
                )
                single = affordable_single[0]
                return Route(
                    route.intent,
                    Mode.FAST,
                    single,
                    None,
                    f"{route.reason}; quota-workflow={route.mode.value}->fast",
                ), {
                    "level": "warning",
                    "message": (
                        f"Réserve insuffisante pour {route.mode.value.upper()} : "
                        f"{single.capitalize()} répond seul. "
                        "Force le mode pour tenter le workflow complet."
                    ),
                    "forced": False,
                }
            return route, {
                "level": "error",
                "message": (
                    "Aucun fournisseur ne dispose d’une réserve connue suffisante. "
                    "Choisis explicitement un agent ou un mode pour tenter quand même."
                ),
                "forced": False,
                "blocked": True,
            }
        reduced = Route(
            route.intent,
            Mode.FAST,
            eligible[0],
            None,
            f"{route.reason}; quota-workflow={route.mode.value}->fast",
        )
        return reduced, {
            "level": "warning",
            "message": (
                f"Réserve insuffisante pour {route.mode.value.upper()} : "
                f"{eligible[0].capitalize()} répond seul. "
                "Force le mode pour tenter le workflow complet."
            ),
            "forced": False,
        }

    original_reviewer = route.reviewer
    if needed == 2 and not original_reviewer:
        original_reviewer = (
            "claude" if route.primary == "codex" else "codex"
        )
    primary = route.primary if route.primary in eligible else eligible[0]
    others = [provider for provider in eligible if provider != primary]
    reviewer = others[0] if needed == 2 else route.reviewer
    adjusted = Route(
        route.intent,
        route.mode,
        primary,
        reviewer,
        route.reason,
    )
    changes = []
    if primary != route.primary:
        changes.append(f"{route.primary}->{primary}")
    if needed == 2 and reviewer != original_reviewer:
        changes.append(f"{original_reviewer}->{reviewer}")
    if not changes:
        if forced_provider_warning:
            return adjusted, {
                "level": "warning",
                "message": (
                    f"{route.primary.capitalize()} est forcé malgré sa réserve "
                    f"connue ({forced_provider_warning})."
                ),
                "forced": True,
            }
        return adjusted, None
    adjusted = Route(
        adjusted.intent,
        adjusted.mode,
        adjusted.primary,
        adjusted.reviewer,
        f"{adjusted.reason}; quota-admission={','.join(changes)}",
    )
    message = "Participants adaptés aux quotas : " + ", ".join(changes) + "."
    if forced_provider_warning:
        message += (
            f" {route.primary.capitalize()} reste forcé malgré "
            f"{forced_provider_warning}."
        )
    return adjusted, {
        "level": "info",
        "message": message,
        "forced": bool(forced_provider_warning),
    }


def _workflow_threshold(route: Route) -> float:
    if route.mode is Mode.CONSENSUS:
        return 20
    if route.mode is Mode.REVIEW:
        return 12
    if route.intent.value == "answer":
        return 3
    return 8


def _provider_capacity(
    status: dict[str, Any] | None,
    threshold: float,
    now: float,
) -> tuple[bool | None, float | None, str]:
    if status is None:
        return None, None, "quota inconnu"
    if not status.get("available"):
        return False, 0, str(status.get("message") or "indisponible")
    windows = status.get("windows") or []
    windows = [
        window
        for window in windows
        if not isinstance(window.get("resets_at"), (int, float))
        or float(window["resets_at"]) > now
    ]
    if not windows:
        return None, None, "plafond non exposé"
    limiting = min(
        windows,
        key=lambda window: float(window.get("remaining_percent", 100)),
    )
    remaining = float(limiting.get("remaining_percent", 100))
    reset = limiting.get("resets_at")
    seconds = float(reset) - now if isinstance(reset, (int, float)) else None
    enough = remaining >= threshold
    if not enough and remaining > 5 and seconds is not None and seconds <= 7200:
        enough = True
    return (
        enough,
        remaining,
        f"{remaining:g} % restant sur {limiting.get('name', 'quota')}",
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
        if seconds is not None and seconds <= 0:
            continue
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
    return _claude_status_impl(
        path,
        unavailable=_unavailable,
        now_timestamp=time.time(),
        stale_usage_seconds=_STALE_USAGE_SECONDS,
    )


def _refresh_claude_status(timeout: int = 18) -> dict[str, Any]:
    return _refresh_claude_status_impl(
        timeout,
        refresh_via_tmux=_refresh_claude_via_tmux,
        parse_screen=_parse_claude_usage_screen,
        fallback_reader=_claude_status,
        subprocess_module=subprocess,
        pexpect_module=pexpect,
        os_module=os,
        time_module=time,
    )


def _refresh_claude_via_tmux(timeout: int) -> dict[str, Any] | None:
    return _refresh_claude_via_tmux_impl(
        timeout,
        parse_screen=_parse_claude_usage_screen,
        subprocess_module=subprocess,
        shutil_module=shutil,
        time_module=time,
        uuid_module=uuid,
    )


def _codex_status() -> dict[str, Any]:
    return _codex_status_impl(
        __version__,
        _unavailable,
        subprocess_module=subprocess,
        selectors_module=selectors,
        os_module=os,
        time_module=time,
    )


def _send(process: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    _send_impl(process, message)


def _unavailable(provider: str, message: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "available": False,
        "plan": None,
        "windows": [],
        "message": message,
    }
