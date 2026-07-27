from joe.models import Intent, Mode, Route
from joe.usage import balance_route


def status(provider, remaining, reset):
    return {
        "provider": provider,
        "available": True,
        "windows": [
            {
                "name": "5 heures",
                "remaining_percent": remaining,
                "resets_at": reset,
            }
        ],
    }


def test_low_codex_with_distant_reset_switches_to_claude():
    route = Route(Intent.MODIFY, Mode.FAST, "codex", None, "preferred=codex")

    balanced = balance_route(
        route,
        [status("codex", 15, 20_000), status("claude", 70, 30_000)],
        now=10_000,
    )

    assert balanced.primary == "claude"
    assert "quota-switch=codex->claude" in balanced.reason


def test_low_claude_switches_back_to_codex():
    route = Route(Intent.ANALYZE, Mode.FAST, "claude", None, "preferred=claude")

    balanced = balance_route(
        route,
        [status("claude", 4, 10_200), status("codex", 80, 30_000)],
        now=10_000,
    )

    assert balanced.primary == "codex"


def test_near_reset_does_not_switch_unless_almost_exhausted():
    route = Route(Intent.MODIFY, Mode.FAST, "codex", None, "preferred=codex")

    balanced = balance_route(
        route,
        [status("codex", 15, 10_600), status("claude", 90, 30_000)],
        now=10_000,
    )

    assert balanced.primary == "codex"


def test_consensus_and_unknown_usage_are_not_rebalanced():
    consensus = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")
    fast = Route(Intent.ANALYZE, Mode.FAST, "codex")

    assert balance_route(consensus, [], now=10_000) == consensus
    assert balance_route(fast, [], now=10_000) == fast
