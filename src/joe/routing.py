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
    forced_reviewer: str | None = None,
    previous_provider: str | None = None,
    classification: RouteClassification | None = None,
    wait_for_provider: bool = False,
) -> RoutingDecision:
    """Apply the shared lexical, balancing, and quota-admission pipeline."""
    route = router.route(
        request,
        forced_agent=forced_agent,
        forced_mode=forced_mode,
        forced_reviewer=forced_reviewer,
        previous_provider=previous_provider,
    )
    if not forced_agent and forced_mode is None and not forced_reviewer:
        route = apply_classification(route, classification)
    if not forced_agent and not forced_reviewer:
        route = balance_route(route, statuses)
    route, quota_admission = admit_route(
        route,
        statuses,
        forced_agent=bool(forced_agent),
        forced_mode=forced_mode is not None or bool(forced_reviewer),
        wait_for_provider=wait_for_provider,
    )
    return RoutingDecision(route, quota_admission, classification)
