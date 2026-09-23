from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

from .model_tiers import choose, is_metered
from .providers import resolve_executable
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
            "models": _CLAUDE_MODELS,
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
        "antigravity": _antigravity,
        "cursor-agent": _cursor,
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


def _auto_selectable(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ce dans quoi un choix automatique a le droit de puiser.

    Un modèle facturé au jeton reste dans le catalogue — on peut le choisir à
    la main — mais aucune bascule automatique ne doit engager une dépense hors
    abonnement. Si tout est au jeton, on rend la liste complète plutôt que rien.
    """
    free = [model for model in models if not is_metered(model)]
    return free or models


def select_model(provider: str, *, complex_request: bool) -> str | None:
    """Choose from published capabilities without assuming model names."""
    capabilities = cached_provider_capabilities() or provider_capabilities()
    models = _auto_selectable(
        list(capabilities.get(provider, {}).get("models", []))
    )
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


def select_model_tier(provider: str, tier: str) -> str | None:
    """Map an abstract routing tier to the provider's current model catalog.

    Le niveau se résolvait par un index : « strong » prenait le premier modèle,
    « standard » le milieu de la liste. Sur le catalogue Claude, ce milieu tombe
    sur le modèle le plus cher des six — plus cher que celui rendu pour
    « strong ». Le niveau demandé n'avait aucun rapport avec le modèle obtenu.
    """
    models = _auto_selectable(
        list(
            (cached_provider_capabilities() or provider_capabilities())
            .get(provider, {})
            .get("models", [])
        )
    )
    if not models:
        return None
    declared = choose(models, tier)
    if declared:
        return declared
    # Aucun modèle classé : on garde l'ancien comportement plutôt que de
    # deviner. Une gamme entièrement nouvelle est routée comme avant.
    if tier == "light":
        return select_model(provider, complex_request=False)
    if tier in {"strong", "long-context"}:
        return str(models[0].get("id"))
    if len(models) <= 2:
        return str(models[0].get("id"))
    return str(models[len(models) // 2].get("id"))


def _codex() -> dict[str, Any]:
    models = []
    if shutil.which("codex"):
        try:
            completed = subprocess.run(
                ["codex", "debug", "models"],
                text=True,
                capture_output=True,
                timeout=8,
                check=False, encoding="utf-8", errors="replace"
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


def _cursor_models(output: str) -> list[dict[str, Any]]:
    """Keep only the lines that can be a model identifier.

    Le format de `cursor-agent models` n'a pas pu être observé : aucun compte
    n'était disponible. On ne retient donc que ce qui a la forme d'un
    identifiant — un seul mot, sans ponctuation de phrase — ce qui écarte les
    en-têtes et les puces. Rien de reconnaissable, aucun modèle : l'interface
    retombe sur « défaut du fournisseur », qui reste utilisable.
    """
    models = []
    for line in output.splitlines():
        candidate = line.strip().lstrip("*-• ").strip()
        if not 2 <= len(candidate) <= 60:
            continue
        if not all(character.isalnum() or character in "-._" for character in candidate):
            continue
        models.append({"id": candidate, "label": candidate, "cost_tier": 1})
    return models


def _antigravity() -> dict[str, Any]:
    """`agy models` imprime « slug<TAB>libelle », une ligne par modele.

    Aucune description ni cout publie : le niveau se deduit du libelle, que
    `model_tiers` sait lire. Un modele non reconnu laisse Joe router comme
    avant plutot que de deviner.
    """
    executable = resolve_executable("antigravity")
    models = []
    if executable:
        try:
            completed = subprocess.run(
                [executable, "models"],
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        for line in (completed.stdout if completed else "").splitlines():
            slug, separator, label = line.partition("\t")
            slug = slug.strip()
            if not separator or not slug or " " in slug:
                continue
            models.append({"id": slug, "label": label.strip() or slug})
    return {
        "available": bool(executable),
        "models": models,
        # L'effort est aussi encode dans le slug ; le drapeau reste accepte.
        "efforts": ["low", "medium", "high"],
        "execution_modes": [
            _mode("auto", "Automatique"),
            _mode("plan", "Plan — lecture seule"),
            _mode("acceptEdits", "Modifications autorisées"),
            _mode("danger-full-access", "Accès complet — toutes permissions"),
            _mode("dontAsk", "Bac à sable"),
        ],
    }


def _cursor() -> dict[str, Any]:
    executable = shutil.which("cursor-agent")
    models: list[dict[str, Any]] = []
    if executable:
        try:
            completed = subprocess.run(
                [executable, "models"],
                text=True,
                capture_output=True,
                timeout=8,
                check=False, encoding="utf-8", errors="replace"
            )
            if completed.returncode == 0:
                models = _cursor_models(completed.stdout)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "available": bool(executable),
        "models": models,
        # Aucun équivalent de l'effort n'est documenté sur cette CLI.
        "efforts": [],
        "execution_modes": [
            _mode("auto", "Automatique"),
            _mode("plan", "Plan — lecture seule"),
            _mode("workspace-write", "Modifications autorisées"),
            _mode("danger-full-access", "Accès complet"),
        ],
    }


# Le CLI Claude n'expose aucun listing de modèles, contrairement à Codex : ce
# catalogue est donc tenu à la main. Les identifiants sont complets et non des
# alias — « opus » suit le défaut du CLI, qui reste une version en arrière et
# affichait Opus 4.8 alors que le 5 est disponible. `cost_tier` suit le prix
# publié, `models[0]` est le défaut proposé.
_CLAUDE_MODELS: list[dict[str, Any]] = [
    {
        "id": "claude-opus-5",
        "label": "Opus 5",
        "description": "Le meilleur rapport qualité-prix pour le travail complexe.",
        "cost_tier": 4,
    },
    {
        # Palier rapide de la gamme : c'est lui que le routage automatique
        # choisit pour une demande simple. Haiku est moins cher encore, mais
        # trop court pour du code — il reste sélectionnable à la main.
        "id": "claude-sonnet-5",
        "label": "Sonnet 5",
        "description": "Rapide et économique, pour le volume.",
        "cost_tier": 2,
        "speed_tiers": ["fast"],
    },
    {
        "id": "claude-haiku-4-5",
        "label": "Haiku 4.5",
        "description": "Le plus léger, pour les tâches simples.",
        "cost_tier": 1,
    },
    {
        "id": "claude-fable-5-1",
        "label": "Fable 5.1",
        "description": "Le plus capable, pour le raisonnement long. Coût élevé.",
        "cost_tier": 5,
    },
    {
        "id": "claude-opus-4-8",
        "label": "Opus 4.8",
        "description": "Génération précédente d'Opus.",
        "cost_tier": 4,
    },
    {
        "id": "claude-sonnet-4-6",
        "label": "Sonnet 4.6",
        "description": "Génération précédente de Sonnet.",
        "cost_tier": 3,
    },
]


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
