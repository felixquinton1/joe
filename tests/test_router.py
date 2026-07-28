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


def test_french_merge_then_implementation_is_writable():
    route = Router().route("Merge origin/dev puis fais l'implémentation")

    assert route.intent is Intent.MODIFY
    assert route.primary == "codex"


def test_git_actions_are_modifications():
    for request in (
        "pull la branche dev",
        "rebase sur origin/main",
        "commit et push les changements",
        "fusionne cette branche",
    ):
        assert Router().route(request).intent is Intent.MODIFY


def test_large_implementation_automatically_gets_review():
    route = Router().route(
        "Fais une implémentation complète de la gestion des permissions."
    )

    assert route.intent is Intent.MODIFY
    assert route.mode is Mode.REVIEW
    assert route.reviewer == "claude"


def test_operational_audit_uses_review_instead_of_consensus():
    route = Router().route("Fais un audit global de Joe et exécute les tests")

    assert route.intent is Intent.ANALYZE
    assert route.mode is Mode.REVIEW
    assert route.primary == "codex"
    assert route.reviewer == "claude"


def test_long_capability_question_stays_fast_and_read_only():
    route = Router().route(
        "Est-ce que tu peux modifier le code de Joe depuis ici ou dois-je "
        "passer par la CLI ? Est-ce que cela ferait planter le serveur si "
        "je ferme puis relance l’application après avoir modifié le code ?"
    )

    assert route.intent is Intent.ANSWER
    assert route.mode is Mode.FAST
    assert route.reviewer is None


def test_explicit_provider_name_routes_directly_to_that_provider():
    route = Router().route("Fais-moi un petit test de Gemini")

    assert route.mode is Mode.FAST
    assert route.primary == "gemini"
    assert "explicit-provider" in route.reason
    assert "health-check" in route.reason


def test_explicit_file_change_negations_stay_read_only():
    for request in (
        "Analyse ce module mais ne modifie rien",
        "Ne change aucun fichier, donne-moi seulement un avis",
        "Explique le correctif sans changer les fichiers",
        "Do not modify files; review this implementation",
    ):
        route = Router().route(request)
        assert route.intent is not Intent.MODIFY
        assert "explicit-read-only" in route.reason


def test_negated_execution_clause_does_not_trigger_git_modification():
    route = Router().route(
        "N’exécute pas git push ; explique seulement ce qui se passerait"
    )

    assert route.intent is not Intent.MODIFY


def test_scoped_git_negation_does_not_hide_a_real_code_change():
    route = Router().route("Corrige le bug mais ne commit pas le changement")

    assert route.intent is Intent.MODIFY


def test_scoped_read_only_clause_does_not_hide_an_explicit_change():
    route = Router().route("Corrige le bug sans changer son API publique")

    assert route.intent is Intent.MODIFY


def test_fais_cela_is_an_explicit_follow_up_change():
    route = Router().route("Fais cela et ajoute les tests ciblés")

    assert route.intent is Intent.MODIFY


def test_feature_request_phrased_as_possibility_is_writable():
    route = Router().route(
        "C'est possible de préciser le modèle utilisé dans tous les modes ?"
    )

    assert route.intent is Intent.MODIFY


def test_capability_question_about_access_stays_read_only():
    route = Router().route(
        "Est-ce que tu peux modifier le code de Joe depuis cette session ?"
    )

    assert route.intent is Intent.ANSWER
