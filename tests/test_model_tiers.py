"""Le niveau demandé doit décider du modèle, pas sa place dans la liste."""

from __future__ import annotations

from joe.model_tiers import choose, model_tier


CLAUDE = [
    {"id": "claude-opus-5", "cost_tier": 4,
     "description": "Le meilleur rapport qualité-prix pour le travail complexe."},
    {"id": "claude-sonnet-5", "cost_tier": 2,
     "description": "Rapide et économique, pour le volume."},
    {"id": "claude-haiku-4-5", "cost_tier": 1,
     "description": "Le plus léger, pour les tâches simples."},
    {"id": "claude-fable-5-1", "cost_tier": 5,
     "description": "Le plus capable, pour le raisonnement long. Coût élevé."},
    {"id": "claude-opus-4-8", "cost_tier": 4,
     "description": "Génération précédente d'Opus."},
    {"id": "claude-sonnet-4-6", "cost_tier": 3,
     "description": "Génération précédente de Sonnet."},
]

CODEX = [
    {"id": "gpt-6-astra", "priority": 1,
     "description": "Our most capable model for complex, demanding work."},
    {"id": "gpt-5.6-sol", "priority": 4,
     "description": "Reliable agentic workhorse for everyday tasks."},
    {"id": "gpt-5.6-terra", "priority": 7,
     "description": "Balanced agentic coding model for everyday work."},
    {"id": "gpt-5.6-luna", "priority": 8,
     "description": "Fast and affordable agentic coding model."},
    {"id": "gpt-5.5", "priority": 12,
     "description": "Proven previous-generation model for coding and general work."},
]


def test_a_standard_task_no_longer_gets_the_most_expensive_model():
    """« standard » rendait `models[len//2]`, soit le milieu du tableau.

    Sur ce catalogue, ce milieu tombe sur le modèle le plus cher des six —
    plus cher que celui rendu pour « strong ».
    """
    assert CLAUDE[len(CLAUDE) // 2]["id"] == "claude-fable-5-1"
    assert choose(CLAUDE, "standard") != "claude-fable-5-1"
    assert choose(CLAUDE, "standard") == "claude-sonnet-5"


def test_a_provider_that_describes_its_models_classes_itself():
    """Codex publie une description par modèle : personne ne la lisait.

    Une nouvelle sortie décrite comme « fast and affordable » est classée le
    jour de sa publication, sans qu'une table soit mise à jour.
    """
    assert model_tier(CODEX[0]) == "strong"
    assert model_tier(CODEX[1]) == "standard"
    assert model_tier(CODEX[3]) == "light"
    assert choose(CODEX, "light") == "gpt-5.6-luna"
    assert choose(CODEX, "standard") == "gpt-5.6-sol"
    assert choose(CODEX, "strong") == "gpt-6-astra"


def test_a_previous_generation_model_is_never_routed_to():
    """Il reste sélectionnable à la main, jamais choisi automatiquement."""
    assert model_tier(CLAUDE[4]) == "legacy"
    assert model_tier(CODEX[4]) == "legacy"
    chosen = {choose(CLAUDE, tier) for tier in ("light", "standard", "strong")}
    assert "claude-opus-4-8" not in chosen
    assert "gpt-5.5" not in {
        choose(CODEX, tier) for tier in ("light", "standard", "strong")
    }


def test_an_unknown_catalog_hands_the_decision_back():
    """Une gamme entièrement nouvelle doit router comme avant, pas au hasard."""
    unknown = [{"id": "aurora-1"}, {"id": "aurora-2"}]

    assert model_tier(unknown[0]) == ""
    assert choose(unknown, "standard") is None
    assert choose(unknown, "strong") is None


def test_a_heavy_request_never_falls_back_to_a_light_model():
    """Servir un modèle léger à une demande lourde serait pire que le défaut."""
    only_light = [{"id": "mini", "cost_tier": 1,
                   "description": "Fast and affordable model."}]

    assert choose(only_light, "light") == "mini"
    assert choose(only_light, "strong") is None
    assert choose(only_light, "long-context") is None


def test_the_declared_catalog_settles_what_a_description_cannot():
    """Fable se décrit comme le plus capable, et coûte le plus cher.

    Laissé au seul texte, il disputerait la tête à Opus. La déclaration en
    fait un dernier recours, jamais un défaut.
    """
    assert model_tier(CLAUDE[3]) == "strong"
    assert choose(CLAUDE, "strong") == "claude-opus-5"
