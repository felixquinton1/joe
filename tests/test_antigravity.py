"""Antigravity CLI : le successeur de Gemini CLI, pilote par `agy`.

Les evenements de ce fichier sont copies d'une sortie reelle de `agy 1.2.9`,
pas inventes : l'enveloppe porte la cle `event` et non `type`, et chaque etape
d'outil arrive deux fois.
"""

from __future__ import annotations

from pathlib import Path

from joe.models import Intent
from joe.provider_registry import get_provider_spec, install_hint
from joe.providers import (
    Provider,
    _activity_antigravity,
    _final_antigravity,
)


def argv(mode: str, model: str | None = None, effort: str | None = None) -> list[str]:
    """`command` recoit un mode d'execution, que Joe traduit en niveau d'acces."""
    return Provider("antigravity", "agy").command(
        "MA DEMANDE", Path("/tmp"), Intent.MODIFY, model, effort, mode
    )


def test_the_prompt_is_attached_to_the_flag_it_belongs_to():
    """`--print` accepte une valeur facultative.

    Laisse seul, il avale l'argument suivant comme prompt : la CLI le dit
    elle-meme, « --print took --output-format as its prompt ». Le meme piege
    que `--allowedTools` chez Claude, sous un autre nom.
    """
    command = argv("plan")

    assert command[-1] == "--print=MA DEMANDE"
    assert "--print" not in command[:-1]


def test_each_access_level_maps_to_a_real_mode():
    """La CLI n'expose pas d'autorisation fine, seulement des paliers.

    Les mapper de travers donnerait soit une lecture seule qui ecrit, soit une
    ecriture qui ne peut rien faire.
    """
    assert "--mode" in argv("plan") and "plan" in argv("plan")
    assert "accept-edits" in argv("acceptEdits")
    assert "--dangerously-skip-permissions" in argv("danger-full-access")
    assert "--sandbox" in argv("dontAsk")

    # Le palier le plus large n'est jamais accorde pour une simple ecriture :
    # il autoriserait aussi ce qui sort du projet.
    assert "--dangerously-skip-permissions" not in argv("acceptEdits")
    assert "--dangerously-skip-permissions" not in argv("plan")
    # Et le bac a sable reste en lecture, il ne se contente pas de restreindre
    # le terminal.
    assert "plan" in argv("dontAsk")


def test_the_model_and_effort_reach_the_command():
    command = argv("plan", "gemini-3.8-flash-high", "high")

    assert command[command.index("--model") + 1] == "gemini-3.8-flash-high"
    assert command[command.index("--effort") + 1] == "high"
    # Le flux structure est indispensable : sans lui, aucune activite affichee.
    assert command[command.index("--output-format") + 1] == "stream-json"


def test_a_tool_step_is_announced_once_not_twice():
    """Chaque etape arrive en ACTIVE puis en DONE.

    Les annoncer toutes les deux ferait apparaitre chaque outil en double dans
    le panneau d'activite.
    """
    actif = {
        "event": "step_update",
        "step_update": {
            "step_index": 2,
            "state": "ACTIVE",
            "step_type": "tool",
            "tool_name": "run_command",
            "tool_info": {"parameters": {"CommandLine": "ls -la"}},
        },
    }
    termine = {**actif, "step_update": {**actif["step_update"], "state": "DONE"}}

    activite = _activity_antigravity(actif)
    assert activite is not None
    assert activite["kind"] == "command_execution"
    assert activite["label"] == "run_command"
    assert "ls -la" in activite["detail"]
    assert _activity_antigravity(termine) is None


def test_an_mcp_tool_is_recognised_as_such():
    event = {
        "event": "step_update",
        "step_update": {
            "state": "ACTIVE",
            "step_type": "tool",
            "tool_name": "mcp__base__db_query",
            "tool_info": {"parameters": {"sql": "SELECT 1"}},
        },
    }

    assert _activity_antigravity(event)["kind"] == "mcp_tool_call"


def test_the_answer_comes_from_the_result_event():
    event = {"event": "result", "result": {"status": "SUCCESS", "response": "OK"}}

    assert _final_antigravity(event, []) == "OK"
    # Une etape intermediaire ne conclut rien.
    assert _final_antigravity({"event": "step_update", "step_update": {}}, []) == ""


def test_a_refused_permission_is_named_instead_of_returning_nothing():
    """La CLI « refuse en douceur » : succes, reponse vide, refus listes.

    Rendre ce vide tel quel donnerait une bulle muette, exactement le symptome
    que Joe cherche a ne plus produire.
    """
    event = {
        "event": "result",
        "result": {
            "status": "SUCCESS",
            "response": "",
            "denied_actions": [{"action": "command", "display_name": "RunCommand"}],
        },
    }

    reponse = _final_antigravity(event, [])

    assert "RunCommand" in reponse
    assert "accès complet" in reponse


def test_the_registry_knows_how_to_install_and_reach_it():
    spec = get_provider_spec("antigravity")

    # Le nom du fournisseur est parlant, celui du binaire ne l'est pas.
    assert spec.executables == ("agy",)
    assert spec.streams_json is True

    posix = install_hint("antigravity")
    windows = install_hint("antigravity", windows=True)
    assert posix["command"].startswith("curl -fsSL https://antigravity.google")
    # Une commande Windows inconnue ne doit pas renvoyer celle de POSIX : un
    # `curl … | bash` colle dans PowerShell echoue sans dire pourquoi.
    assert windows["command"].startswith("irm https://antigravity.google")
    assert posix["homepage"].startswith("https://antigravity.google")


def test_a_provider_without_a_windows_command_offers_none():
    """Mieux vaut pas de commande qu'une commande qui echouera."""
    spec = get_provider_spec("cursor-agent")
    assert spec.install_windows  # celui-ci en declare une

    class Muet:
        install_posix = "curl x | bash"
        install_windows = ""
        homepage = "https://exemple"
        sign_in = ""
        requires_node = ""

    from joe import provider_registry

    original = provider_registry.get_provider_spec
    provider_registry.get_provider_spec = lambda name: Muet()
    try:
        assert provider_registry.install_hint("muet", windows=True)["command"] == ""
    finally:
        provider_registry.get_provider_spec = original
