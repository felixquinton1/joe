from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .models import Mode, Route
from .provider_health import apply_cooldowns
from .provider_registry import counterpart, usage_providers, get_provider_spec
from .usage_claude import (
    CLAUDE_STALE_SECONDS,
    claude_unscoped_limit,
    claude_account_plan as _claude_account_plan,
    claude_reset_timestamp as _claude_reset_timestamp,
    claude_status as _claude_status_impl,
    iso_timestamp as _iso_timestamp,
    local_claude_plan as _local_claude_plan,
    parse_claude_usage_screen as _parse_claude_usage_screen,
    refresh_claude_status as _refresh_claude_status_impl,
    refresh_claude_via_tmux as _refresh_claude_via_tmux_impl,
    strip_terminal_codes as _strip_terminal_codes,
)
from .usage_codex import (
    codex_status as _codex_status_impl,
    normalize_codex_usage,
    window_name as _window_name,
)
from .usage_gemini import (
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
            return apply_cooldowns(_cache[1])
    antigravity_installed = bool(shutil.which("agy"))
    fresh = [
        _codex_status(),
        _refresh_claude_status() if force else _claude_status(),
        {
            "provider": "antigravity",
            "available": antigravity_installed,
            "availability_state": (
                "quota_unknown" if antigravity_installed else "unavailable"
            ),
            "plan": None,
            "windows": [],
            "message": (
                "Quota non exposé par Antigravity CLI"
                if antigravity_installed
                else "Antigravity CLI non installée"
            ),
        },
        _unavailable(
            "copilot",
            "Disponible uniquement dans la session interactive Copilot",
        ),
        _deepseek_status(),
    ]
    with _lock:
        providers = [
            item if force and item.get("stale") else _with_last_available(item)
            for item in fresh
        ]
        _cache = (time.monotonic(), providers)
        return apply_cooldowns(providers)


def cached_usage_status() -> list[dict[str, Any]]:
    """Return quota data immediately without probing provider CLIs."""
    with _lock:
        return apply_cooldowns(list(_cache[1])) if _cache else []


def _deepseek_status() -> dict[str, Any]:
    # Le solde d'API n'est pas une fenetre d'abonnement Codex. On ne sonde
    # jamais un modele facture pour l'obtenir.
    from .deepseek_profile import fetch_deepseek_balance, setup_error
    from .providers import resolve_executable

    issue = setup_error()
    installed = bool(resolve_executable("deepseek"))
    return {
        "provider": "deepseek", "label": "DeepSeek",
        "quota_display": "token_billing", "installed": installed, "setup_required": issue,
        "available": installed and issue is None,
        "availability_state": "quota_unknown" if installed and issue is None else "unavailable",
        "plan": "DeepSeek API", "windows": [],
        "balance": fetch_deepseek_balance(),
        "message": issue or ("DeepSeek API: solde et reprise inconnus, facturation par token"
                             if installed else "Codex CLI non installée"),
    }


def _quota_observation_invalid(status: dict[str, Any], now: float) -> bool:
    if status.get("stale") or status.get("refresh_required"):
        return True
    observed_at = status.get("observed_at")
    if (
        status.get("provider") == "claude"
        and isinstance(observed_at, (int, float))
        and now - observed_at > CLAUDE_STALE_SECONDS
    ):
        return True
    return any(
        (
            isinstance(window.get("resets_at"), (int, float))
            and float(window["resets_at"]) <= now
        ) or (
            status.get("provider") == "claude"
            and isinstance(window.get("observed_at"), (int, float))
            and now - window["observed_at"] > CLAUDE_STALE_SECONDS
        )
        for window in status.get("windows") or []
    )


def routing_usage_status() -> list[dict[str, Any]]:
    """Obtenir un instantane de routage avec les regles de cache existantes."""
    statuses = usage_status()
    now = time.time()
    if any(
        item.get("provider") in {"claude", "codex"}
        and (
            _quota_observation_invalid(item, now)
            or (
                item.get("provider") == "claude"
                and not item.get("windows")
                and shutil.which("claude")
            )
        )
        for item in statuses
    ):
        statuses = usage_status(force=True)
    snapshot = []
    for item in statuses:
        result = deepcopy(item)
        if item.get("provider") in {"claude", "codex"} and _quota_observation_invalid(
            item, time.time()
        ):
            # Le repli diagnostique est une preuve, jamais une capacite
            # actuelle. None preserve la semantique existante de quota inconnu.
            result.update(
                last_observation=dict(item), windows=[], available=None,
                availability_state="quota_unknown", stale=True,
                message="Current quota unknown; refresh did not establish valid capacity",
            )
            result["limit_observations"] = [
                {**observation, "governing": False, "reason": "Quota observation is stale"}
                for observation in item.get("limit_observations", [])
            ]
        snapshot.append(result)
    return apply_cooldowns(snapshot)


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
                retained["stale"] = age > CLAUDE_STALE_SECONDS
                retained["message"] = (
                    "Dernière actualisation /usage Claude"
                    if age <= CLAUDE_STALE_SECONDS
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
    result["refresh_required"] = True
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
    # La bascule ne valait que pour deux fournisseurs nommés : tout nouveau
    # venu restait collé à son quota épuisé faute d'être dans la liste. Le
    # registre désigne déjà un pair pour chacun, il suffit de le demander.
    if route.mode is not Mode.FAST:
        return route
    alternative = counterpart(route.primary)
    if not alternative:
        return route
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
    wait_for_provider: bool = False,
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
        for provider in usage_providers()
    }
    for provider in (route.primary, route.reviewer):
        if provider and get_provider_spec(provider).manual_only:
            capacity[provider] = _provider_capacity(by_provider.get(provider), threshold, timestamp)
    constrained = {
        provider: detail
        for provider, (eligible, _, detail) in capacity.items()
        if eligible is False
    }
    if wait_for_provider and capacity.get(route.primary, (None,))[0] is False:
        retry_at = by_provider.get(route.primary, {}).get("retry_at")
        retry_at = retry_at if isinstance(retry_at, (int, float)) and retry_at > timestamp else _next_quota_reset(
            [by_provider[route.primary]] if route.primary in by_provider else [],
            threshold=threshold,
            now=timestamp,
        )
        return route, {
            "level": "warning",
            "message": (
                f"{route.primary.capitalize()} est réservé pour ce projet, mais "
                "sa réserve connue est insuffisante."
                f" {capacity[route.primary][2]}"
            ),
            "forced": False,
            "blocked": True,
            "retry_at": retry_at,
            "reserved_provider": route.primary,
            "automatic_retry": not (
                by_provider.get(route.primary, {}).get("error_kind") in {
                    "authentication", "insufficient_balance", "rate_limit",
                } and by_provider.get(route.primary, {}).get("retry_at") is None
            ),
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
        for provider in usage_providers()
        if capacity[provider][0] is not False
    ]
    eligible.sort(
        key=lambda provider: (
            provider == route.primary,
            capacity[provider][1] if capacity[provider][1] is not None else -1,
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
                for provider in usage_providers()
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
                    "La tâche peut attendre automatiquement le prochain reset."
                ),
                "forced": False,
                "blocked": True,
                "retry_at": _next_quota_reset(
                    statuses,
                    threshold=fast_threshold,
                    now=timestamp,
                ),
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
            counterpart(route.primary)
        )
    primary = route.primary if route.primary in eligible else eligible[0]
    others = [provider for provider in eligible if provider != primary]
    reviewer = (
        original_reviewer
        if needed == 2
        and original_reviewer in eligible
        and original_reviewer != primary
        else (others[0] if needed == 2 else route.reviewer)
    )
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


def next_window_reset(
    provider: str | None = None,
    *,
    now: float | None = None,
) -> float | None:
    """Quand la prochaine fenêtre de quota se recharge-t-elle ?

    Sert à programmer un travail autonome « juste après le rechargement ».
    Contrairement à `_next_quota_reset`, aucun seuil : on veut la bascule de
    fenêtre elle-même, y compris quand il reste du quota. Claude expose des
    fenêtres de 5 h ; les fournisseurs qui n'annoncent pas d'échéance
    renvoient None, et l'appelant réessaie plus tard.
    """
    moment = time.time() if now is None else now
    resets = [
        float(window["resets_at"])
        # `usage_status` garde son propre cache de 60 s : s'appuyer sur
        # `cached_usage_status` figerait un instantané périmé et un plan calé
        # sur le rechargement n'y trouverait jamais d'échéance à venir.
        for status in usage_status()
        if not provider or status.get("provider") == provider
        for window in status.get("windows") or []
        if isinstance(window.get("resets_at"), (int, float))
        and float(window["resets_at"]) > moment
    ]
    return min(resets) if resets else None


def _next_quota_reset(
    statuses: list[dict[str, Any]],
    *,
    threshold: float,
    now: float,
) -> float | None:
    """Return the first future reset that can change a quota decision."""
    resets = []
    for status in statuses:
        for window in status.get("windows") or []:
            reset = window.get("resets_at")
            remaining = window.get("remaining_percent")
            if (
                isinstance(reset, (int, float))
                and float(reset) > now
                and isinstance(remaining, (int, float))
                and float(remaining) < threshold
            ):
                resets.append(float(reset))
    return min(resets) if resets else None


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
        detail = str(status.get("message") or "disponibilité inconnue")
        state = status.get("availability_state")
        known_exhaustion = state in {
            "quota_exhausted",
            "temporarily_unavailable",
            "unavailable",
            "balance_exhausted", "rate_limited", "authentication_required",
        } or any(
            marker in detail.lower()
            for marker in (
                "quota épuisé",
                "limite atteinte",
                "pause temporaire",
                "authentication",
                "authentification",
            )
        )
        if known_exhaustion:
            return False, 0, detail
        return None, None, detail
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
    status = _claude_status_impl(
        path,
        unavailable=_unavailable,
        now_timestamp=time.time(),
        stale_usage_seconds=_STALE_USAGE_SECONDS,
    )
    if path is None and not status.get("plan"):
        status["plan"] = _claude_account_plan()
    return status


def _refresh_claude_status(timeout: int = 18) -> dict[str, Any]:
    plan = _local_claude_plan() or _claude_account_plan()
    started_at = time.time()
    def parse_screen(output: str) -> Any:
        return _parse_claude_usage_screen(output, plan=plan)

    status = _refresh_claude_status_impl(
        timeout,
        refresh_via_tmux=_refresh_claude_via_tmux,
        parse_screen=parse_screen,
        fallback_reader=_claude_status,
        subprocess_module=subprocess,
        os_module=os,
        time_module=time,
    )
    if not status.get("stale") and status.get("available"):
        local = _claude_status()
        observations = local.get("limit_observations", [])
        status["limit_observations"] = observations
        if not local.get("stale"):
            observed_at = local.get("observed_at")
            if isinstance(observed_at, (int, float)) and observed_at >= started_at:
                current_windows = local.get("windows") or []
                # Une reponse structuree obtenue pendant cette actualisation
                # possede les resets exacts. On remplace ses equivalents rendus
                # en texte, en gardant les autres lignes et les donnees texte.
                status["windows"] = [
                    window for window in status["windows"]
                    if not any(
                        claude_unscoped_limit(current.get("scope"))
                        and (
                            current.get("source_key") in {"five_hour", "seven_day"}
                            or current.get("kind") in {"session", "weekly_all"}
                        )
                        and current.get("duration_minutes") == window.get("duration_minutes")
                        for current in current_windows
                    )
                ] + current_windows
                return status
            # /usage peut enrichir le cache structure de lignes absentes de sa
            # vue texte. On les conserve dans le meme instantane de routage.
            for window in local.get("windows") or []:
                if not window.get("provider_limit"):
                    continue
                duplicate = next((existing for existing in status["windows"] if all(
                    existing.get(key) == window.get(key)
                    for key in ("scope", "duration_minutes", "remaining_percent", "resets_at")
                )), None)
                if duplicate is None:
                    status["windows"].append(window)
                else:
                    duplicate.update({key: value for key, value in window.items() if key != "name"})
        else:
            status["limit_observations"] = [
                {**observation, "governing": False, "reason": "Structured cache is stale"}
                for observation in observations
            ]
            if any(
                observation.get("applicable")
                and not claude_unscoped_limit(observation.get("scope"))
                and isinstance(observation.get("is_active"), bool)
                for observation in observations
            ):
                status["stale"] = True
                status["message"] = "Claude /usage refreshed; structured limits remain stale"
    return status


def _refresh_claude_via_tmux(timeout: int) -> dict[str, Any] | None:
    return _refresh_claude_via_tmux_impl(
        timeout,
        parse_screen=lambda output: _parse_claude_usage_screen(
            output,
            plan=_local_claude_plan(),
        ),
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
        time_module=time,
    )


def _unavailable(provider: str, message: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "available": False,
        "plan": None,
        "windows": [],
        "message": message,
    }
