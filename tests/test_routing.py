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
                    "resets_at": 2_000_000,
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
                    "resets_at": 2_000_000,
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
