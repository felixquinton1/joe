from __future__ import annotations

from typing import Any

from .autonomous_scale import scale_audit


TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "reasoning_tokens",
)


def metric_value(metrics: dict[str, Any], name: str) -> float | None:
    primary = metrics.get("primary_metric")
    if isinstance(primary, dict) and primary.get("name") == name:
        return _number(primary.get("value"))
    aliases = (name, f"selected_{name}", f"{name}_mean", f"raw_{name}")
    containers = [metrics]
    for key in ("summary", "aggregate", "metrics"):
        value = metrics.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for alias in aliases:
            value = _number(container.get(alias))
            if value is not None:
                return value
    return None


def analyze_campaign(campaign: dict[str, Any]) -> dict[str, Any]:
    metric_name = str(campaign.get("metric_name") or "score")
    direction = "min" if campaign.get("metric_direction") == "min" else "max"
    nodes: list[dict[str, Any]] = []
    previous_id: str | None = None
    best: float | None = None
    baseline: float | None = None
    reference_validation: tuple[Any, ...] | None = None
    non_improving_streak = 0
    for index, event in enumerate(
        item for item in (campaign.get("history") or []) if item.get("kind") == "experiment"
    ):
        metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
        value = metric_value(metrics, metric_name)
        status = str(event.get("status") or "unknown")
        quality = "final" if status == "completed" and value is not None else (
            "partial" if value is not None else "missing"
        )
        experiment = metrics.get("experiment") if isinstance(metrics.get("experiment"), dict) else {}
        validation = metrics.get("validation") if isinstance(metrics.get("validation"), dict) else {}
        signature = _validation_signature(validation)
        contract_matches = (
            isinstance(metrics.get("primary_metric"), dict)
            and metrics["primary_metric"].get("name") == metric_name
            and metrics["primary_metric"].get("direction") == direction
        )
        comparable = bool(contract_matches and signature)
        if comparable and reference_validation is None:
            reference_validation = signature
        elif comparable and signature != reference_validation:
            comparable = False
        node_id = str(experiment.get("id") or event.get("id") or f"experiment-{index + 1}")
        if quality == "final" and comparable:
            baseline = value if baseline is None else baseline
            if best is None or (direction == "min" and value < best) or (
                direction == "max" and value > best
            ):
                best = value
                non_improving_streak = 0
            else:
                non_improving_streak += 1
        nodes.append({
            "id": node_id,
            "parent_id": (
                experiment.get("parent_experiment_id")
                or event.get("parent_experiment_id")
                or previous_id
            ),
            "iteration": event.get("iteration", index + 1),
            "status": status,
            "quality": quality,
            "comparable": comparable,
            "validation_status": (
                "comparable" if comparable else (
                    "incompatible" if contract_matches and signature else "unverified"
                )
            ),
            "metric": value,
            "hypothesis": experiment.get("hypothesis") or event.get("hypothesis"),
            "variant": experiment.get("variant"),
            "decision": {
                "expected_outcome": experiment.get("expected_outcome"),
                "decision_rule": experiment.get("decision_rule"),
                "estimated_gpu_minutes": experiment.get("estimated_gpu_minutes"),
            },
            "duration_seconds": event.get("duration_seconds"),
            "checkpoint_available": bool(event.get("checkpoint_available")),
            "checkpoint": event.get("checkpoint") or {
                "available": bool(event.get("checkpoint_available"))
            },
            "reproducibility": metrics.get("reproducibility") or {},
            "reason": (
                None if quality == "final" and comparable
                else event.get("error") or (
                    "Validation absente ou incompatible avec la référence de campagne."
                    if quality == "final" and not comparable else
                    "Processus interrompu : métrique informative mais exclue du meilleur score."
                    if quality == "partial" else "Aucune métrique exploitable publiée."
                )
            ),
        })
        previous_id = node_id
    usage = model_usage(campaign)
    budget = dict(campaign.get("token_budget") or {})
    improvement = None
    if baseline is not None and best is not None:
        improvement = baseline - best if direction == "min" else best - baseline
    return {
        "metric_name": metric_name,
        "metric_direction": direction,
        "experiments": nodes,
        "summary": {
            "total": len(nodes),
            "final": sum(node["quality"] == "final" for node in nodes),
            "partial": sum(node["quality"] == "partial" for node in nodes),
            "missing": sum(node["quality"] == "missing" for node in nodes),
            "comparable": sum(node["comparable"] for node in nodes),
            "baseline_metric": baseline,
            "best_metric": best,
            "improvement": improvement,
            "non_improving_streak": non_improving_streak,
        },
        "usage": usage,
        "compute": scale_audit(campaign),
        "compute_scale_policy": dict(campaign.get("compute_scale_policy") or {}),
        "checkpoints": [
            {"experiment_id": node["id"], **node["checkpoint"]}
            for node in nodes if node["checkpoint"].get("available")
        ],
        "token_budget": {
            "max_tokens": budget.get("max_tokens"),
            "max_model_calls": budget.get("max_model_calls"),
            "tokens_remaining": _remaining(budget.get("max_tokens"), usage["known_tokens"]),
            "model_calls_remaining": _remaining(
                budget.get("max_model_calls"), usage["model_calls"]
            ),
            "enforcement": (
                "exact" if usage["unknown_usage_calls"] == 0
                else "partial_provider_telemetry"
            ),
        },
    }


def iteration_report(
    campaign: dict[str, Any], result: dict[str, Any], analysis: dict[str, Any]
) -> str:
    """Build a short human report while keeping raw telemetry in campaign history."""
    iteration = int(campaign.get("iteration") or 0)
    status = str(result.get("status") or "unknown")
    duration_value = _number(result.get("duration_seconds"))
    duration = None if duration_value is None else f"{duration_value:.1f}".rstrip("0").rstrip(".")
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    primary = metrics.get("primary_metric") if isinstance(metrics.get("primary_metric"), dict) else {}
    metric_name = str(primary.get("name") or campaign.get("metric_name") or "score")
    metric = _number(primary.get("value"))
    if metric is None:
        metric = metric_value(metrics, metric_name)
    experiment = metrics.get("experiment") if isinstance(metrics.get("experiment"), dict) else {}
    validation = metrics.get("validation") if isinstance(metrics.get("validation"), dict) else {}
    nodes = analysis.get("experiments") or []
    current = nodes[-1] if nodes else {}
    best = _number((analysis.get("summary") or {}).get("best_metric"))

    status_text = {
        "completed": "terminée", "crashed": "en échec", "timed_out": "arrêtée par délai",
        "interrupted": "interrompue", "cancelled": "annulée",
    }.get(status, status)
    headline = f"**Expérience {status_text}**"
    if duration is not None:
        headline += f" · {duration} s"
    variant = str(experiment.get("variant") or "").strip()
    if variant:
        headline += f" · `{variant}`"

    lines = [f"### Autonomous — itération {iteration}", "", headline]
    if metric is not None:
        lines.extend([
            "", "| Indicateur | Résultat |", "| --- | ---: |",
            f"| **{_clean_cell(metric_name)}** | **{_format_number(metric)}** |",
        ])
        if best is not None:
            lines.append(f"| Meilleur de la campagne | {_format_number(best)} |")
        validation_text = _validation_label(validation, bool(current.get("comparable")))
        if validation_text:
            lines.append(f"| Validation | {_clean_cell(validation_text)} |")
        for name, value in _secondary_metrics(metrics, limit=2):
            lines.append(f"| {_clean_cell(name)} | {_format_number(value)} |")

    hypothesis = str(experiment.get("hypothesis") or "").strip()
    if hypothesis:
        lines.extend(["", f"**Hypothèse testée.** {hypothesis}"])
    if status != "completed":
        error = str(result.get("error") or metrics.get("error") or "").strip()
        if error:
            lines.extend(["", f"**À corriger.** {error}"])
    elif metric is None:
        lines.extend(["", "Aucune métrique principale exploitable n’a été publiée."])
    lines.extend(["", "Les métriques techniques complètes restent disponibles dans le journal détaillé."])
    return "\n".join(lines)


def _secondary_metrics(metrics: dict[str, Any], limit: int) -> list[tuple[str, float]]:
    values = metrics.get("secondary_metrics")
    if not isinstance(values, dict):
        return []
    selected: list[tuple[str, float]] = []
    for name, value in values.items():
        number = _number(value)
        if number is not None:
            selected.append((str(name), number))
            if len(selected) >= limit:
                break
    return selected


def _validation_label(validation: dict[str, Any], comparable: bool) -> str:
    parts = ["comparable" if comparable else "non comparable"]
    strategy = str(validation.get("strategy") or "").strip()
    if strategy:
        parts.append(strategy)
    folds = validation.get("folds")
    if isinstance(folds, int) and folds > 1:
        parts.append(f"{folds} folds")
    return " · ".join(parts)


def _format_number(value: Any, digits: int = 6) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:.{digits}g}"


def _clean_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def model_usage(campaign: dict[str, Any]) -> dict[str, int]:
    calls = known_tokens = unknown = prompts = 0
    for event in campaign.get("history") or []:
        if event.get("kind") != "agent_step" or event.get("status") not in {
            "completed", "integrated",
        }:
            continue
        attempts = event.get("attempts") or []
        if not attempts and event.get("provider"):
            attempts = [{"provider": event.get("provider")}]
        prompts += 1
        calls += len(attempts)
        for attempt in attempts:
            usage = attempt.get("usage") if isinstance(attempt.get("usage"), dict) else None
            if usage is None:
                unknown += 1
                continue
            known_tokens += sum(int(usage.get(field) or 0) for field in TOKEN_FIELDS)
    return {
        "prompts": prompts,
        "model_calls": calls,
        "known_tokens": known_tokens,
        "unknown_usage_calls": unknown,
    }


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _remaining(limit: Any, consumed: int) -> int | None:
    try:
        return max(0, int(limit) - consumed) if limit is not None else None
    except (TypeError, ValueError):
        return None


def _validation_signature(validation: dict[str, Any]) -> tuple[Any, ...] | None:
    strategy = str(validation.get("strategy") or "").strip()
    controls = tuple(sorted(str(item) for item in (validation.get("leakage_controls") or [])))
    if not strategy or not controls:
        return None
    return (
        strategy,
        validation.get("folds"),
        validation.get("split_fingerprint") or "unspecified-split",
        validation.get("data_fingerprint") or "unspecified-data",
        controls,
    )
