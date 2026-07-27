from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

KNOWN_VERSIONS = {
    "codex": "0.145.0",
    "claude": "2.1.197",
    "gemini": "0.52.0",
    "copilot": "1.0.75",
}


def provider_audit() -> list[dict[str, Any]]:
    results = []
    for provider, known_version in KNOWN_VERSIONS.items():
        current = _version(provider)
        results.append(
            {
                "provider": provider,
                "available": current is not None,
                "known_version": known_version,
                "current_version": current,
                "changed": current is not None and current != known_version,
            }
        )
    return results


def format_audit(results: list[dict[str, Any]]) -> str:
    lines = ["Joe provider sync"]
    for item in results:
        if not item["available"]:
            state = "indisponible"
        elif item["changed"]:
            state = f"nouvelle version (référence {item['known_version']})"
        else:
            state = "à jour"
        lines.append(f"- {item['provider']}: {item['current_version'] or '—'} · {state}")
    return "\n".join(lines)


def joe_project() -> Path:
    return Path(__file__).resolve().parents[2]


def _version(provider: str) -> str | None:
    if not shutil.which(provider):
        return None
    try:
        result = subprocess.run(
            [provider, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    for token in output.replace(",", " ").split():
        if token[:1].isdigit() and "." in token:
            return token.rstrip(".")
    return output.splitlines()[0][:80] if output else None


def update_request(results: list[dict[str, Any]], force: bool) -> str:
    changed = [item for item in results if item["changed"]]
    scope = changed if changed else results
    versions = ", ".join(
        f"{item['provider']} {item['current_version']}" for item in scope
    )
    reason = "audit forcé" if force and not changed else "nouvelles versions détectées"
    return f"""Maintenance de Joe ({reason}) : {versions}.

Inspecte les --help locaux des fournisseurs concernés et compare-les aux wrappers,
capabilities, modèles, efforts, permissions, streaming, historique et quotas de Joe.
Implémente uniquement les nouveautés réellement utiles à l'orchestrateur, sans
ajouter de framework ni dépendance inutile. Mets à jour KNOWN_VERSIONS dans
src/joe/maintenance.py, la documentation et les tests. Exécute la suite de tests.
Ne committe et ne pousse rien : l'utilisateur doit pouvoir examiner le résultat."""
