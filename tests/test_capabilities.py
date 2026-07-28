from joe.capabilities import provider_capabilities, provider_defaults


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
