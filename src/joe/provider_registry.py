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


def get_provider_names() -> tuple[str, ...]:
    return tuple(p.name for p in PROVIDERS)
