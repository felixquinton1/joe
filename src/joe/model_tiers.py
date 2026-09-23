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

Un modèle facturé au jeton reste hors du routage automatique, quel que soit son
niveau : engager une dépense hors abonnement est une décision de l'utilisateur,
pas une conséquence du choix d'un mode.
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
#
# La table est indexée par fournisseur, parce qu'un identifiant ne suffit pas
# à désigner un modèle : `claude-sonnet-4-6` est une génération précédente
# dans le catalogue de Claude Code, et le Sonnet le plus récent qu'Antigravity
# propose. Une table commune donnait à l'un le classement de l'autre.
DECLARED: dict[str, dict[str, dict[str, Any]]] = {
    "claude": {
        # Haiku coûte moins cher que Sonnet, mais sa fenêtre est trop courte
        # pour du code : à niveau égal, Joe a toujours préféré Sonnet, et
        # cette préférence était noyée dans un score sur les noms de modèles.
        "claude-sonnet-5": {"tier": "light", "preference": 1},
        "claude-haiku-4-5": {"tier": "light", "preference": 2},
        "claude-opus-5-5": {"tier": "strong", "preference": 1},
        # Facturé au jeton, hors abonnement : le routage ne doit jamais
        # l'engager sans qu'on l'ait demandé. Il reste choisissable à la main.
        "claude-fable-5-1": {"tier": "strong", "metered": True},
        "claude-opus-5": {"tier": "legacy"},
        "claude-opus-4-8": {"tier": "legacy"},
        "claude-sonnet-4-6": {"tier": "legacy"},
    },
    # `agy models` n'imprime que « slug<TAB>libellé » : ni description, ni
    # coût, donc aucun signal à lire. Sans cette table rien n'était classé, le
    # niveau retombait sur un index, et « standard » servait une génération 3.6
    # là où « strong » servait une 3.8.
    #
    # Ce qui suit reprend le nom publié par Google : Flash est la gamme rapide,
    # Pro la gamme capable, et le numéro ordonne les générations d'une gamme.
    "antigravity": {
        "gemini-3.8-flash-low": {"tier": "light", "preference": 1},
        "gemini-3.8-flash-medium": {"tier": "standard", "preference": 1},
        "gemini-3.8-flash-high": {"tier": "standard", "preference": 2},
        "gemini-3.1-pro-low": {"tier": "standard", "preference": 3},
        "gemini-3.1-pro-high": {"tier": "strong", "preference": 1},
        "claude-opus-4-6-thinking": {"tier": "strong", "preference": 2},
        # Le classer « legacy » comme chez Claude Code le retirerait du routage
        # sans rien mettre à la place : Antigravity ne publie pas de Sonnet
        # plus récent.
        "claude-sonnet-4-6": {"tier": "standard", "preference": 4},
        "gemini-3.7-flash-high": {"tier": "legacy"},
        "gemini-3.7-flash-medium": {"tier": "legacy"},
        "gemini-3.7-flash-low": {"tier": "legacy"},
        "gemini-3.6-flash-high": {"tier": "legacy"},
        "gemini-3.6-flash-medium": {"tier": "legacy"},
        "gemini-3.6-flash-low": {"tier": "legacy"},
        # `gpt-oss-120b-medium` n'est pas déclaré : rien de publié ne dit où il
        # se place. Il reste choisissable à la main, jamais automatiquement.
    },
}


def _declared(model: dict[str, Any], provider: str) -> dict[str, Any]:
    """Sans fournisseur nommé, aucune entrée ne s'applique.

    Chercher l'identifiant dans toutes les tables ramènerait la collision que
    l'indexation par fournisseur corrige.
    """
    return DECLARED.get(provider, {}).get(str(model.get("id", "")), {})


def model_tier(model: dict[str, Any], provider: str = "") -> str:
    """`light`, `standard`, `strong`, `legacy`, ou `""` si rien ne le dit."""
    declared = _declared(model, provider)
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


def is_metered(model: dict[str, Any], provider: str = "") -> bool:
    """Facturé à l'usage plutôt que couvert par l'abonnement.

    Un tel modèle n'entre jamais dans un choix automatique : la dépense doit
    venir d'une décision, pas du mode retenu pour une demande.
    """
    declared = _declared(model, provider)
    return bool(declared.get("metered") or model.get("metered"))


def _preference(model: dict[str, Any], provider: str = "") -> tuple[int, int, str]:
    identifier = str(model.get("id", ""))
    declared = _declared(model, provider)
    explicit = declared.get("preference")
    cost = model.get("cost_tier")
    priority = model.get("priority")
    return (
        int(explicit) if isinstance(explicit, (int, float)) else 50,
        int(cost) if isinstance(cost, (int, float))
        else int(priority) if isinstance(priority, (int, float)) else 50,
        identifier,
    )


def choose(
    models: list[dict[str, Any]], tier: str, provider: str = ""
) -> str | None:
    """Le modèle du niveau demandé, ou le plus proche vers le bas.

    Renvoie `None` quand aucun modèle n'est classé : l'appelant garde alors son
    comportement d'origine plutôt que de deviner.
    """
    ranked = {name: index for index, name in enumerate(TIERS)}
    classified = [
        (model, model_tier(model, provider)) for model in models
    ]
    usable = [
        (model, kind)
        for model, kind in classified
        if kind in ranked and not is_metered(model, provider)
    ]
    if not usable:
        return None
    wanted = ranked.get(tier)
    if wanted is None:
        # « long-context » et tout niveau non listé demandent le haut de gamme.
        wanted = ranked["strong"]
    def rank(model: dict[str, Any]) -> tuple[int, int, str]:
        return _preference(model, provider)

    exact = [model for model, kind in usable if ranked[kind] == wanted]
    if exact:
        return str(min(exact, key=rank).get("id"))
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
        below, key=lambda model: -ranked[model_tier(model, provider)]
    ) or above
    return str(min(candidates, key=rank).get("id")) if candidates else None
