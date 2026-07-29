from joe import capabilities, provider_registry
from joe.cli import parser
from joe.provider_registry import ProviderSpec
from joe.providers import default_providers


def test_registry_extension_reaches_cli_engine_and_capabilities(monkeypatch):
    monkeypatch.setattr(
        provider_registry,
        "PROVIDERS",
        provider_registry.PROVIDERS + (ProviderSpec("fixture", "Fixture"),),
    )
    monkeypatch.setattr(capabilities, "_cache", None)

    arguments = parser().parse_args(["--agent", "fixture", "test"])
    providers = default_providers()
    discovered = capabilities.provider_capabilities(refresh=True)

    assert arguments.agent == "fixture"
    assert "fixture" in providers
    assert discovered["fixture"]["models"] == []
    assert provider_registry.get_provider_catalog()[-1] == {
        "id": "fixture",
        "label": "Fixture",
    }
