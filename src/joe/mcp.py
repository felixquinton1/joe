"""Les serveurs MCP que chaque CLI a déjà configurés.

Un outil MCP demande toujours une autorisation explicite, même pour lire. Joe
lance les CLI en un appel non interactif, où cette autorisation ne peut pas
être donnée : mesuré, un outil MCP est refusé en lecture seule comme en
écriture, et ne s'exécute qu'en accès complet.

Pré-autoriser lève le refus, mais l'autorisation se donne par serveur — le
motif générique s'est révélé peu fiable. Il faut donc connaître leurs noms, et
c'est la CLI elle-même qui les liste.

Rien n'est pré-autorisé sans que le projet l'ait demandé : un outil MCP sort du
projet par nature, et « je peux modifier ce projet » ne vaut pas « je peux agir
au-dehors ».
"""

from __future__ import annotations

import re
import subprocess
import time

# `claude mcp list` imprime une ligne par serveur : « nom: commande - état ».
_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:")
_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}
_TTL_SECONDS = 300.0

# Seul Claude Code expose un inventaire interrogeable. Ailleurs, le réglage
# reste sans effet plutôt que de deviner une syntaxe non vérifiée.
_LIST_COMMAND = {"claude": ("mcp", "list")}


def configured_servers(provider: str, executable: str | None = None) -> tuple[str, ...]:
    """Noms des serveurs MCP déclarés pour ce fournisseur, cache de 5 minutes."""
    arguments = _LIST_COMMAND.get(provider)
    if not arguments:
        return ()
    cached = _CACHE.get(provider)
    if cached and time.monotonic() - cached[0] < _TTL_SECONDS:
        return cached[1]
    names: tuple[str, ...] = ()
    try:
        completed = subprocess.run(
            [executable or provider, *arguments],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    else:
        found = []
        for line in (completed.stdout or "").splitlines():
            match = _LINE.match(line.strip())
            if match:
                found.append(match.group(1))
        names = tuple(dict.fromkeys(found))
    _CACHE[provider] = (time.monotonic(), names)
    return names


def allowed_tool_patterns(provider: str, executable: str | None = None) -> list[str]:
    """Ce qu'il faut passer à la CLI pour qu'un outil MCP puisse s'exécuter."""
    return [f"mcp__{name}" for name in configured_servers(provider, executable)]
