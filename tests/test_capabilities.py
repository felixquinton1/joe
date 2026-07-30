import joe.capabilities as capabilities_module
from joe.capabilities import provider_capabilities, provider_defaults, select_model


def test_capabilities_have_safe_defaults_and_provider_specific_controls():
    capabilities = provider_capabilities(refresh=True)
    assert set(capabilities) == {"codex", "claude", "gemini", "copilot"}
    assert any(
        mode["id"] == "read-only"
        for mode in capabilities["codex"]["execution_modes"]
    )
    assert "xhigh" in capabilities["claude"]["efforts"]
    assert capabilities["gemini"]["models"][0]["id"] == "auto"


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


def test_claude_uses_sonnet_for_simple_and_opus_for_complex_requests(monkeypatch):
    monkeypatch.setattr(capabilities_module, "_cache", None)
    capabilities = provider_capabilities(refresh=True)

    assert select_model("claude", complex_request=False) == "sonnet"
    assert select_model("claude", complex_request=True) == "opus"
