"""Joe ne doit router que vers les CLI qu'il peut réellement lancer."""

from __future__ import annotations

import json

import pytest

from joe import provider_choice
from joe.provider_registry import get_provider_names, install_hint, provider_executables
from joe.providers import active_providers, default_providers, resolve_executable


@pytest.fixture
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "joe" / "providers.json"


def test_routing_only_receives_cli_that_are_installed(monkeypatch):
    """Le routage recevait les cinq fournisseurs quoi qu'il arrive.

    Sur une machine où une seule CLI est installée, la première demande partait
    donc vers une CLI absente, échouait, puis se rabattait : l'utilisateur
    voyait une erreur pour une situation parfaitement normale.
    """
    monkeypatch.setattr(
        "joe.providers.resolve_executable",
        lambda name, **kwargs: "/usr/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr("joe.provider_choice.disabled_providers", set)

    assert sorted(active_providers()) == ["claude"]
    assert len(default_providers()) == len(get_provider_names())


def test_nothing_installed_keeps_every_provider(monkeypatch):
    """Une liste vide priverait le routage de destinataire.

    L'erreur au run doit rester « exécutable introuvable », qui dit la vérité,
    au lieu d'un plantage sans rapport avec la cause.
    """
    monkeypatch.setattr("joe.providers.resolve_executable", lambda name, **kwargs: None)
    monkeypatch.setattr("joe.provider_choice.disabled_providers", set)

    assert sorted(active_providers()) == sorted(get_provider_names())


def test_a_refused_provider_leaves_the_routing(monkeypatch, isolated_preferences):
    """Détecter n'est pas vouloir : on peut avoir une CLI sans l'autoriser."""
    monkeypatch.setattr(
        "joe.providers.resolve_executable", lambda name, **kwargs: f"/usr/bin/{name}"
    )
    provider_choice.set_disabled(["copilot"])

    assert "copilot" not in active_providers()
    assert "codex" in active_providers()


def test_a_refusal_survives_a_restart(isolated_preferences):
    provider_choice.set_disabled(["gemini", "copilot"])

    assert isolated_preferences.is_file()
    assert provider_choice.disabled_providers() == {"gemini", "copilot"}
    assert not provider_choice.is_enabled("gemini")


def test_an_unknown_name_is_never_written(isolated_preferences):
    """Le fichier est relu à chaque démarrage : il ne doit pas accumuler."""
    provider_choice.set_disabled(["codex", "chatgpt", ""])

    payload = json.loads(isolated_preferences.read_text(encoding="utf-8"))
    assert payload["disabled"] == ["codex"]


def test_a_corrupt_preferences_file_does_not_block_joe(isolated_preferences):
    isolated_preferences.parent.mkdir(parents=True)
    isolated_preferences.write_text("{ pas du json", encoding="utf-8")

    assert provider_choice.disabled_providers() == set()


def test_every_provider_says_how_to_install_itself():
    """Constater qu'une CLI manque sans dire comment l'obtenir ne sert à rien."""
    for name in get_provider_names():
        hint = install_hint(name)
        assert hint["command"], name
        assert hint["homepage"].startswith("https://"), name
        assert hint["sign_in"], name
        assert install_hint(name, windows=True)["command"], name


def test_a_renamed_cli_is_still_found(monkeypatch):
    """Cursor a renommé `cursor-agent` en `agent`.

    Chercher le seul nom du fournisseur la déclarait absente sur toute
    installation à jour, sans que rien ne l'explique.
    """
    assert provider_executables("cursor-agent") == ("cursor-agent", "agent")
    assert provider_executables("codex") == ("codex",)

    monkeypatch.setattr(
        "joe.providers.windows_aware_executable",
        lambda name, **kwargs: "/usr/bin/agent" if name == "agent" else None,
    )
    monkeypatch.setattr("joe.providers._proves_identity", lambda path, marker: True)

    assert resolve_executable("cursor-agent") == "/usr/bin/agent"


def test_a_generic_name_must_prove_it_is_the_right_cli(monkeypatch):
    """`agent` n'appartient à personne : un autre outil peut l'occuper.

    Sans preuve d'identité, Joe adopterait ce binaire et enverrait ses runs à
    un inconnu — ce qui arrive réellement sur une machine où un paquet Python
    installe son propre `agent`.
    """
    monkeypatch.setattr(
        "joe.providers.windows_aware_executable",
        lambda name, **kwargs: "/venv/bin/agent" if name == "agent" else None,
    )
    monkeypatch.setattr("joe.providers._proves_identity", lambda path, marker: False)

    assert resolve_executable("cursor-agent") is None
    # Le diagnostic doit pouvoir le nommer plutôt que de dire « absent ».
    assert resolve_executable("cursor-agent", unconfirmed=True) == "/venv/bin/agent"
