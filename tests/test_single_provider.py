"""Une machine avec une seule CLI doit fonctionner, pas echouer."""

from __future__ import annotations

from pathlib import Path

import pytest

from joe.models import Intent, Mode
from joe.orchestrator import Orchestrator
from joe.providers import Provider
from joe.router import Router


def test_the_router_never_names_a_provider_the_machine_lacks():
    """Il nommait codex sur toute demande de code, installe ou non.

    Le repli rattrapait la situation, donc rien ne semblait casse : l'interface
    annoncait simplement un agent que la machine n'a pas.
    """
    seul = Router(frozenset({"claude"}))

    for demande in ("Corrige ce bug de tri", "Explique cette fonction",
                    "Implemente un cache"):
        assert seul.route(demande).primary == "claude", demande

    # Sans contrainte declaree, le comportement d'origine est conserve.
    assert Router().route("Corrige ce bug de tri").primary == "codex"


def test_a_lone_provider_answers_instead_of_failing():
    """Relire ou arbitrer demande un second intervenant.

    Sans lui, la seconde etape ne trouvait personne et le run echouait sur
    « All providers failed » — pour une question parfaitement ordinaire.
    """
    seul = Router(frozenset({"claude"}))

    for demande in (Mode.REVIEW, Mode.CONSENSUS):
        route = seul.route("Corrige ce bug", forced_mode=demande)
        assert route.mode is Mode.FAST
        assert route.reviewer is None
        # La degradation se dit, elle ne se subit pas en silence.
        assert "un seul fournisseur disponible" in route.reason


def test_two_providers_keep_a_real_review():
    """Des qu'un second existe, la relecture retrouve son sens."""
    duo = Router(frozenset({"claude", "gemini"}))

    route = duo.route("Corrige ce bug", forced_mode=Mode.REVIEW)

    assert route.mode is Mode.REVIEW
    assert route.reviewer and route.reviewer != route.primary


def test_an_unavailable_reviewer_is_replaced_not_kept():
    """Nommer un relecteur absent revient a n'en avoir aucun."""
    route = Router(frozenset({"claude", "gemini"})).route(
        "Corrige ce bug", forced_mode=Mode.REVIEW, forced_agent="claude"
    )

    assert route.primary == "claude"
    assert route.reviewer == "gemini"


def test_the_orchestrator_hands_its_providers_to_the_router():
    """Le filtrage portait sur la liste des fournisseurs, pas sur la decision.

    L'orchestrateur ne gardait que ce qui est lancable, mais le routeur, lui,
    n'en savait rien et continuait de designer un absent.
    """
    orchestrateur = Orchestrator(
        Path("/tmp"), providers={"claude": Provider("claude", "claude")}
    )

    assert orchestrateur.router.available == frozenset({"claude"})
    assert orchestrateur.plan("Corrige ce bug de tri").primary == "claude"


@pytest.mark.parametrize("intent", [Intent.ANSWER, Intent.ANALYZE, Intent.MODIFY])
def test_every_intent_stays_answerable_with_one_cli(intent):
    route = Router(frozenset({"claude"})).route("Corrige ce bug de tri")

    assert route.primary == "claude"
    assert route.reviewer in (None, "claude")
