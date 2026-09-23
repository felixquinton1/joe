"""Le niveau demandé doit décider du modèle, pas sa place dans la liste."""

from __future__ import annotations

from joe.model_tiers import choose, is_metered, model_tier


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
    assert choose(CLAUDE, "standard", "claude") != "claude-fable-5-1"
    assert choose(CLAUDE, "standard", "claude") == "claude-sonnet-5"


def test_a_provider_that_describes_its_models_classes_itself():
    """Codex publie une description par modèle : personne ne la lisait.

    Une nouvelle sortie décrite comme « fast and affordable » est classée le
    jour de sa publication, sans qu'une table soit mise à jour.
    """
    assert model_tier(CODEX[0], "codex") == "strong"
    assert model_tier(CODEX[1], "codex") == "standard"
    assert model_tier(CODEX[3], "codex") == "light"
    assert choose(CODEX, "light", "codex") == "gpt-5.6-luna"
    assert choose(CODEX, "standard", "codex") == "gpt-5.6-sol"
    assert choose(CODEX, "strong", "codex") == "gpt-6-astra"


def test_a_previous_generation_model_is_never_routed_to():
    """Il reste sélectionnable à la main, jamais choisi automatiquement."""
    assert model_tier(CLAUDE[4], "claude") == "legacy"
    assert model_tier(CODEX[4], "codex") == "legacy"
    chosen = {choose(CLAUDE, tier, "claude") for tier in ("light", "standard", "strong")}
    assert "claude-opus-4-8" not in chosen
    assert "gpt-5.5" not in {
        choose(CODEX, tier, "codex") for tier in ("light", "standard", "strong")
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

    Laissé au seul texte, il disputerait la tête à Opus.
    """
    assert model_tier(CLAUDE[3], "claude") == "strong"
    assert choose(CLAUDE, "strong", "claude") == "claude-opus-5"


def test_a_metered_model_is_never_chosen_automatically():
    """Facturé au jeton, hors abonnement.

    Engager une dépense doit venir d'une décision, pas du mode retenu pour une
    demande. Le modèle reste proposé au choix manuel.
    """
    assert is_metered(CLAUDE[3], "claude") is True
    assert all(not is_metered(model, "codex") for model in CODEX)

    chosen = {choose(CLAUDE, tier, "claude") for tier in ("light", "standard", "strong")}
    assert "claude-fable-5-1" not in chosen
    # Même en haut de gamme et seul de son niveau, il n'est pas retenu.
    assert choose([CLAUDE[3]], "strong", "claude") is None


def test_everything_metered_still_answers_rather_than_refusing():
    """Une gamme entièrement au jeton ne doit pas priver Joe de modèle."""
    from joe.capabilities import _auto_selectable

    metered = [{"id": "paid-1", "metered": True}, {"id": "paid-2", "metered": True}]
    assert len(_auto_selectable(metered)) == 2


# Le catalogue reel d'`agy 1.2.9` : « slug<TAB>libelle », rien d'autre. Ni
# description ni cout, donc aucun des signaux que le module sait lire.
ANTIGRAVITY = [
    {"id": "gemini-3.8-flash-high", "label": "Gemini 3.8 Flash (High)"},
    {"id": "gemini-3.8-flash-medium", "label": "Gemini 3.8 Flash (Medium)"},
    {"id": "gemini-3.8-flash-low", "label": "Gemini 3.8 Flash (Low)"},
    {"id": "gemini-3.6-flash-medium", "label": "Gemini 3.6 Flash (Medium)"},
    {"id": "gemini-3.1-pro-high", "label": "Gemini 3.1 Pro (High)"},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 (Thinking)"},
    {"id": "claude-opus-4-6-thinking", "label": "Claude Opus 4.6 (Thinking)"},
]


def test_a_catalog_without_metadata_is_still_classed():
    """Sans table, rien n'etait classe et le niveau retombait sur un index.

    « standard » prenait le milieu du tableau, soit une generation 3.6, quand
    « strong » prenait la tete, une 3.8 : le niveau demande ne designait rien.
    """
    assert choose(ANTIGRAVITY, "light", "antigravity") == "gemini-3.8-flash-low"
    assert choose(ANTIGRAVITY, "standard", "antigravity") == "gemini-3.8-flash-medium"
    assert choose(ANTIGRAVITY, "strong", "antigravity") == "gemini-3.1-pro-high"

    # Une generation depassee n'est jamais servie automatiquement.
    assert "gemini-3.6-flash-medium" not in {
        choose(ANTIGRAVITY, tier, "antigravity")
        for tier in ("light", "standard", "strong")
    }


def test_the_same_identifier_can_mean_two_things_at_two_providers():
    """`claude-sonnet-4-6` existe des deux cotes, et ne vaut pas pareil.

    Chez Claude Code c'est une generation precedente, ecartee du routage. Chez
    Antigravity c'est le Sonnet le plus recent publie : l'ecarter le retirerait
    sans rien mettre a la place. Une table commune donnait a l'un le classement
    de l'autre.
    """
    sonnet = {"id": "claude-sonnet-4-6"}

    assert model_tier(sonnet, "claude") == "legacy"
    assert model_tier(sonnet, "antigravity") == "standard"
    # Sans fournisseur nomme, aucune table ne s'applique : c'est la seule
    # reponse qui ne choisit pas arbitrairement entre les deux.
    assert model_tier(sonnet) == ""
