from __future__ import annotations

import re
import sys
import unicodedata
from typing import Any


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
    return {
        "title": title[:80],
        "objective": request.strip(),
        "urls": urls[:20],
        "max_duration_seconds": duration,
        "max_iterations": iterations,
        "restricted_data": bool(
            re.search(r"\b(?:donnees?|data|dataset|fichiers? locaux?|_root)\b", folded)
        ),
    }


def build_campaign_payload(
    request: str,
    conversation_id: str,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    urls = parsed.get("urls") or []
    sources = "\n".join(f"- {url}" for url in urls) or "- À découvrir depuis le brief."
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
        "command": [sys.executable, "autonomous_run.py"],
        "resume_command": [sys.executable, "autonomous_run.py", "--resume"],
        "working_directory": ".",
        "metrics_path": "artifacts/metrics.json",
        "metric_name": "primary_metric",
        "metric_direction": "max",
        "timeout_seconds": min(86400, max(300, int(parsed["max_duration_seconds"]))),
        "max_iterations": int(parsed["max_iterations"]),
        "max_duration_seconds": int(parsed["max_duration_seconds"]),
        "restricted_data": bool(parsed.get("restricted_data")),
        "schedule": {"timezone": "Europe/Paris", "windows": []},
        "checkpoint_path": "checkpoints/latest",
        "stop_signal_path": "artifacts/STOP_REQUESTED",
        "stop_grace_seconds": 30,
        "mode": "fast",
        "execution_mode": "workspace-write",
    }


def _duration_seconds(text: str) -> int:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(heures?|hours?|hrs?|h|min(?:utes?)?)\b", text)
    if not match:
        return 3600
    value = float(match.group(1).replace(",", "."))
    multiplier = 60 if match.group(2).startswith("min") else 3600
    return max(60, min(604800, round(value * multiplier)))


def _integer_after(text: str, pattern: str, default: int, minimum: int, maximum: int) -> int:
    match = re.search(pattern, text)
    return max(minimum, min(maximum, int(match.group(1)))) if match else default
