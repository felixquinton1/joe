from __future__ import annotations

import re
import sys
import unicodedata
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .autonomous_resources import normalize_resource_policy


def parse_autonomous_request(request: str) -> dict[str, Any] | None:
    """Parse an explicit natural-language request to create an Autonomous run."""
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", request.casefold())
        if not unicodedata.combining(character)
    )
    if "autonomous" not in folded:
        return None
    if not re.search(r"\b(?:cree|creer|lance|lancer|demarre|demarrer|configure|configurer)\b", folded):
        return None
    urls = re.findall(r"https?://[^\s<>()]+", request)
    duration = _duration_seconds(folded)
    iterations = _integer_after(folded, r"(\d+)\s+iterations?", 20, 1, 50)
    title_match = re.search(
        r"(?i)(?:challenge|projet|campagne)\s+[«\"']?([^\n,.;:]{2,80})",
        request,
    )
    title = (title_match.group(1).strip(" «»\"'") if title_match else "Nouvelle campagne")
    resource_policy = _resource_policy_from_request(folded)
    return {
        "title": title[:80],
        "objective": request.strip(),
        "urls": urls[:20],
        "max_duration_seconds": duration,
        "max_iterations": iterations,
        "restricted_data": bool(
            re.search(r"\b(?:donnees?|data|dataset|fichiers? locaux?|_root)\b", folded)
        ),
        "schedule": _schedule_from_request(folded, duration),
        "metric_name": "log_loss" if "log loss" in folded else "primary_metric",
        "metric_direction": "min" if "log loss" in folded else "max",
        "resource_policy": resource_policy,
    }


def build_campaign_payload(
    request: str,
    conversation_id: str,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    urls = parsed.get("urls") or []
    sources = "\n".join(f"- {url}" for url in urls) or "- À découvrir depuis le brief."
    resource_policy = normalize_resource_policy(parsed.get("resource_policy"))
    preflight_metrics_path = "artifacts/preflight.json"
    return {
        "title": f"{parsed['title']} — Autonomous",
        "conversation_id": conversation_id,
        "objective": parsed["objective"],
        "research_protocol": (
            "Commencer par les sources officielles publiques, identifier les règles, "
            "les métriques et les approches comparables, puis justifier chaque choix. "
            "Revenir à la bibliographie après un plateau, un résultat surprenant, une "
            "incertitude méthodologique ou des échecs répétés, pas selon une cadence "
            "fixe qui consomme des prompts sans signal nouveau."
        ),
        "data_policy": (
            "Les données privées restent locales. Ne transmettre à une IA que du code, "
            "de la documentation publique, des agrégats nettoyés et des erreurs assainies. "
            "Ne jamais effectuer de soumission ou d’action externe sans autorisation explicite."
        ),
        "campaign_context": (
            "Brief original fourni par l’utilisateur :\n"
            f"{request.strip()}\n\nSources explicites :\n{sources}\n\n"
            "Joe doit découvrir et formaliser les détails propres au projet avant de coder. "
            "Le workspace Git de la conversation est le dépôt actif. Il doit viser la "
            "meilleure performance validée dans le budget imparti, mesurer les ressources "
            "disponibles et consacrer l'essentiel du temps aux expériences locales plutôt "
            "qu'aux appels de modèles."
        ),
        "research_refresh_interval": 0,
        "command": [sys.executable, "-m", "joe.autonomous_entrypoint"],
        "resume_command": [sys.executable, "-m", "joe.autonomous_entrypoint", "--resume"],
        "working_directory": ".",
        "metrics_path": "artifacts/metrics.json",
        "metric_name": str(parsed.get("metric_name", "primary_metric")),
        "metric_direction": str(parsed.get("metric_direction", "max")),
        "timeout_seconds": min(86400, max(300, int(parsed["max_duration_seconds"]))),
        "max_iterations": int(parsed["max_iterations"]),
        "max_duration_seconds": int(parsed["max_duration_seconds"]),
        "restricted_data": bool(parsed.get("restricted_data")),
        "resource_policy": resource_policy,
        "preflight_command": [
            sys.executable, "-m", "joe.autonomous_preflight",
            "--output", preflight_metrics_path,
            "--policy-json", json.dumps(resource_policy, ensure_ascii=False),
        ],
        "preflight_metrics_path": preflight_metrics_path,
        "preflight_timeout_seconds": 120,
        "schedule": parsed.get("schedule") or {"timezone": "Europe/Paris", "windows": []},
        "checkpoint_path": "checkpoints/latest",
        "stop_signal_path": "artifacts/STOP_REQUESTED",
        "stop_grace_seconds": 30,
        "mode": "fast",
        "execution_mode": "workspace-write",
    }


def _resource_policy_from_request(text: str) -> dict[str, Any]:
    mode = "auto"
    if re.search(r"\b(?:gpu uniquement|uniquement (?:sur )?(?:le )?gpu|gpu only)\b", text):
        mode = "gpu_only"
    elif re.search(r"\b(?:cpu uniquement|uniquement (?:sur )?(?:le )?cpu|cpu only)\b", text):
        mode = "cpu_only"
    index_match = re.search(r"\bgpu\s*(?:numero|n[°o]?|index)?\s*[:#]?\s*(\d+)\b", text)
    return normalize_resource_policy({
        "mode": mode,
        "gpu_index": int(index_match.group(1)) if index_match else None,
    })


def _duration_seconds(text: str) -> int:
    explicit = re.search(
        r"pendant\s+(\d+(?:[.,]\d+)?)\s*(heures?|hours?|hrs?|h|min(?:utes?)?)\b",
        text,
    )
    if explicit:
        value = float(explicit.group(1).replace(",", "."))
        multiplier = 60 if explicit.group(2).startswith("min") else 3600
        return max(60, min(604800, round(value * multiplier)))
    window = re.search(
        r"(?:de|entre)\s*(\d{1,2})(?:\s*h(?:\s*(\d{1,2}))?)?\s*(?:a|et)\s*"
        r"(\d{1,2})(?:\s*h(?:\s*(\d{1,2}))?)?",
        text,
    )
    if window:
        start = int(window.group(1)) * 60 + int(window.group(2) or 0)
        end = int(window.group(3)) * 60 + int(window.group(4) or 0)
        minutes = (end - start) % (24 * 60)
        return max(60, minutes * 60)
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(heures?|hours?|hrs?|h|min(?:utes?)?)\b", text)
    if not match:
        return 3600
    value = float(match.group(1).replace(",", "."))
    multiplier = 60 if match.group(2).startswith("min") else 3600
    return max(60, min(604800, round(value * multiplier)))


def _integer_after(text: str, pattern: str, default: int, minimum: int, maximum: int) -> int:
    match = re.search(pattern, text)
    return max(minimum, min(maximum, int(match.group(1)))) if match else default


def _schedule_from_request(text: str, duration_seconds: int) -> dict[str, Any]:
    timezone = ZoneInfo("Europe/Paris")
    now = datetime.now(timezone)
    range_match = re.search(
        r"(?:de|entre)\s*(\d{1,2})(?:\s*h(?:\s*(\d{1,2}))?)?\s*(?:a|et)\s*"
        r"(\d{1,2})(?:\s*h(?:\s*(\d{1,2}))?)?",
        text,
    )
    at_match = re.search(r"\ba\s*(\d{1,2})\s*h(?:\s*(\d{1,2}))?\b", text)
    if not range_match and not at_match:
        return {"timezone": "Europe/Paris", "windows": []}
    if range_match:
        start_hour, start_minute = int(range_match.group(1)), int(range_match.group(2) or 0)
        end_hour, end_minute = int(range_match.group(3)), int(range_match.group(4) or 0)
    else:
        start_hour, start_minute = int(at_match.group(1)), int(at_match.group(2) or 0)
        end = datetime(2000, 1, 1, start_hour, start_minute) + timedelta(seconds=duration_seconds)
        end_hour, end_minute = end.hour, end.minute
    if not (0 <= start_hour < 24 and 0 <= end_hour < 24 and 0 <= start_minute < 60 and 0 <= end_minute < 60):
        return {"timezone": "Europe/Paris", "windows": []}
    target = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    if "demain" in text:
        target += timedelta(days=1)
    elif "cette nuit" in text and target <= now:
        target += timedelta(days=1)
    elif target <= now:
        target += timedelta(days=1)
    return {
        "timezone": "Europe/Paris",
        "windows": [{
            "days": [target.weekday()],
            "start": f"{start_hour:02d}:{start_minute:02d}",
            "end": f"{end_hour:02d}:{end_minute:02d}",
        }],
    }
