from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

from .provider_registry import get_provider_specs

_cache: tuple[float, dict[str, Any]] | None = None


def provider_capabilities(refresh: bool = False) -> dict[str, Any]:
    global _cache
    if not refresh and _cache and time.monotonic() - _cache[0] < 300:
        return _cache[1]
    specialized = {
        "codex": _codex,
        "claude": {
            "available": bool(shutil.which("claude")),
            "models": _models("opus", "sonnet", "fable"),
            "efforts": ["low", "medium", "high", "xhigh", "max"],
            "execution_modes": [
                _mode("auto", "Automatique"),
                _mode("plan", "Plan — lecture seule"),
                _mode("acceptEdits", "Modifications autorisées"),
                _mode(
                    "danger-full-access",
                    "Accès complet — confirmation avant chaque run",
                ),
                _mode("dontAsk", "Refuser les permissions non accordées"),
            ],
        },
        "gemini": {
            "available": bool(shutil.which("gemini")),
            "models": _models("auto"),
            "efforts": [],
            "execution_modes": [
                _mode("auto", "Automatique"),
                _mode("plan", "Plan — lecture seule"),
                _mode("auto_edit", "Modifications autorisées"),
                _mode(
                    "danger-full-access",
                    "Accès complet — confirmation avant chaque run",
                ),
            ],
        },
        "copilot": {
            "available": bool(shutil.which("copilot")),
            "models": _models("auto"),
            "efforts": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
            "execution_modes": [
                _mode("auto", "Automatique"),
                _mode("plan", "Plan — lecture seule"),
                _mode("modify", "Modifications autorisées"),
            ],
        },
    }
    result = {}
    for provider in get_provider_specs():
        capabilities = specialized.get(provider.name)
        result[provider.name] = (
            capabilities()
            if callable(capabilities)
            else capabilities
            or {
                "available": bool(shutil.which(provider.name)),
                "models": [],
                "efforts": [],
                "execution_modes": [],
            }
        )
    _cache = (time.monotonic(), result)
    return result


def cached_provider_capabilities() -> dict[str, Any]:
    """Return discovered capabilities without running provider commands."""
    return _cache[1] if _cache else {}


def provider_defaults(
    provider: str,
    model: str | None = None,
    effort: str | None = None,
) -> tuple[str | None, str | None]:
    capabilities = cached_provider_capabilities() or provider_capabilities()
    models = capabilities.get(provider, {}).get("models", [])
    selected_model = model or (
        str(models[0]["id"]) if models else None
    )
    selected = next(
        (
            item for item in models
            if str(item.get("id")) == selected_model
        ),
        None,
    )
    selected_effort = effort or (
        str(selected["default_effort"])
        if selected and selected.get("default_effort")
        else None
    )
    return selected_model, selected_effort


def select_model(provider: str, *, complex_request: bool) -> str | None:
    """Choose from published capabilities without assuming model names."""
    capabilities = cached_provider_capabilities() or provider_capabilities()
    models = list(capabilities.get(provider, {}).get("models", []))
    if not models:
        return None
    if complex_request:
        return str(models[0].get("id"))

    def score(item: dict[str, Any]) -> tuple[int, int, int, str]:
        identifier = str(item.get("id", "")).lower()
        speed = {str(value).lower() for value in item.get("speed_tiers", [])}
        description = str(item.get("description", "")).lower()
        fast = int(bool(speed & {"fast", "quick", "mini"}))
        fast += int(any(word in identifier or word in description
                        for word in ("mini", "flash", "haiku", "sonnet", "terra", "luna")))
        priority = item.get("priority")
        priority_value = int(priority) if isinstance(priority, (int, float)) else 99
        cost = item.get("cost_tier")
        # When a provider omits pricing, a lower catalog priority is the
        # provider's own signal for a lighter/cheaper tier.
        cost_value = (
            int(cost) if isinstance(cost, (int, float)) else -priority_value
        )
        return (-fast, cost_value, priority_value, identifier)

    return str(min(models, key=score).get("id"))


def _codex() -> dict[str, Any]:
    models = []
    if shutil.which("codex"):
        try:
            completed = subprocess.run(
                ["codex", "debug", "models"],
                text=True,
                capture_output=True,
                timeout=8,
                check=False,
            )
            payload = json.loads(completed.stdout)
            catalog = payload.get("models", payload)
            for item in catalog:
                if item.get("visibility") not in {None, "list"}:
                    continue
                models.append(
                    {
                        "id": item["slug"],
                        "label": item.get("display_name", item["slug"]),
                        "default_effort": item.get("default_reasoning_level"),
                        "description": item.get("description", ""),
                        "priority": item.get("priority"),
                        "speed_tiers": item.get("additional_speed_tiers", []),
                        "efforts": [
                            level["effort"]
                            for level in item.get("supported_reasoning_levels", [])
                        ],
                    }
                )
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError):
            pass
    return {
        "available": bool(shutil.which("codex")),
        "models": models,
        "efforts": [],
        "execution_modes": [
            _mode("auto", "Automatique"),
            _mode("read-only", "Lecture seule"),
            _mode("workspace-write", "Commandes et modifications du projet"),
            _mode(
                "danger-full-access",
                "Accès complet — Git et commandes hors sandbox",
            ),
        ],
    }


def _models(*names: str) -> list[dict[str, Any]]:
    return [
        {
            "id": name,
            "label": name,
            "cost_tier": index + 1,
            "speed_tiers": ["fast"] if name in {"sonnet", "fable"} else [],
        }
        for index, name in enumerate(names)
    ]


def _mode(identifier: str, label: str) -> dict[str, str]:
    return {"id": identifier, "label": label}
