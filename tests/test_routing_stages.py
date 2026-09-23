"""Chaque etape paie ce qu'elle coute, et le quota restant compte."""

from __future__ import annotations

import pytest

from joe import web_runs
from joe.models import Intent, Mode, Route
from joe.orchestrator_workflows import STAGE_TIERS, stage_model


CATALOGUE = {
    "codex": {"models": [
        {"id": "astra", "description": "Our most capable model for complex work."},
        {"id": "sol", "description": "Reliable agentic workhorse for everyday tasks."},
        {"id": "luna", "description": "Fast and affordable agentic coding model."},
    ]}
}


@pytest.fixture
def catalogue(monkeypatch):
    monkeypatch.setattr(
        "joe.capabilities.cached_provider_capabilities", lambda: CATALOGUE
    )


def test_a_cross_review_costs_less_than_the_proposal_it_reads(catalogue):
    """Une revue croisee lit une proposition et la critique.

    C'est plus leger que de la produire, et une seule etape recevait un modele
    jusqu'ici : toutes les autres partaient sur le defaut de leur CLI, que Joe
    ne controle pas.
    """
    assert stage_model(None, "codex", "proposal", None) == "astra"
    assert stage_model(None, "codex", "cross_review", None) == "sol"
    # La synthese rend la reponse finale et tranche les desaccords.
    assert stage_model(None, "codex", "synthesis", None) == "astra"


def test_the_examiner_is_not_given_a_lighter_model_than_the_author(catalogue):
    """L'examen a cesse de critiquer un texte : il rouvre le code et le corrige.

    Tant qu'il ne faisait que lire, un modele plus leger suffisait. Depuis
    qu'il ecrit, le plafonner reviendrait a confier la seconde passe a moins
    capable que la premiere — l'inverse du but.
    """
    assert STAGE_TIERS["review"] == "strong"
    assert stage_model(None, "codex", "review", None) == "astra"

    # Et le niveau se resout dans le catalogue de l'examinateur : lui passer le
    # modele de l'auteur nommerait un identifiant que sa CLI ne connait pas.
    assert stage_model(None, "claude", "review", None) != "astra"


def test_a_stage_that_follows_the_request_keeps_its_model(catalogue):
    """L'implementation suit le niveau de la demande, pas un niveau fixe."""
    assert STAGE_TIERS["implementation"] is None
    assert stage_model(None, "codex", "implementation", "choisi") == "choisi"
    assert stage_model(None, "codex", "correction", "choisi") == "choisi"
    # Une etape inconnue ne tente rien.
    assert stage_model(None, "codex", "inconnue", "choisi") == "choisi"


def test_an_unknown_catalogue_leaves_the_stage_alone(monkeypatch):
    """Sans modele classe, l'etape garde ce qu'on lui avait donne."""
    monkeypatch.setattr(
        "joe.capabilities.cached_provider_capabilities",
        lambda: {"codex": {"models": []}},
    )
    assert stage_model(None, "codex", "cross_review", "repli") == "repli"


def test_a_shrinking_quota_stops_paying_for_the_flagship(monkeypatch):
    """L'information etait lue pour choisir un fournisseur, jamais sa taille.

    Une question triviale pouvait consommer la derniere tranche d'Opus,
    indisponible ensuite pour ce qui en avait besoin.
    """
    monkeypatch.setattr(web_runs, "_remaining_percent", lambda provider: 8.0)

    assert web_runs._tier_under_quota_pressure("codex", "strong") == "standard"
    assert web_runs._tier_under_quota_pressure("codex", "standard") == "light"
    # On ne descend pas sous le plancher.
    assert web_runs._tier_under_quota_pressure("codex", "light") == "light"


def test_a_healthy_quota_changes_nothing(monkeypatch):
    monkeypatch.setattr(web_runs, "_remaining_percent", lambda provider: 64.0)

    for tier in ("light", "standard", "strong"):
        assert web_runs._tier_under_quota_pressure("codex", tier) == tier


def test_a_provider_without_published_quota_is_not_presumed_tight(monkeypatch):
    """Gemini, Copilot et Cursor n'exposent aucune reserve exploitable.

    Les degrader par defaut punirait l'absence de mesure.
    """
    monkeypatch.setattr(web_runs, "_remaining_percent", lambda provider: None)

    assert web_runs._tier_under_quota_pressure("copilot", "strong") == "strong"


def test_the_threshold_reads_the_smallest_known_window(monkeypatch):
    """Une fenetre de cinq heures presque vide compte, meme si la semaine va bien."""
    monkeypatch.setattr(
        web_runs,
        "usage_status",
        lambda force=False: [
            {"provider": "codex", "windows": [
                {"name": "5 heures", "remaining_percent": 4.0},
                {"name": "7 jours", "remaining_percent": 71.0},
            ]}
        ],
    )

    assert web_runs._remaining_percent("codex") == 4.0
    assert web_runs._tier_under_quota_pressure("codex", "strong") == "standard"


def test_the_consensus_still_asks_for_the_top_tier_by_itself():
    """Le repli vient du quota, pas d'un renoncement du routeur."""
    consensus = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")
    assert web_runs._route_tier("Choisir entre deux architectures", consensus) == (
        "strong",
        "high",
    )
