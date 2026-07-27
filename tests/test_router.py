from joe.models import Intent, Mode
from joe.router import Router


def test_simple_python_change_routes_once_to_codex():
    route = Router().route("Ajoute une option Python pour désactiver la loss Gamma")
    assert route.intent is Intent.MODIFY
    assert route.mode is Mode.FAST
    assert route.primary == "codex"


def test_architecture_choice_uses_consensus():
    route = Router().route("Propose quelle approche d'architecture choisir")
    assert route.mode is Mode.CONSENSUS
    assert route.primary == "claude"


def test_large_context_routes_to_gemini():
    route = Router().route("Explore tout ce gros dépôt et résume les conventions")
    assert route.primary == "gemini"
    assert route.mode is Mode.FAST


def test_other_opinion_switches_provider_with_memory():
    route = Router().route("Qu'en pense l'autre ?", previous_provider="codex")
    assert route.primary == "claude"
    assert route.mode is Mode.FAST


def test_force_options_win():
    route = Router().route(
        "fix bug", forced_agent="gemini", forced_mode=Mode.REVIEW
    )
    assert route.primary == "gemini"
    assert route.mode is Mode.REVIEW
    assert route.reviewer == "codex"
