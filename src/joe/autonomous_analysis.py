from __future__ import annotations

from typing import Any


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
        node_id = str(experiment.get("id") or event.get("id") or f"experiment-{index + 1}")
        if quality == "final":
            baseline = value if baseline is None else baseline
            if best is None or (direction == "min" and value < best) or (
                direction == "max" and value > best
            ):
                best = value
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
            "metric": value,
            "hypothesis": experiment.get("hypothesis") or event.get("hypothesis"),
            "variant": experiment.get("variant"),
            "duration_seconds": event.get("duration_seconds"),
            "checkpoint_available": bool(event.get("checkpoint_available")),
            "reason": (
                None if quality == "final"
                else event.get("error") or (
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
            "baseline_metric": baseline,
            "best_metric": best,
            "improvement": improvement,
        },
        "usage": usage,
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
