from dataclasses import replace

from joe.models import Mode
from joe.provider_registry import arbitration_order, counterpart
from joe import provider_registry
from joe.router import Router


def test_manual_provider_policy_does_not_depend_on_a_specific_provider_id(monkeypatch):
    monkeypatch.setitem(provider_registry._BY_NAME, "claude",
                        replace(provider_registry.get_provider_spec("claude"), manual_only=True))
    router = Router(frozenset({"codex", "claude"}))
    automatic = router.route("Explique cette documentation", forced_mode=Mode.REVIEW)
    assert automatic.primary == "codex"
    assert automatic.reviewer is None
    assert automatic.mode is Mode.FAST
    explicit = router.route("Explique cette documentation", forced_agent="claude", forced_mode=Mode.REVIEW)
    assert explicit.primary == "claude"
    assert explicit.reviewer == "codex"
    assert counterpart("codex", ("codex", "claude")) is None
    assert "claude" not in arbitration_order(eligible=("codex", "claude"))
