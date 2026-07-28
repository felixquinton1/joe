from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .maintenance import provider_audit
from .models import Intent
from .providers import Provider, default_providers
from .usage import usage_status


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
    for name, provider in provider_map.items():
        executable = shutil.which(provider.executable)
        item: dict[str, Any] = {
            "provider": name,
            "installed": bool(executable),
            "executable": executable,
            "version": versions.get(name),
            "usage": usage.get(name),
        }
        if live and executable:
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
    storage = report["storage"]
    lines = [
        f"Joe doctor — {report['project']}",
        (
            "Stockage : OK"
            if storage["writable"]
            else f"Stockage : ERREUR · {storage['message']}"
        ),
    ]
    for item in report["providers"]:
        state = "installé" if item["installed"] else "absent"
        version = f" · {item['version']}" if item.get("version") else ""
        line = f"- {item['provider'].capitalize()} : {state}{version}"
        live = item.get("live")
        if live:
            line += (
                f" · live OK ({live['duration_seconds']} s)"
                if live["ok"]
                else f" · live échec ({live['error'] or 'réponse vide'})"
            )
        lines.append(line)
    if not any("live" in item for item in report["providers"]):
        lines.append("Utilise `joe doctor --live` pour tester les CLI réellement.")
    return "\n".join(lines)


def _storage_status(project: Path) -> dict[str, Any]:
    agentflow = project / ".agentflow"
    try:
        agentflow.mkdir(parents=True, exist_ok=True)
        probe = agentflow / ".doctor.tmp"
        probe.write_text("ok")
        probe.unlink()
        return {"writable": True, "path": str(agentflow), "message": None}
    except OSError as exc:
        return {
            "writable": False,
            "path": str(agentflow),
            "message": str(exc),
        }
