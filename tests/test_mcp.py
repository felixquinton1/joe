"""Les outils MCP ne s'executent que si le projet les a autorises."""

from __future__ import annotations

from pathlib import Path

from joe import mcp
from joe.models import Intent
from joe.providers import Provider


def test_the_prompt_is_never_taken_for_a_tool_name():
    """`--allowedTools` accepte plusieurs valeurs.

    Sans separateur, la CLI avale le prompt comme un nom d'outil de plus et le
    run echoue sur « Input must be provided ». Seuls les drapeaux qui
    suivaient le masquaient.
    """
    provider = Provider("claude", "claude")
    argv = provider.command(
        "ma demande", Path("/tmp"), Intent.MODIFY, None, None, "acceptEdits"
    )

    assert argv[-2:] == ["--", "ma demande"]
    assert argv.index("--") > argv.index("--allowedTools")


def test_nothing_is_pre_authorised_without_the_project_asking(monkeypatch):
    """Un outil MCP sort du projet : base de donnees, reseau, service externe.

    « Je peux modifier ce projet » ne vaut pas « je peux agir au-dehors ».
    """
    monkeypatch.setattr(
        "joe.providers.allowed_tool_patterns", lambda *args: ["mcp__base"]
    )

    ferme = Provider("claude", "claude").command(
        "demande", Path("/tmp"), Intent.MODIFY, None, None, "acceptEdits"
    )
    assert "mcp__base" not in ferme

    ouvert = Provider("claude", "claude", (), False, True).command(
        "demande", Path("/tmp"), Intent.MODIFY, None, None, "acceptEdits"
    )
    assert "mcp__base" in ouvert
    # Bash reste autorise en ecriture : les deux cohabitent sur un seul drapeau.
    assert ouvert.count("--allowedTools") == 1
    assert "Bash" in ouvert


def test_a_read_only_run_can_also_reach_a_database(monkeypatch):
    """Mesure : un outil MCP est refuse en lecture seule aussi.

    Le but du reglage est justement de lire une base sans passer en acces
    complet, donc l'autorisation ne doit pas dependre du niveau d'ecriture.
    """
    monkeypatch.setattr(
        "joe.providers.allowed_tool_patterns", lambda *args: ["mcp__base"]
    )

    argv = Provider("claude", "claude", (), False, True).command(
        "demande", Path("/tmp"), Intent.ANALYZE, None, None, "plan"
    )

    assert "mcp__base" in argv
    assert "Bash" not in argv


def test_server_names_come_from_the_cli_itself(monkeypatch):
    """`claude mcp list` imprime « nom: commande - etat »."""
    sortie = (
        "Checking MCP server health…\n\n"
        "fausse-db: /bin/true  - ⏸ Pending approval\n"
        "autre-outil: /bin/true  - ✓ Connected\n"
    )

    class Completed:
        stdout = sortie

    monkeypatch.setattr(mcp, "_CACHE", {})
    monkeypatch.setattr(mcp.subprocess, "run", lambda *a, **k: Completed())

    assert mcp.configured_servers("claude") == ("fausse-db", "autre-outil")
    assert mcp.allowed_tool_patterns("claude") == [
        "mcp__fausse-db",
        "mcp__autre-outil",
    ]


def test_a_provider_without_an_inventory_is_left_alone(monkeypatch):
    """Seul Claude expose une liste interrogeable.

    Ailleurs, le reglage reste sans effet plutot que de deviner une syntaxe
    non verifiee.
    """
    monkeypatch.setattr(mcp, "_CACHE", {})
    assert mcp.configured_servers("codex") == ()
    assert mcp.allowed_tool_patterns("gemini") == []
