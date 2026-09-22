"""Quel modèle vaut quoi, pour choisir autrement que par sa place dans la liste.

`select_model_tier` résolvait un niveau abstrait par un index : « strong »
prenait `models[0]`, « standard » le milieu du tableau. Sur le catalogue Claude,
le milieu tombe sur le modèle le plus cher des six — plus cher que celui rendu
pour « strong ». Le niveau demandé n'avait donc aucun rapport avec le modèle
obtenu.

La matière existait déjà : Claude publie un `cost_tier` et une description,
Codex une description, une priorité et des paliers de vitesse. Personne ne la
lisait. Ce module en déduit un niveau, et un catalogue déclaré tranche là où la
description ne suffit pas.

Un modèle inconnu ne reçoit aucun niveau : l'appelant retombe alors sur son
comportement d'origine. Une nouvelle sortie chez un fournisseur ne casse donc
rien, elle est simplement routée comme avant jusqu'à ce que sa description soit
reconnue ou son entrée ajoutée.
"""

from __future__ import annotations

from typing import Any

TIERS = ("light", "standard", "strong")

# Ce que les fournisseurs écrivent eux-mêmes de leurs modèles. C'est leur
# formulation qui fait foi : une nouvelle sortie décrite comme « fast and
# affordable » est classée le jour de sa publication, sans rien éditer ici.
_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "legacy",
        ("previous-generation", "previous generation", "génération précédente"),
    ),
    (
        "strong",
        (
            "most capable", "plus capable", "complex, demanding",
            "travail complexe", "raisonnement long", "hardest",
        ),
    ),
    (
        "light",
        (
            "fast and affordable", "économique", "plus léger", "tâches simples",
            "lightweight", "for volume", "pour le volume",
        ),
    ),
    (
        "standard",
        (
            "balanced", "everyday", "workhorse", "équilibré", "courant",
            "general work",
        ),
    ),
)

# Ce que la description ne dit pas. Une entrée ici prime sur tout le reste.
# `preference` départage à l'intérieur d'un niveau, du plus souhaitable au
# moins ; sans elle, c'est le coût qui tranche.
DECLARED: dict[str, dict[str, Any]] = {
    # Haiku coûte moins cher que Sonnet, mais sa fenêtre est trop courte pour
    # du code : à niveau égal, Joe a toujours préféré Sonnet, et cette
    # préférence était jusqu'ici noyée dans un score sur les noms de modèles.
    "claude-sonnet-5": {"tier": "light", "preference": 1},
    "claude-haiku-4-5": {"tier": "light", "preference": 2},
    "claude-opus-5": {"tier": "strong", "preference": 1},
    # Décrit par le catalogue comme le plus capable et le plus cher : c'est un
    # choix de dernier recours, jamais un défaut.
    "claude-fable-5-1": {"tier": "strong", "preference": 2},
    "claude-opus-4-8": {"tier": "legacy"},
    "claude-sonnet-4-6": {"tier": "legacy"},
}


def model_tier(model: dict[str, Any]) -> str:
    """`light`, `standard`, `strong`, `legacy`, ou `""` si rien ne le dit."""
    identifier = str(model.get("id", ""))
    declared = DECLARED.get(identifier, {})
    if declared.get("tier"):
        return str(declared["tier"])
    if model.get("tier"):
        return str(model["tier"])
    haystack = " ".join(
        str(model.get(key, "")) for key in ("description", "label", "id")
    ).lower()
    for tier, words in _SIGNALS:
        if any(word in haystack for word in words):
            return tier
    cost = model.get("cost_tier")
    if isinstance(cost, (int, float)):
        return "light" if cost <= 2 else "standard" if cost == 3 else "strong"
    return ""


def _preference(model: dict[str, Any]) -> tuple[int, int, str]:
    identifier = str(model.get("id", ""))
    declared = DECLARED.get(identifier, {})
    explicit = declared.get("preference")
    cost = model.get("cost_tier")
    priority = model.get("priority")
    return (
        int(explicit) if isinstance(explicit, (int, float)) else 50,
        int(cost) if isinstance(cost, (int, float))
        else int(priority) if isinstance(priority, (int, float)) else 50,
        identifier,
    )


def choose(models: list[dict[str, Any]], tier: str) -> str | None:
    """Le modèle du niveau demandé, ou le plus proche vers le bas.

    Renvoie `None` quand aucun modèle n'est classé : l'appelant garde alors son
    comportement d'origine plutôt que de deviner.
    """
    ranked = {name: index for index, name in enumerate(TIERS)}
    classified = [
        (model, model_tier(model)) for model in models
    ]
    usable = [
        (model, kind) for model, kind in classified if kind in ranked
    ]
    if not usable:
        return None
    wanted = ranked.get(tier)
    if wanted is None:
        # « long-context » et tout niveau non listé demandent le haut de gamme.
        wanted = ranked["strong"]
    exact = [model for model, kind in usable if ranked[kind] == wanted]
    if exact:
        return str(min(exact, key=_preference).get("id"))
    if wanted == ranked["strong"]:
        # Rien de classé en haut de gamme : servir un modèle léger à une
        # demande explicitement lourde serait pire que l'ancien défaut. On
        # rend la main, l'appelant garde son modèle de tête.
        return None
    # Sinon on descend d'un cran plutôt que de monter : c'est le modèle phare
    # imposé par défaut qu'on cherche à éviter.
    below = [model for model, kind in usable if ranked[kind] < wanted]
    above = [model for model, kind in usable if ranked[kind] > wanted]
    candidates = sorted(
        below, key=lambda model: -ranked[model_tier(model)]
    ) or above
    return str(min(candidates, key=_preference).get("id")) if candidates else None
