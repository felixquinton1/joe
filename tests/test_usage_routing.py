from joe.models import Intent, Mode, Route
from joe.usage import _next_quota_reset, admit_route, balance_route


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
        [status("codex", 15, 100_000), status("claude", 70, 130_000)],
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


def test_a_provider_outside_the_historic_pair_also_switches_on_low_quota():
    """La bascule ne dépend plus d'une liste de deux noms écrite en dur.

    Tout fournisseur à court de quota doit pouvoir passer la main à son pair,
    sans quoi un nouveau venu reste collé à un quota épuisé.
    """
    route = Route(Intent.MODIFY, Mode.FAST, "cursor-agent", None, "preferred=cursor")

    balanced = balance_route(
        route,
        [status("cursor-agent", 3, 100_000), status("codex", 90, 130_000)],
        now=10_000,
    )

    assert balanced.primary == "codex"
    assert "quota-switch=cursor-agent->codex" in balanced.reason


def test_reset_within_24_hours_does_not_switch_unless_almost_exhausted():
    route = Route(Intent.MODIFY, Mode.FAST, "codex", None, "preferred=codex")

    balanced = balance_route(
        route,
        [status("codex", 15, 80_000), status("claude", 90, 130_000)],
        now=10_000,
    )

    assert balanced.primary == "codex"


def test_long_window_can_trigger_even_when_short_window_resets_soon():
    route = Route(Intent.MODIFY, Mode.FAST, "codex", None, "preferred=codex")
    codex = {
        "provider": "codex",
        "available": True,
        "windows": [
            {"name": "5 heures", "remaining_percent": 15, "resets_at": 12_000},
            {"name": "7 jours", "remaining_percent": 18, "resets_at": 200_000},
        ],
    }

    balanced = balance_route(
        route, [codex, status("claude", 80, 130_000)], now=10_000
    )

    assert balanced.primary == "claude"


def test_consensus_and_unknown_usage_are_not_rebalanced():
    consensus = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")
    fast = Route(Intent.ANALYZE, Mode.FAST, "codex")

    assert balance_route(consensus, [], now=10_000) == consensus
    assert balance_route(fast, [], now=10_000) == fast


def test_reserved_provider_waits_for_its_own_reset():
    route = Route(Intent.MODIFY, Mode.FAST, "claude", None, "reserved")

    admitted, notice = admit_route(
        route,
        [status("claude", 2, 12_000), status("codex", 90, 200_000)],
        wait_for_provider=True,
        now=10_000,
    )

    assert admitted.primary == "claude"
    assert notice["blocked"] is True
    assert notice["retry_at"] == 12_000
    assert notice["reserved_provider"] == "claude"


def test_consensus_replaces_low_claude_with_gemini():
    route = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")
    gemini = {
        "provider": "gemini",
        "available": True,
        "windows": [],
    }

    admitted, notice = admit_route(
        route,
        [
            status("codex", 70, 200_000),
            status("claude", 10, 200_000),
            gemini,
        ],
        now=10_000,
    )

    assert admitted.mode is Mode.CONSENSUS
    assert admitted.primary == "codex"
    assert admitted.reviewer == "gemini"
    assert "claude->gemini" in notice["message"]


def test_consensus_keeps_healthy_claude_ahead_of_unknown_gemini():
    route = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")

    admitted, notice = admit_route(
        route,
        [
            status("codex", 91, 200_000),
            status("claude", 69, 200_000),
            {
                "provider": "gemini",
                "available": True,
                "windows": [],
            },
        ],
        now=10_000,
    )

    assert admitted.mode is Mode.CONSENSUS
    assert admitted.primary == "codex"
    assert admitted.reviewer == "claude"
    assert notice is None


def test_consensus_keeps_claude_when_its_measure_is_pending_refresh():
    route = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")

    admitted, notice = admit_route(
        route,
        [
            status("codex", 91, 200_000),
            {
                "provider": "claude",
                "available": False,
                "windows": [],
                "message": "Quota expiré · actualisation en attente",
            },
            {
                "provider": "gemini",
                "available": True,
                "windows": [],
            },
        ],
        now=10_000,
    )

    assert admitted.reviewer == "claude"
    assert notice is None


def test_consensus_replaces_claude_during_a_real_provider_cooldown():
    route = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")

    admitted, notice = admit_route(
        route,
        [
            status("codex", 91, 200_000),
            {
                "provider": "claude",
                "available": False,
                "availability_state": "quota_exhausted",
                "windows": [],
                "message": "Pause temporaire après quota",
            },
            {
                "provider": "gemini",
                "available": True,
                "windows": [],
            },
        ],
        now=10_000,
    )

    assert admitted.reviewer == "gemini"
    assert "claude->gemini" in notice["message"]


def test_consensus_becomes_fast_when_only_one_provider_has_capacity():
    route = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")
    unavailable_gemini = {
        "provider": "gemini",
        "available": False,
        "windows": [],
        "message": "quota épuisé",
    }

    admitted, notice = admit_route(
        route,
        [
            status("codex", 4, 200_000),
            status("claude", 4, 200_000),
            unavailable_gemini,
        ],
        now=10_000,
    )

    assert admitted == route
    assert notice["level"] == "error"
    assert notice["blocked"] is True
    assert notice["retry_at"] == 200_000
    assert "Aucun fournisseur" in notice["message"]


def test_next_quota_reset_ignores_healthy_and_expired_windows():
    assert _next_quota_reset(
        [
            status("codex", 2, 12_000),
            status("claude", 80, 11_000),
            status("gemini", 1, 9_000),
        ],
        threshold=8,
        now=10_000,
    ) == 12_000


def test_consensus_uses_one_affordable_provider_when_two_are_too_costly():
    route = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")

    admitted, notice = admit_route(
        route,
        [
            status("codex", 10, 200_000),
            status("claude", 4, 200_000),
            {
                "provider": "gemini",
                "available": False,
                "availability_state": "temporarily_unavailable",
                "windows": [],
            },
        ],
        now=10_000,
    )

    assert admitted.mode is Mode.FAST
    assert admitted.primary == "codex"
    assert "répond seul" in notice["message"]


def test_forced_consensus_can_attempt_despite_low_quota():
    route = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")

    admitted, notice = admit_route(
        route,
        [
            status("codex", 4, 200_000),
            status("claude", 4, 200_000),
        ],
        forced_mode=True,
        now=10_000,
    )

    assert admitted == route
    assert notice["forced"] is True
    assert "forcé" in notice["message"]


def test_forced_agent_is_kept_and_warned_when_its_quota_is_low():
    route = Route(Intent.ANALYZE, Mode.FAST, "claude")

    admitted, notice = admit_route(
        route,
        [
            status("claude", 2, 200_000),
            status("codex", 80, 200_000),
        ],
        forced_agent=True,
        now=10_000,
    )

    assert admitted.primary == "claude"
    assert notice["forced"] is True
    assert "forcé" in notice["message"]
