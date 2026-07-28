from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def record_usage(
    raw_output: str,
    target: Path,
    *,
    now: float,
) -> None:
    stats = gemini_stats(raw_output)
    if not stats:
        return
    try:
        payload = json.loads(target.read_text()) if target.exists() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    day = datetime.fromtimestamp(now).date().isoformat()
    days = payload.setdefault("days", {})
    current = days.setdefault(
        day,
        {"tokens": 0, "requests": 0, "models": {}},
    )
    current["tokens"] += stats["tokens"]
    current["requests"] += stats["requests"]
    for model, model_stats in stats["models"].items():
        item = current["models"].setdefault(
            model,
            {"tokens": 0, "requests": 0},
        )
        item["tokens"] += model_stats["tokens"]
        item["requests"] += model_stats["requests"]
    payload["last"] = {"at": now, **stats}
    payload["days"] = dict(sorted(days.items())[-30:])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    os.replace(temporary, target)


def gemini_stats(raw_output: str) -> dict[str, Any] | None:
    payloads = []
    for line in raw_output.splitlines():
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not payloads:
        start = raw_output.find("{")
        if start >= 0:
            try:
                payloads.append(json.loads(raw_output[start:]))
            except json.JSONDecodeError:
                pass
    stats = next(
        (
            payload.get("stats")
            for payload in reversed(payloads)
            if isinstance(payload, dict)
            and isinstance(payload.get("stats"), dict)
        ),
        None,
    )
    if not stats:
        return None
    models = {}
    for model, values in stats.get("models", {}).items():
        if not isinstance(values, dict):
            continue
        tokens = values.get("tokens", {})
        api = values.get("api", {})
        models[str(model)] = {
            "tokens": int(tokens.get("total", values.get("total_tokens", 0)) or 0),
            "requests": int(api.get("totalRequests", 1) or 0),
        }
    if not models:
        return None
    return {
        "tokens": sum(item["tokens"] for item in models.values()),
        "requests": sum(item["requests"] for item in models.values()),
        "models": models,
    }


def gemini_usage_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "joe" / "gemini_usage.json"


def gemini_status(
    path: Path | None = None,
    settings_path: Path | None = None,
) -> dict[str, Any]:
    target = path or gemini_usage_path()
    auth_type = gemini_auth_type(settings_path)
    quota_description = gemini_quota_description(auth_type)
    try:
        payload = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return {
            "provider": "gemini",
            "available": True,
            "plan": None,
            "windows": [],
            "metrics": [{"name": "Quota", "value": quota_description}],
            "message": (
                "Aucun appel Joe mesuré · pas de réserve globale de tokens exposée"
            ),
        }
    day = datetime.now().date().isoformat()
    current = payload.get("days", {}).get(
        day, {"tokens": 0, "requests": 0, "models": {}}
    )
    last = payload.get("last", {})
    models = ", ".join(sorted(current.get("models", {}))) or "aucun"
    return {
        "provider": "gemini",
        "available": True,
        "plan": None,
        "windows": [],
        "metrics": [
            {"name": "Tokens aujourd’hui", "value": f"{current['tokens']:,}"},
            {"name": "Requêtes aujourd’hui", "value": str(current["requests"])},
            {"name": "Quota", "value": quota_description},
            {"name": "Modèles utilisés", "value": models},
            {
                "name": "Dernier appel",
                "value": (
                    datetime.fromtimestamp(float(last["at"])).strftime("%H:%M")
                    if last.get("at")
                    else "aucun"
                ),
            },
        ],
        "message": (
            "Consommation Joe uniquement · Gemini limite surtout les requêtes "
            "selon le modèle et l’offre · détail via /stats model"
        ),
    }


def gemini_auth_type(path: Path | None = None) -> str | None:
    target = path or Path.home() / ".gemini" / "settings.json"
    try:
        payload = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    selected = payload.get("security", {}).get("auth", {}).get("selectedType")
    return str(selected) if selected else None


def gemini_quota_description(auth_type: str | None) -> str:
    if auth_type == "gemini-api-key":
        return "Clé API · variable par modèle/offre"
    if auth_type:
        return "Compte Google · plafond en requêtes"
    return "Limites variables par modèle/offre"
