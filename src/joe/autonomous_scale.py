from __future__ import annotations

import math
from typing import Any


MAX_CAMPAIGN_SECONDS = 90 * 24 * 3600


def compute_scale_policy(
    duration_seconds: int, resource_policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Translate a campaign horizon into lightweight experimental-scale expectations."""
    total = max(60, min(MAX_CAMPAIGN_SECONDS, int(duration_seconds)))
    gpu_expected = (resource_policy or {}).get("mode") == "gpu_only"
    if total <= 2 * 3600:
        horizon, target, minimum, count, exploration = "short", 20 * 60, 5 * 60, 0, 0.30
    elif total <= 12 * 3600:
        horizon, target, minimum, count, exploration = "day", 2 * 3600, 30 * 60, 1, 0.20
    elif total <= 3 * 24 * 3600:
        horizon, target, minimum, count, exploration = "multi_day", 6 * 3600, 2 * 3600, 2, 0.15
    elif total <= 14 * 24 * 3600:
        horizon, target, minimum, count, exploration = "long", 12 * 3600, 4 * 3600, 2, 0.10
    else:
        horizon, target, minimum, count, exploration = "extended", 24 * 3600, 8 * 3600, 3, 0.08
    return {
        "horizon": horizon,
        "campaign_seconds": total,
        "exploration_budget_fraction": exploration,
        "target_substantive_run_seconds": min(target, max(minimum, total // 3)),
        "minimum_substantive_run_seconds": min(minimum, max(5 * 60, total // 6)),
        "minimum_substantive_runs": count,
        "gpu_expected": gpu_expected,
        "meaningful_gpu_utilization_pct": 10 if gpu_expected else None,
    }


def scale_audit(campaign: dict[str, Any]) -> dict[str, Any]:
    policy = campaign.get("compute_scale_policy") or compute_scale_policy(
        int(campaign.get("max_duration_seconds", 3600)), campaign.get("resource_policy")
    )
    experiments = [
        event for event in campaign.get("history") or [] if event.get("kind") == "experiment"
    ]
    minimum = int(policy.get("minimum_substantive_run_seconds") or 0)
    substantive = [
        event for event in experiments if float(event.get("duration_seconds") or 0) >= minimum
    ]
    durations = [float(event.get("duration_seconds") or 0) for event in experiments]
    gpu_seconds = 0.0
    measured_seconds = 0.0
    for event in experiments:
        resources = (event.get("metrics") or {}).get("resources") or {}
        utilization = resources.get("gpu_util_mean_pct")
        duration = float(event.get("duration_seconds") or 0)
        if isinstance(utilization, (int, float)):
            gpu_seconds += duration * float(utilization) / 100
            measured_seconds += duration
    return {
        "experiments": len(experiments),
        "longest_run_seconds": max(durations, default=0.0),
        "substantive_runs": len(substantive),
        "minimum_substantive_runs": int(policy.get("minimum_substantive_runs") or 0),
        "substantive_run_debt": max(
            0, int(policy.get("minimum_substantive_runs") or 0) - len(substantive)
        ),
        "experiment_seconds": sum(durations),
        "measured_gpu_utilization_pct": (
            round(100 * gpu_seconds / measured_seconds, 2) if measured_seconds else None
        ),
        "model_call_cadence_hint": (
            "Analyze only after a run ends, crashes, reaches a checkpoint, or changes a decision."
        ),
    }


def default_model_calls(iterations: int, mode: str, duration_seconds: int) -> int:
    multiplier = {"fast": 1, "review": 2, "consensus": 3}.get(mode, 2)
    normal = (max(1, iterations) + 1) * multiplier
    if duration_seconds <= 24 * 3600:
        return normal
    days = duration_seconds / (24 * 3600)
    long_horizon = max(6, math.ceil(6 + 2 * days)) * multiplier
    return min(normal, long_horizon)
