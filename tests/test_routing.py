from joe.models import Intent, Mode
from joe.router import Router
from joe.routing import resolve_route


def test_shared_pipeline_balances_then_admits_a_route():
    statuses = [
        {
            "provider": "codex",
            "available": True,
            "windows": [
                {
                    "remaining_percent": 4,
                        "resets_at": 2_000_000_000,
                    "duration_minutes": 10_080,
                }
            ],
        },
        {
            "provider": "claude",
            "available": True,
            "windows": [
                {
                    "remaining_percent": 80,
                        "resets_at": 2_000_000_000,
                    "duration_minutes": 10_080,
                }
            ],
        },
    ]

    decision = resolve_route(
        Router(),
        "Analyse ce code",
        statuses,
    )

    assert decision.route.intent is Intent.ANALYZE
    assert decision.route.mode is Mode.FAST
    assert decision.route.primary == "claude"
    assert "quota-switch=codex->claude" in decision.route.reason


def test_explicit_reviewer_survives_balancing_and_quota_admission():
    statuses = [
        {"provider": "codex", "available": True, "windows": []},
        {"provider": "deepseek", "available": True, "windows": [],
         "error_kind": "insufficient_balance", "retry_at": None},
    ]
    decision = resolve_route(
        Router(frozenset({"codex", "deepseek"})), "Analyse ce code", statuses,
        forced_agent="codex", forced_reviewer="deepseek",
    )
    assert decision.route.mode is Mode.REVIEW
    assert decision.route.primary == "codex"
    assert decision.route.reviewer == "deepseek"


def test_explicit_reviewer_with_auto_primary_selects_another_automatic_provider():
    route = Router(frozenset({"codex", "claude", "deepseek"})).route(
        "Analyse ce code", forced_reviewer="codex",
    )
    assert route.mode is Mode.REVIEW
    assert route.primary == "claude"
    assert route.reviewer == "codex"


def test_invalid_explicit_review_pairs_are_rejected():
    import pytest

    router = Router(frozenset({"codex", "deepseek"}))
    for kwargs in [
        {"forced_agent": "codex", "forced_reviewer": "codex"},
        {"forced_reviewer": "missing"},
        {"forced_reviewer": "claude"},
        {"forced_reviewer": "deepseek", "forced_mode": Mode.FAST},
    ]:
        with pytest.raises(ValueError):
            router.route("Analyse ce code", **kwargs)
