"""Aucun encodage implicite dans le produit.

Sans encodage explicite, Python lit et écrit selon la locale : UTF-8 sous
Linux et macOS, cp1252 sous Windows. Un skill en français y devenait
illisible pour les modèles, et un diff accentué pouvait faire échouer la
revue. Les tests ne l'attrapent pas, puisqu'ils tournent dans la locale de
la machine : ce contrôle lit le code lui-même.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "joe"


def _implicit_encoding_sites() -> list[str]:
    sites = []
    for path in sorted(SOURCE_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", "")
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            if "encoding" in keywords:
                continue
            if name in {"read_text", "write_text"}:
                sites.append(f"{path.name}:{node.lineno} {name}")
            elif name in {"run", "Popen", "check_output"} and any(
                isinstance(keywords[flag], ast.Constant)
                and keywords[flag].value is True
                for flag in ("text", "universal_newlines")
                if flag in keywords
            ):
                sites.append(f"{path.name}:{node.lineno} subprocess en mode texte")
    return sites


def test_no_source_file_relies_on_the_locale_encoding():
    sites = _implicit_encoding_sites()
    assert not sites, (
        "Encodage implicite, donc dépendant de la plateforme :\n  "
        + "\n  ".join(sites)
        + "\nPasser encoding=\"utf-8\", ou read_user_text pour un fichier "
        "que l'utilisateur a pu écrire dans un autre encodage."
    )
