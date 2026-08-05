from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .capabilities import cached_provider_capabilities, select_model
from .models import Intent, Mode, ProviderResult, Route
from .provider_health import recent_failure
from .provider_registry import counterpart
from .provider_registry import get_provider_names
from .providers import Provider

CLASSIFIER_TIMEOUT_SECONDS = 6
CLASSIFIER_CACHE_SECONDS = 10 * 60
_cache: dict[str, tuple[float, "RouteClassification"]] = {}
_cooldowns: dict[str, float] = {}
_latencies: dict[str, float] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class RouteClassification:
    intent: str
    action: str
    complexity: str
    workflow: str
    provider: str
    model_tier: str
    effort: str
    confidence: float
    skill_name: str = ""
    skill_instructions: str = ""
    skill_scope: str = "project"
    classifier_provider: str = ""
    classifier_model: str = ""
    latency_ms: int = 0

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def should_classify(
    request: str,
    route: Route,
    *,
    forced_agent: bool = False,
    forced_mode: bool = False,
) -> bool:
    """Use the LLM only where lexical routing leaves meaningful uncertainty."""
    if forced_mode:
        return False
    if "explicit-" in route.reason or "health-check" in route.reason:
        return False
    length = len(request.strip())
    if re.search(r"\b(?:skill|comp[ée]tence)\b", request, re.IGNORECASE):
        return True
    if length < 70 and route.mode is Mode.FAST:
        return False
    if length > 8_000:
        return False
    return route.mode is Mode.FAST


def clear_classifier_state() -> None:
    with _lock:
        _cache.clear()
        _cooldowns.clear()
        _latencies.clear()


def classify_request(
    request: str,
    route: Route,
    providers: dict[str, Provider],
    statuses: list[dict[str, Any]],
    cwd: Path,
    *,
    forced_agent: bool = False,
    forced_mode: bool = False,
) -> RouteClassification | None:
    if os.environ.get("JOE_DISABLE_LLM_ROUTER") == "1":
        return None
    if not should_classify(
        request,
        route,
        forced_agent=forced_agent,
        forced_mode=forced_mode,
    ):
        return None
    cache_key = re.sub(r"\s+", " ", request.strip().casefold())
    with _lock:
        cached = _cache.get(cache_key)
        if cached and time.monotonic() - cached[0] <= CLASSIFIER_CACHE_SECONDS:
            return cached[1]
    selected = _select_classifier(providers, statuses)
    if selected is None:
        return None
    provider_name, provider, model = selected
    started = time.monotonic()
    result = provider.run(
        _classifier_prompt(request),
        cwd,
        Intent.ANALYZE,
        CLASSIFIER_TIMEOUT_SECONDS,
        model=model,
        effort="low",
        execution_mode="read-only",
    )
    latency_ms = round((time.monotonic() - started) * 1000)
    if not result.ok:
        with _lock:
            _cooldowns[provider_name] = time.monotonic() + 60
        return None
    parsed = _parse_classification(result, provider_name, model, latency_ms)
    if parsed is None:
        return None
    with _lock:
        previous = _latencies.get(provider_name)
        _latencies[provider_name] = (
            latency_ms if previous is None else previous * 0.7 + latency_ms * 0.3
        )
        _cache[cache_key] = (time.monotonic(), parsed)
    return parsed


def apply_classification(
    route: Route,
    classification: RouteClassification | None,
) -> Route:
    if classification is None or classification.confidence < 0.65:
        return route
    intent = route.intent
    if intent is not Intent.MODIFY:
        if (
            classification.intent == Intent.MODIFY.value
            and classification.confidence >= 0.85
        ):
            intent = Intent.MODIFY
        elif classification.intent in {Intent.ANSWER.value, Intent.ANALYZE.value}:
            intent = Intent(classification.intent)
    complexity = classification.complexity
    requested_mode = classification.workflow
    mode = route.mode
    if complexity in {"trivial", "simple"}:
        mode = Mode.FAST
    elif complexity == "moderate":
        mode = (
            Mode.REVIEW
            if intent is Intent.MODIFY and requested_mode == Mode.REVIEW.value
            else Mode.FAST
        )
    elif complexity == "complex":
        mode = (
            Mode.REVIEW
            if intent is Intent.MODIFY or requested_mode == Mode.REVIEW.value
            else Mode.FAST
        )
    elif complexity == "critical":
        mode = (
            Mode.CONSENSUS
            if requested_mode == Mode.CONSENSUS.value
            else Mode.REVIEW
        )
    provider = (
        classification.provider
        if classification.provider in get_provider_names()
        else route.primary
    )
    reviewer = None
    if mode is Mode.REVIEW:
        reviewer = counterpart(provider)
    reason = (
        f"{route.reason}; llm-classifier={classification.classifier_provider}; "
        f"complexity={complexity}; confidence={classification.confidence:.2f}; "
        f"tier={classification.model_tier}"
    )
    return Route(intent, mode, provider, reviewer, reason)


def _select_classifier(
    providers: dict[str, Provider],
    statuses: list[dict[str, Any]],
) -> tuple[str, Provider, str | None] | None:
    by_status = {str(item.get("provider")): item for item in statuses}
    capabilities = cached_provider_capabilities()
    now = time.monotonic()
    candidates = []
    for order, name in enumerate(("codex", "claude", "gemini", "copilot")):
        provider = providers.get(name)
        if provider is None or recent_failure(name):
            continue
        status = by_status.get(name)
        if status and status.get("available") is False:
            continue
        capability = capabilities.get(name, {})
        if capability and capability.get("available") is False:
            continue
        with _lock:
            if _cooldowns.get(name, 0) > now:
                continue
            latency = _latencies.get(name, 4_000)
        remaining = _remaining_percent(status)
        score = (
            remaining if remaining is not None else 50,
            -latency,
            -order,
        )
        candidates.append((score, name, provider, select_model(name, complex_request=False)))
    if not candidates:
        return None
    _, name, provider, model = max(candidates, key=lambda item: item[0])
    return name, provider, model


def _remaining_percent(status: dict[str, Any] | None) -> float | None:
    if not status:
        return None
    values = [
        float(window["remaining_percent"])
        for window in status.get("windows", [])
        if isinstance(window.get("remaining_percent"), (int, float))
    ]
    return min(values) if values else None


def _classifier_prompt(request: str) -> str:
    return f"""Classify one request for a local multi-AI orchestrator.
Return exactly one JSON object and no prose. Do not use tools or inspect files.

Allowed values:
- intent: answer | analyze | modify
- action: none | create_skill
- complexity: trivial | simple | moderate | complex | critical
- workflow: fast | review | consensus
- provider: codex | claude | gemini | copilot
- model_tier: light | standard | strong | long-context
- effort: low | medium | high
- skill_scope: project | global

Rules:
- consensus only for critical, genuinely consequential decisions.
- review for substantial implementation; fast for questions and small changes.
- prefer codex for code/debug/science, claude for architecture/review/docs,
  gemini for long context/research, copilot only for tiny isolated work.
- create_skill only when the user explicitly asks to create a reusable skill.
- confidence is a number from 0 to 1.
- for create_skill, extract skill_name and skill_instructions. Leave them empty
  when the request does not supply enough information.

Schema:
{{"intent":"answer","action":"none","complexity":"simple","workflow":"fast",
"provider":"codex","model_tier":"light","effort":"low","confidence":0.9,
"skill_name":"","skill_instructions":"","skill_scope":"project"}}

User request:
{request[:8000]}"""


def _parse_classification(
    result: ProviderResult,
    provider: str,
    model: str | None,
    latency_ms: int,
) -> RouteClassification | None:
    match = re.search(r"\{.*\}", result.stdout, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
        intent = str(payload["intent"])
        action = str(payload.get("action", "none"))
        complexity = str(payload["complexity"])
        workflow = str(payload["workflow"])
        selected_provider = str(payload["provider"])
        model_tier = str(payload["model_tier"])
        effort = str(payload["effort"])
        confidence = float(payload["confidence"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if intent not in {item.value for item in Intent}:
        return None
    if action not in {"none", "create_skill"}:
        return None
    if complexity not in {"trivial", "simple", "moderate", "complex", "critical"}:
        return None
    if workflow not in {item.value for item in Mode}:
        return None
    if selected_provider not in get_provider_names():
        return None
    if model_tier not in {"light", "standard", "strong", "long-context"}:
        return None
    if effort not in {"low", "medium", "high"} or not 0 <= confidence <= 1:
        return None
    scope = str(payload.get("skill_scope", "project"))
    if scope not in {"project", "global"}:
        scope = "project"
    return RouteClassification(
        intent=intent,
        action=action,
        complexity=complexity,
        workflow=workflow,
        provider=selected_provider,
        model_tier=model_tier,
        effort=effort,
        confidence=confidence,
        skill_name=str(payload.get("skill_name", "")).strip(),
        skill_instructions=str(payload.get("skill_instructions", "")).strip(),
        skill_scope=scope,
        classifier_provider=provider,
        classifier_model=model or "",
        latency_ms=latency_ms,
    )
