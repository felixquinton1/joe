from pathlib import Path

import pytest

from joe.models import Intent, Mode, ProviderResult, Route
from joe.route_classifier import (
    RouteClassification,
    apply_classification,
    classify_request,
    clear_classifier_state,
    should_classify,
)


class FakeProvider:
    def __init__(self, name: str, output: str, *, returncode: int = 0):
        self.name = name
        self.output = output
        self.returncode = returncode
        self.calls = []

    def run(self, prompt, cwd, intent, timeout, **kwargs):
        self.calls.append({
            "prompt": prompt,
            "cwd": cwd,
            "intent": intent,
            "timeout": timeout,
            **kwargs,
        })
        return ProviderResult(
            self.name,
            [self.name],
            self.output,
            "",
            self.returncode,
            0.01,
            error_kind=None if self.returncode == 0 else "timeout",
        )


@pytest.fixture(autouse=True)
def clean_classifier(monkeypatch):
    monkeypatch.delenv("JOE_DISABLE_LLM_ROUTER", raising=False)
    clear_classifier_state()
    yield
    clear_classifier_state()


def classification(**changes):
    payload = {
        "intent": "modify",
        "action": "none",
        "complexity": "moderate",
        "workflow": "review",
        "provider": "codex",
        "model_tier": "standard",
        "effort": "medium",
        "confidence": 0.9,
        "classifier_provider": "claude",
    }
    payload.update(changes)
    return RouteClassification(**payload)


def test_short_clear_question_stays_deterministic_but_skill_is_classified():
    route = Route(Intent.ANSWER, Mode.FAST, "codex", reason="answer; fast")
    assert not should_classify("Pourquoi cette erreur ?", route)
    assert should_classify("Fais une compétence réutilisable", route)


def test_classifier_uses_healthy_quota_and_returns_strict_decision(
    tmp_path, monkeypatch
):
    output = """{"intent":"modify","action":"none","complexity":"moderate",
    "workflow":"review","provider":"codex","model_tier":"standard",
    "effort":"medium","confidence":0.91,"skill_name":"",
    "skill_instructions":"","skill_scope":"project"}"""
    codex = FakeProvider("codex", output)
    claude = FakeProvider("claude", output)
    monkeypatch.setattr(
        "joe.route_classifier.cached_provider_capabilities",
        lambda: {
            "codex": {"available": True},
            "claude": {"available": True},
        },
    )
    monkeypatch.setattr(
        "joe.route_classifier.select_model",
        lambda provider, complex_request: f"{provider}-light",
    )
    route = Route(Intent.ANALYZE, Mode.FAST, "codex", reason="analyze; fast")
    result = classify_request(
        "Analyse cette modification assez ambiguë et choisis la bonne stratégie.",
        route,
        {"codex": codex, "claude": claude},
        [
            {"provider": "codex", "available": True, "windows": [{"remaining_percent": 20}]},
            {"provider": "claude", "available": True, "windows": [{"remaining_percent": 90}]},
        ],
        tmp_path,
    )

    assert result is not None
    assert result.classifier_provider == "claude"
    assert result.classifier_model == "claude-light"
    assert result.model_tier == "standard"
    assert not codex.calls
    assert claude.calls[0]["timeout"] == 6
    assert claude.calls[0]["execution_mode"] == "read-only"


def test_classifier_failure_falls_back_without_trying_every_provider(
    tmp_path, monkeypatch
):
    failed = FakeProvider("codex", "", returncode=124)
    backup = FakeProvider("claude", "{}")
    monkeypatch.setattr(
        "joe.route_classifier.cached_provider_capabilities",
        lambda: {},
    )
    monkeypatch.setattr(
        "joe.route_classifier.select_model",
        lambda provider, complex_request: "light",
    )
    route = Route(Intent.ANALYZE, Mode.FAST, "codex", reason="analyze; fast")
    result = classify_request(
        "Analyse une demande suffisamment longue mais difficile à catégoriser correctement.",
        route,
        {"codex": failed, "claude": backup},
        [],
        tmp_path,
    )
    assert result is None
    assert len(failed.calls) == 1
    assert not backup.calls


def test_classification_cannot_downgrade_writes_or_overuse_consensus():
    modifying = Route(Intent.MODIFY, Mode.FAST, "codex", reason="modify; fast")
    result = apply_classification(
        modifying,
        classification(
            intent="answer",
            complexity="simple",
            workflow="consensus",
            provider="claude",
        ),
    )
    assert result.intent is Intent.MODIFY
    assert result.mode is Mode.FAST

    critical = apply_classification(
        Route(Intent.ANALYZE, Mode.FAST, "codex", reason="analyze; fast"),
        classification(complexity="critical", workflow="consensus"),
    )
    assert critical.mode is Mode.CONSENSUS

    complex_review = apply_classification(
        Route(Intent.ANSWER, Mode.FAST, "codex", reason="answer; fast"),
        classification(
            intent="analyze",
            complexity="complex",
            workflow="review",
            provider="claude",
        ),
    )
    assert complex_review.intent is Intent.ANALYZE
    assert complex_review.mode is Mode.REVIEW
    assert complex_review.primary == "claude"
