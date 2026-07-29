from __future__ import annotations

from typing import NamedTuple


class ProviderSpec(NamedTuple):
    name: str
    label: str


PROVIDERS = (
    ProviderSpec("codex", "Codex"),
    ProviderSpec("claude", "Claude"),
    ProviderSpec("gemini", "Gemini"),
    ProviderSpec("copilot", "Copilot"),
)


def get_provider_specs() -> tuple[ProviderSpec, ...]:
    return PROVIDERS


def get_provider_names() -> tuple[str, ...]:
    return tuple(provider.name for provider in get_provider_specs())


def get_provider_catalog() -> tuple[dict[str, str], ...]:
    return tuple(
        {"id": provider.name, "label": provider.label}
        for provider in get_provider_specs()
    )
