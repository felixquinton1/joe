"""Quelles CLI Joe a le droit d'utiliser sur cette machine.

Joe détecte ce qui est installé, mais détecter n'est pas vouloir : on peut
avoir Copilot sur sa machine sans souhaiter qu'un run y consomme un quota. Ce
choix appartient à la machine, pas au projet — sinon chaque nouveau dossier
reposerait la question.

Le fichier n'enregistre que les refus. Un fournisseur installé plus tard est
donc utilisable sans que personne ait à revenir ici, ce qui est exactement ce
qu'on attend en ajoutant une CLI après coup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from .provider_registry import get_provider_names
from .text_encoding import read_user_text

FILE_VERSION = 1


def preferences_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "joe" / "providers.json"


def disabled_providers() -> set[str]:
    """Fournisseurs que l'utilisateur a explicitement écartés."""
    path = preferences_path()
    try:
        payload = json.loads(read_user_text(path))
    except (OSError, ValueError):
        return set()
    if not isinstance(payload, dict):
        return set()
    declared = set(get_provider_names())
    return {
        str(name)
        for name in payload.get("disabled", [])
        if str(name) in declared
    }


def set_disabled(names: Iterable[str]) -> set[str]:
    """Écrire la liste des refus, en ignorant tout nom inconnu."""
    declared = set(get_provider_names())
    disabled = sorted({str(name) for name in names} & declared)
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"version": FILE_VERSION, "disabled": disabled}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return set(disabled)


def is_enabled(name: str) -> bool:
    return name not in disabled_providers()
