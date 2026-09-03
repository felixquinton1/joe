"""Declarative description of every provider Joe can drive.

Chaque comportement propre à un fournisseur était auparavant re-testé par nom
dans `providers.py` et réécrit littéralement dans cinq autres modules. Le signe
le plus net : `watchdog_seconds` existait comme donnée, mais son activation
restait un `if provider == "gemini"`. Ici, un comportement se déclare une fois
et se lit partout.

Ajouter un fournisseur = une entrée dans `PROVIDERS` et une fonction d'argv
dans `providers.py`. L'absence de l'une ou de l'autre échoue à l'import, pas au
premier run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str

    streams_json: bool = False
    """La CLI émet des événements JSON sur stdout plutôt que du texte brut.

    Remplace les listes blanches `{"codex", "claude", "gemini"}` recopiées dans
    `_activity`, `_collect_streams` et `_final_output` : un fournisseur absent
    de ces listes renvoyait silencieusement du stdout brut au lieu d'événements
    analysés.
    """

    watchdog_seconds: float | None = None
    """Délai d'inactivité toléré. `None` = pas de chien de garde.

    Unique commutateur : la présence d'une valeur suffit à armer le watchdog.
    """

    terminal_quota_markers: tuple[str, ...] = ()
    """Marqueurs prouvant un quota définitivement épuisé, même en sortie 0."""

    reviewer_peers: tuple[str, ...] = ()
    """Fournisseurs pouvant relire ce fournisseur, par ordre de préférence.

    La règle « choisis le complémentaire » était écrite cinq fois dans cinq
    modules, ce qui empêchait structurellement Gemini et Copilot d'être
    relecteurs.
    """

    fallbacks: tuple[str, ...] = ()
    """Ordre de repli quand ce fournisseur échoue."""

    minimum_version: str = ""
    """Version minimale connue pour fonctionner."""

    exposes_usage: bool = False
    """La CLI expose une mesure de quota exploitable."""


PROVIDERS = (
    ProviderSpec(
        "codex",
        "Codex",
        streams_json=True,
        reviewer_peers=("claude",),
        fallbacks=("gemini", "claude", "copilot"),
        minimum_version="0.145.0",
        exposes_usage=True,
    ),
    ProviderSpec(
        "claude",
        "Claude",
        streams_json=True,
        reviewer_peers=("codex",),
        fallbacks=("gemini", "codex", "copilot"),
        minimum_version="2.1.197",
        exposes_usage=True,
    ),
    ProviderSpec(
        "gemini",
        "Gemini",
        streams_json=True,
        watchdog_seconds=90,
        terminal_quota_markers=(
            "resource_exhausted",
            "exceeded your current quota",
            "status 429",
        ),
        reviewer_peers=("codex", "claude"),
        fallbacks=("codex", "claude", "copilot"),
        minimum_version="0.52.0",
        exposes_usage=True,
    ),
    ProviderSpec(
        "copilot",
        "Copilot",
        reviewer_peers=("codex", "claude"),
        fallbacks=("gemini", "codex", "claude"),
        minimum_version="1.0.75",
    ),
    # Le nom porte le suffixe `-agent` parce qu'il sert aussi à trouver
    # l'exécutable : `cursor` est l'éditeur, `cursor-agent` la CLI sans fenêtre.
    # Pointer sur le premier ferait croire Cursor disponible sans qu'aucun run
    # ne puisse aboutir. L'interface affiche le libellé, pas ce nom.
    ProviderSpec(
        "cursor-agent",
        "Cursor",
        reviewer_peers=("codex", "claude"),
        fallbacks=("claude", "codex", "gemini"),
    ),
)

_BY_NAME = {provider.name: provider for provider in PROVIDERS}


def get_provider_specs() -> tuple[ProviderSpec, ...]:
    return PROVIDERS


def get_provider_names() -> tuple[str, ...]:
    return tuple(provider.name for provider in PROVIDERS)


def get_provider_catalog() -> tuple[dict[str, str], ...]:
    return tuple(
        {"id": provider.name, "label": provider.label} for provider in PROVIDERS
    )


def get_provider_spec(name: str) -> ProviderSpec:
    """Return one spec, or a neutral one for an unknown name.

    Un nom inconnu ne doit pas faire planter une lecture de comportement : il
    hérite des valeurs les plus prudentes (pas de JSON, pas de watchdog).
    """
    return _BY_NAME.get(name) or ProviderSpec(name, name.capitalize())


def default_fallbacks() -> dict[str, list[str]]:
    return {
        provider.name: list(provider.fallbacks)
        for provider in PROVIDERS
        if provider.fallbacks
    }


def minimum_versions() -> dict[str, str]:
    """Every provider, including those whose working version is unknown.

    Filtrer les versions vides sortait le fournisseur de l'audit : il
    disparaissait du diagnostic au lieu d'y figurer comme non vérifié.
    """
    return {provider.name: provider.minimum_version for provider in PROVIDERS}


def usage_providers() -> tuple[str, ...]:
    return tuple(provider.name for provider in PROVIDERS if provider.exposes_usage)


def counterpart(primary: str, eligible: tuple[str, ...] | None = None) -> str | None:
    """Return the preferred reviewer for `primary`, honouring the registry."""
    allowed = eligible if eligible is not None else get_provider_names()
    for peer in get_provider_spec(primary).reviewer_peers:
        if peer != primary and peer in allowed:
            return peer
    for name in allowed:
        if name != primary:
            return name
    return None
