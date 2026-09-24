import joe.capabilities as capabilities_module
from joe.capabilities import (
    provider_capabilities,
    provider_defaults,
    select_model,
    select_model_tier,
)


def test_capabilities_have_safe_defaults_and_provider_specific_controls():
    capabilities = provider_capabilities(refresh=True)
    # La liste suit le registre : un fournisseur ajouté là-bas doit apparaître
    # ici sans qu'on le recopie, sinon les deux divergent en silence.
    from joe.provider_registry import get_provider_names

    assert set(capabilities) == set(get_provider_names())
    assert "antigravity" in capabilities
    assert any(
        mode["id"] == "read-only"
        for mode in capabilities["codex"]["execution_modes"]
    )
    assert "xhigh" in capabilities["claude"]["efforts"]
    assert "gemini" not in capabilities

    # Le catalogue d'Antigravity est lu sur la CLI `agy`. L'exiger non vide
    # faisait dependre la suite de ce qui est installe sur la machine : vert
    # ici, rouge sur un runner nu, sans qu'une ligne de code change. On verifie
    # la forme, et le contenu seulement quand la CLI a repondu.
    antigravity = capabilities["antigravity"]
    assert isinstance(antigravity["models"], list)
    if antigravity["available"]:
        assert antigravity["models"]


def test_codex_catalog_efforts_follow_selected_model_when_available():
    capabilities = provider_capabilities()
    models = capabilities["codex"]["models"]
    if models:
        assert all("efforts" in model for model in models)


def test_provider_defaults_resolve_a_concrete_catalog_model():
    capabilities = provider_capabilities()

    model, effort = provider_defaults("codex")

    if capabilities["codex"]["models"]:
        assert model == capabilities["codex"]["models"][0]["id"]
        assert effort == capabilities["codex"]["models"][0].get(
            "default_effort"
        )


def test_provider_defaults_load_capabilities_when_cache_is_empty(monkeypatch):
    monkeypatch.setattr(capabilities_module, "_cache", None)
    monkeypatch.setattr(
        capabilities_module,
        "provider_capabilities",
        lambda: {
            "codex": {
                "models": [
                    {
                        "id": "gpt-5.6-sol",
                        "default_effort": "low",
                    }
                ]
            }
        },
    )

    assert provider_defaults("codex") == ("gpt-5.6-sol", "low")


def test_simple_selection_prefers_declared_fast_lower_cost_model(monkeypatch):
    monkeypatch.setattr(
        capabilities_module,
        "_cache",
        (0, {"codex": {"models": [
            {"id": "frontier", "priority": 1},
            {"id": "terra", "priority": 2, "speed_tiers": ["fast"], "cost_tier": 1},
        ]}}),
    )

    assert select_model("codex", complex_request=False) == "terra"
    assert select_model("codex", complex_request=True) == "frontier"
    assert select_model_tier("codex", "light") == "terra"
    # `frontier` ne dit rien de lui-même : ni description, ni coût. Il n'est
    # donc pas promu « standard » alors qu'un modèle classé plus léger existe.
    # Servir le modèle de tête faute de classement est précisément ce qui
    # envoyait le plus gros modèle sur des tâches simples.
    assert select_model_tier("codex", "standard") == "terra"
    # En haut de gamme au contraire, rien de classé signifie qu'on garde le
    # modèle de tête : servir un modèle léger à une demande lourde serait pire
    # que le défaut qu'on corrige.
    assert select_model_tier("codex", "strong") == "frontier"


def test_claude_uses_sonnet_for_simple_and_opus_for_complex_requests(monkeypatch):
    monkeypatch.setattr(capabilities_module, "_cache", None)
    capabilities = provider_capabilities(refresh=True)

    assert select_model("claude", complex_request=False) == "claude-sonnet-5"
    assert select_model("claude", complex_request=True) == "claude-opus-5-5"

    # Des identifiants complets, jamais des alias : « opus » suit le défaut du
    # CLI et peut donc changer sans que Joe ni l'utilisateur l'aient choisi.
    identifiers = [model["id"] for model in capabilities["claude"]["models"]]
    assert identifiers[0] == "claude-opus-5-5"
    assert all(identifier.startswith("claude-") for identifier in identifiers)
