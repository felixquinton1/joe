from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .maintenance import provider_audit
from .models import Intent
from .provider_registry import get_provider_spec
from .provider_choice import disabled_providers
from .provider_health import recent_failure
from .provider_registry import install_hint
from .providers import (
    Provider,
    default_providers,
    provider_runtime_issue,
    resolve_executable,
)
from .usage import usage_status


def node_runtime() -> dict[str, Any]:
    """Version de Node presente, pour juger une commande `npm install -g`.

    Sans cela, `npm` echoue sur un message qui ne nomme jamais la cause : un
    nouvel arrivant colle la commande, voit une erreur obscure, et rien ne lui
    dit que son Node est trop ancien. L'exigence porte sur l'installation et non
    sur l'execution — Codex tourne sous Node 20 alors que son paquet en demande
    22.
    """
    executable = resolve_executable("node") or shutil.which("node")
    if not executable:
        return {"present": False, "version": None, "major": None}
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"present": True, "version": None, "major": None}
    version = (completed.stdout or "").strip().lstrip("v")
    head = version.split(".")[0]
    return {
        "present": True,
        "version": version or None,
        "major": int(head) if head.isdigit() else None,
    }


def _node_warning(required: str, node: dict[str, Any]) -> str:
    """Ce qui manque pour que la commande d'installation aboutisse."""
    if not required:
        return ""
    if not node.get("present"):
        return f"node absent, requires Node {required}+"
    major = node.get("major")
    if major is not None and major < int(required):
        return f"Node {node.get('version')} present, requires {required}+"
    return ""


def doctor_report(
    project: Path,
    *,
    live: bool = False,
    providers: dict[str, Provider] | None = None,
) -> dict[str, Any]:
    root = project.resolve()
    provider_map = providers or default_providers()
    report = {
        "project": str(root),
        "storage": _storage_status(root),
        "providers": [],
    }
    usage = {
        item.get("provider"): item for item in usage_status(force=False)
    }
    versions = {
        item["provider"]: item.get("current_version")
        for item in provider_audit()
    }
    refused = disabled_providers()
    windows = os.name == "nt"
    node = node_runtime()
    report["node"] = node
    for name, provider in provider_map.items():
        executable = resolve_executable(name)
        # Un binaire trouvé sous un nom générique mais qui n'a pas prouvé son
        # identité : le nommer vaut mieux que de le taire, car l'utilisateur
        # est le seul à pouvoir trancher.
        doubtful = (
            None if executable else resolve_executable(name, unconfirmed=True)
        )
        runtime_issue = (
            provider_runtime_issue(name, executable) if executable else None
        )
        item: dict[str, Any] = {
            "provider": name,
            "label": get_provider_spec(name).label,
            "installed": bool(executable) and not runtime_issue,
            "enabled": name not in refused,
            "executable": executable or doubtful,
            "unconfirmed": doubtful,
            "version": versions.get(name),
            "usage": usage.get(name),
            "runtime_issue": runtime_issue,
            # Trouver le binaire ne dit rien du compte : une CLI installée mais
            # jamais connectée echoue au premier run, avec une erreur que rien
            # ne rattache a sa cause. Joe ne peut pas le savoir sans la lancer,
            # mais il retient un refus d'authentification deja rencontre.
            "auth_failed": (recent_failure(name) or ("", 0))[0] == "authentication",
            # Une CLI depreciee reste installee et detectee : sans cette
            # mention, un utilisateur grand public voit des echecs sans cause
            # apparente, alors qu'une licence entreprise, elle, fonctionne.
            "deprecated": {
                "since": get_provider_spec(name).deprecated_since,
                "successor": get_provider_spec(name).successor,
                "successor_url": get_provider_spec(name).successor_url,
            },
            "install": {
                **install_hint(name, windows=windows),
                # Le prerequis ne concerne que ce qui reste a installer :
                # Codex tourne ici sous Node 20 alors que son paquet en
                # demande 22, donc l'annoncer pour une CLI presente serait
                # un faux probleme.
                "node_warning": (
                    ""
                    if executable
                    else _node_warning(get_provider_spec(name).requires_node, node)
                ),
            },
        }
        if live and executable and not runtime_issue:
            result = provider.run(
                "Réponds uniquement : OK",
                root,
                Intent.ANSWER,
                30,
                execution_mode="plan",
            )
            item["live"] = {
                "ok": result.ok and bool(result.stdout.strip()),
                "duration_seconds": round(result.duration_seconds, 2),
                "error": result.error_kind,
            }
        report["providers"].append(item)
    return report


def format_doctor(report: dict[str, Any]) -> str:
    """Le diagnostic est la première commande d'un nouvel arrivant.

    Elle est citée dans le README juste après l'installation, et elle répondait
    en français à qui lisait une documentation anglaise. Elle doit aussi dire
    quoi faire : constater qu'une CLI manque sans indiquer comment l'obtenir
    laisse exactement le problème qu'on venait résoudre.
    """
    storage = report["storage"]
    lines = [
        f"Joe doctor — {report['project']}",
        (
            "Storage: OK"
            if storage["writable"]
            else f"Storage: ERROR · {storage['message']}"
        ),
        "",
    ]
    missing = []
    for item in report["providers"]:
        label = item.get("label") or item["provider"]
        if item["installed"]:
            state = "installed"
        elif item.get("unconfirmed"):
            state = f"unconfirmed · found {item['unconfirmed']}"
        else:
            state = "not found"
            missing.append(item)
        if item.get("runtime_issue"):
            state = f"incomplete · {item['runtime_issue']}"
        version = f" · {item['version']}" if item.get("version") else ""
        retired = item.get("deprecated") or {}
        if retired.get("since"):
            state += f" · retired for consumer accounts on {retired['since']}"
        disabled = "" if item.get("enabled", True) else " · turned off in Joe"
        line = f"- {label}: {state}{version}{disabled}"
        live = item.get("live")
        if live:
            line += (
                f" · live OK ({live['duration_seconds']}s)"
                if live["ok"]
                else f" · live failed ({live['error'] or 'empty answer'})"
            )
        lines.append(line)
    for item in missing:
        install = item.get("install") or {}
        if not install.get("command") and not install.get("homepage"):
            continue
        lines.append("")
        lines.append(f"Install {item.get('label') or item['provider']}:")
        if install.get("command"):
            lines.append(f"    {install['command']}")
        # Sans ce rappel, `npm` echoue sur un message qui ne nomme jamais la
        # cause : l'utilisateur colle la commande et lit une erreur obscure.
        if install.get("node_warning"):
            lines.append(f"    ⚠ {install['node_warning']}")
        if install.get("sign_in"):
            lines.append(f"    then sign in with: {install['sign_in']}")
        if install.get("homepage"):
            lines.append(f"    {install['homepage']}")
    if missing:
        lines.append("")
        lines.append(
            "Joe only routes to the CLIs it can actually run. Install one and "
            "it becomes available on the next request — nothing to configure."
        )
    for item in report["providers"]:
        retired = item.get("deprecated") or {}
        if not retired.get("since"):
            continue
        lines.append("")
        label = item.get("label") or item["provider"]
        lines.append(
            f"{label} stopped serving consumer accounts on {retired['since']}. "
            "An enterprise licence still works; otherwise use "
            f"{retired.get('successor') or 'its successor'}."
        )
        if retired.get("successor_url"):
            lines.append(f"    {retired['successor_url']}")
    if not any("live" in item for item in report["providers"]):
        lines.append("")
        lines.append("Run `joe doctor --live` to send one short real request to each.")
    return "\n".join(lines)


def _storage_status(project: Path) -> dict[str, Any]:
    agentflow = project / ".agentflow"
    try:
        agentflow.mkdir(parents=True, exist_ok=True)
        probe = agentflow / ".doctor.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"writable": True, "path": str(agentflow), "message": None}
    except OSError as exc:
        return {
            "writable": False,
            "path": str(agentflow),
            "message": str(exc),
        }
