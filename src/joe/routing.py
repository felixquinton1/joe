from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Mode, Route
from .router import Router
from .route_classifier import RouteClassification, apply_classification
from .usage import admit_route, balance_route


@dataclass(frozen=True)
class RoutingDecision:
    route: Route
    quota_admission: dict[str, Any] | None
    classification: RouteClassification | None = None


def resolve_route(
    router: Router,
    request: str,
    statuses: list[dict[str, Any]],
    *,
    forced_agent: str | None = None,
    forced_mode: Mode | None = None,
    previous_provider: str | None = None,
    classification: RouteClassification | None = None,
) -> RoutingDecision:
    """Apply the shared lexical, balancing, and quota-admission pipeline."""
    route = router.route(
        request,
        forced_agent=forced_agent,
        forced_mode=forced_mode,
        previous_provider=previous_provider,
    )
    if not forced_agent and forced_mode is None:
        route = apply_classification(route, classification)
    if not forced_agent:
        route = balance_route(route, statuses)
    route, quota_admission = admit_route(
        route,
        statuses,
        forced_agent=bool(forced_agent),
        forced_mode=forced_mode is not None,
    )
    return RoutingDecision(route, quota_admission, classification)
