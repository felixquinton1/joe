"""Quota truth at the CLI/web boundary; no provider generation calls."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from joe import cli, usage, web_runs
from joe.models import Intent, Mode, Route
from joe.router import Router
from joe.usage_claude import claude_status
from joe.usage import _refresh_claude_status as real_refresh_claude_status


NOW = 2_000_000_000
RESET = "2033-05-19T07:20:00Z"  # NOW + 100000


def status(provider, remaining, *, stale=False):
    return {
        "provider": provider,
        "available": True,
        "stale": stale,
        "windows": [{"name": "5 hours", "remaining_percent": remaining,
                     "resets_at": NOW + 100_000}],
        "message": None,
    }


@pytest.fixture
def quota_environment(monkeypatch):
    monkeypatch.setattr(usage.time, "time", lambda: NOW)
    monkeypatch.setattr(usage.time, "monotonic", lambda: 10_000)
    monkeypatch.setattr(usage, "_cache", None)
    monkeypatch.setattr(usage, "_last_available", {})
    monkeypatch.setattr(usage.shutil, "which", lambda name: None)
    monkeypatch.setattr(usage, "_codex_status", lambda: status("codex", 80))
    monkeypatch.setattr(usage, "_claude_status", lambda: status("claude", 0, stale=True))
    monkeypatch.setattr(usage, "_refresh_claude_status", lambda: status("claude", 90))


def manager():
    # Keep the real decision pipeline; isolate stores and provider execution.
    return SimpleNamespace(
        _project_scope=lambda _: (None, [], False, "read_only"),
        _project_quota_provider=lambda _: None,
        conversations=SimpleNamespace(previous_provider=lambda _: None),
        orchestrator=SimpleNamespace(router=Router(frozenset({"claude", "codex"}))),
    )


def test_web_stale_quota_refresh_changes_the_actual_route(quota_environment):
    usage._cache = (10_000, [status("claude", 0, stale=True), status("codex", 80)])

    decision = web_runs.RunManager.decide(manager(), "Explain architecture", "conversation")

    assert decision.route.primary == "claude", "stale zero capacity must not switch the route"
    assert "quota-switch" not in decision.route.reason


def test_cli_stale_quota_refresh_changes_the_actual_route(quota_environment, capsys):
    orchestrator = SimpleNamespace(
        router=Router(frozenset({"claude", "codex"})),
        memory=SimpleNamespace(previous_provider=lambda: None),
    )
    assert cli._handle(orchestrator, "Explain architecture", None, None, True) == 0
    assert "· claude" in capsys.readouterr().err


def read_claude(tmp_path, utilization):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps({"cachedUsageUtilization": {
        "fetchedAtMs": NOW * 1000, "utilization": utilization,
    }}), encoding="utf-8")
    return claude_status(path, unavailable=usage._unavailable, now_timestamp=NOW,
                         stale_usage_seconds=usage._STALE_USAGE_SECONDS)


def limit(**changes):
    return {"kind": "daily_scoped", "group": "opaque_group", "percent": 98,
            "resets_at": RESET, "scope": {"surface": {"display_name": "Claude Code"}},
            "severity": "critical", "is_active": True, **changes}


def test_generic_active_limit_is_a_distinct_governing_observation(tmp_path, quota_environment):
    observed = read_claude(tmp_path, {"limits": [limit()]})

    assert len(observed["windows"]) == 1, "generic limit was discarded by legacy-only ingestion"
    window = observed["windows"][0]
    assert window["remaining_percent"] == 2
    assert window["resets_at"] == NOW + 100_000
    assert window["kind"] == "daily_scoped"
    assert window["group"] == "opaque_group"
    assert window["scope"] == {"surface": {"display_name": "Claude Code"}}
    assert window["duration_minutes"] is None  # Do not infer duration from the label.
    route = Route(Intent.ANALYZE, Mode.FAST, "claude")
    assert usage.balance_route(route, [observed, status("codex", 80)], now=NOW).primary == "codex"


def test_multiple_windows_do_not_overwrite_each_other(tmp_path, quota_environment):
    observed = read_claude(tmp_path, {
        "five_hour": {"utilization": 2, "resets_at": RESET},
        "seven_day": {"utilization": 29, "resets_at": RESET},
        "limits": [limit()],
    })
    assert [w["remaining_percent"] for w in observed["windows"]] == [98, 71, 2]
    assert [w["duration_minutes"] for w in observed["windows"]] == [300, 10080, None]


@pytest.mark.parametrize("row", [
    limit(is_active=None),
    limit(resets_at="2033-05-18T00:00:00Z"),
    limit(scope={"model": {"display_name": "Unmapped model"}}),
    limit(scope={"surface": {"display_name": "Unknown surface"}}),
    limit(scope=None, kind="opaque_kind"),
    limit(scope="opaque_scope"),
    limit(percent="unknown"),
    limit(percent=float("inf")),
    limit(resets_at="unknown"),
    limit(kind=["invalid meter kind"]),
])
def test_non_governing_rows_remain_evidence(tmp_path, quota_environment, row):
    observed = read_claude(tmp_path, {"limits": [row]})
    assert observed["windows"] == []
    assert observed["limit_observations"][0]["provider_limit"] == row
    assert observed["limit_observations"][0]["governing"] is False


def test_legacy_fields_and_non_headline_session_remain_supported(tmp_path, quota_environment):
    observed = read_claude(tmp_path, {
        "five_hour": {"utilization": 2, "resets_at": RESET},
        "seven_day": {"utilization": 29, "resets_at": RESET},
        "seven_day_sonnet": {"utilization": 40, "resets_at": RESET},
        "seven_day_opus": {"utilization": 50, "resets_at": RESET},
        "limits": [limit(kind="session", scope=None, is_active=False, percent=2)],
    })
    assert [w["remaining_percent"] for w in observed["windows"]] == [98, 71, 60, 50]


@pytest.mark.parametrize("message", [
    "Refresh unavailable; last local observation",
    "Last observation: quota épuisé; refresh unavailable",
    "Last observation: authentication failure; refresh unavailable",
])
def test_refresh_failure_is_unknown_to_admission_and_classifier(
    quota_environment, monkeypatch, message,
):
    failed = status("claude", 99, stale=True)
    failed["message"] = message
    usage._with_last_available(status("claude", 99))
    usage._cache = (10_000, [deepcopy(failed), status("codex", 80)])
    monkeypatch.setattr(usage, "_refresh_claude_status", lambda: deepcopy(failed))

    observed = next(s for s in usage.routing_usage_status() if s["provider"] == "claude")

    assert observed["available"] is None
    assert observed["stale"] is True
    assert observed["windows"] == []
    assert observed["last_observation"]["windows"][0]["remaining_percent"] == 99
    assert usage._provider_capacity(observed, 20, NOW)[:2] == (None, None)
    from joe.route_classifier import _remaining_percent
    assert _remaining_percent(observed) is None


def test_expired_window_triggers_refresh_even_with_a_healthy_week(quota_environment):
    old = status("claude", 90)
    old["windows"][0]["resets_at"] = NOW - 1
    old["windows"].append({"name": "week", "remaining_percent": 1,
                           "resets_at": NOW + 100_000})
    usage._cache = (10_000, [old, status("codex", 80)])
    observed = next(s for s in usage.routing_usage_status() if s["provider"] == "claude")
    assert observed["windows"][0]["remaining_percent"] == 90
    assert len(observed["windows"]) == 1


def test_cache_expiry_reprobes_codex_and_preserves_both_windows(quota_environment, monkeypatch):
    codex = usage.normalize_codex_usage({"rateLimits": {
        "primary": {"usedPercent": 1, "windowDurationMins": 300, "resetsAt": NOW + 10_000},
        "secondary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": NOW + 100_000},
    }})
    usage._cache = (9_000, [status("codex", 0), status("claude", 80)])
    monkeypatch.setattr(usage, "_codex_status", lambda: deepcopy(codex))
    monkeypatch.setattr(usage, "_claude_status", lambda: status("claude", 80))
    observed = next(s for s in usage.routing_usage_status() if s["provider"] == "codex")
    assert [w["remaining_percent"] for w in observed["windows"]] == [99, 100]
    assert [w["duration_minutes"] for w in observed["windows"]] == [300, 10080]


def test_web_classification_admission_and_tier_use_one_snapshot(quota_environment, monkeypatch):
    monkeypatch.delenv("JOE_DISABLE_LLM_ROUTER")
    manager_instance = manager()
    monkeypatch.setattr(web_runs, "Orchestrator", lambda *args, **kwargs: SimpleNamespace(
        router=manager_instance.orchestrator.router, providers={},
    ))
    from joe.route_classifier import RouteClassification
    classification = RouteClassification("analyze", "none", "simple", "fast", "claude",
                                         "strong", "high", 0.9)
    seen = []

    def classify(request, baseline, providers, statuses, workspace, **kwargs):
        seen.append(statuses)
        # Simulate a concurrent quota panel update while classification runs.
        usage._cache = (10_000, [status("claude", 0), status("codex", 80)])
        return classification

    monkeypatch.setattr(web_runs, "classify_route_request", classify)
    monkeypatch.setattr(web_runs, "select_model_tier", lambda provider, tier: tier)
    decision = web_runs.RunManager.decide(manager_instance, "Explain architecture", "conversation")
    assert decision.route.primary == "claude"
    assert decision.model == "strong"
    assert seen[0][1]["windows"][0]["remaining_percent"] == 90


def test_claude_age_crosses_stale_boundary_inside_memory_cache(quota_environment, monkeypatch):
    old = status("claude", 0)
    old["observed_at"] = NOW - 901
    usage._cache = (10_000, [old, status("codex", 80)])
    observed = next(s for s in usage.routing_usage_status() if s["provider"] == "claude")
    assert observed["windows"][0]["remaining_percent"] == 90


def test_failed_codex_read_does_not_recycle_old_capacity(quota_environment, monkeypatch):
    usage._with_last_available(status("codex", 99))
    monkeypatch.setattr(usage, "_codex_status", lambda: usage._unavailable("codex", "Read failed"))
    observed = next(s for s in usage.routing_usage_status() if s["provider"] == "codex")
    assert observed["available"] is None
    assert observed["windows"] == []
    assert usage._provider_capacity(observed, 20, NOW)[:2] == (None, None)


def test_successful_claude_refresh_retains_structured_rows(tmp_path, quota_environment, monkeypatch):
    local = read_claude(tmp_path, {
        "five_hour": {"utilization": 2, "resets_at": RESET},
        "seven_day": {"utilization": 29, "resets_at": RESET},
        "limits": [limit(), limit(kind="weekly_scoped", scope={
            "model": {"display_name": "Unknown model"},
        })],
    })
    monkeypatch.setattr(usage, "_claude_status", lambda: deepcopy(local))
    monkeypatch.setattr(usage, "_local_claude_plan", lambda: "pro")
    monkeypatch.setattr(usage, "_refresh_claude_status_impl", lambda *args, **kwargs: {
        "provider": "claude", "available": True, "stale": False,
        "windows": [{"name": "5 hours", "remaining_percent": 98, "resets_at": NOW + 100_000,
                     "duration_minutes": 300},
                    {"name": "7 days", "remaining_percent": 71, "resets_at": NOW + 100_000,
                     "duration_minutes": 10080}],
        "message": "Refreshed via Claude /usage",
    })
    observed = real_refresh_claude_status()
    assert [w["remaining_percent"] for w in observed["windows"]] == [98, 71, 2]
    assert len(observed["limit_observations"]) == 2
    assert observed["limit_observations"][1]["governing"] is False


def test_cache_reader_marks_removed_legacy_window_for_refresh(tmp_path, quota_environment):
    observed = read_claude(tmp_path, {
        "five_hour": {"utilization": 100, "resets_at": "2033-05-18T00:00:00Z"},
        "seven_day": {"utilization": 29, "resets_at": RESET},
    })
    assert observed["refresh_required"] is True
    assert [w["remaining_percent"] for w in observed["windows"]] == [71]


def test_structured_window_keeps_its_own_age_after_text_refresh(quota_environment):
    combined = status("claude", 0)
    combined["observed_at"] = NOW
    combined["windows"][0]["observed_at"] = NOW - 901
    usage._cache = (10_000, [combined, status("codex", 80)])
    observed = next(s for s in usage.routing_usage_status() if s["provider"] == "claude")
    assert observed["windows"][0]["remaining_percent"] == 90


def test_successful_text_refresh_cannot_launder_stale_structured_limits(
    tmp_path, quota_environment, monkeypatch,
):
    local = read_claude(tmp_path, {"limits": [limit()]})
    local["stale"] = True
    monkeypatch.setattr(usage, "_claude_status", lambda: deepcopy(local))
    monkeypatch.setattr(usage, "_local_claude_plan", lambda: "pro")
    monkeypatch.setattr(usage, "_refresh_claude_status_impl", lambda *args, **kwargs: status("claude", 90))
    observed = real_refresh_claude_status()
    assert observed["stale"] is True
    assert observed["limit_observations"][0]["governing"] is False


def test_successful_refresh_keeps_additional_unscoped_weekly_observation(
    tmp_path, quota_environment, monkeypatch,
):
    local = read_claude(tmp_path, {"limits": [limit(kind="weekly_all", scope=None)]})
    monkeypatch.setattr(usage, "_claude_status", lambda: deepcopy(local))
    monkeypatch.setattr(usage, "_local_claude_plan", lambda: "pro")
    monkeypatch.setattr(usage, "_refresh_claude_status_impl", lambda *args, **kwargs: status("claude", 90))
    observed = real_refresh_claude_status()
    assert [w["remaining_percent"] for w in observed["windows"]] == [90, 2]


@pytest.mark.parametrize("scope", [None, {}, {"model": None, "surface": None}])
def test_generic_only_non_headline_session_is_still_a_current_limit(
    tmp_path, quota_environment, scope,
):
    observed = read_claude(tmp_path, {"limits": [
        limit(kind="session", scope=scope, is_active=False, percent=2),
        limit(kind="weekly_all", scope=scope, percent=29),
    ]})
    assert [w["remaining_percent"] for w in observed["windows"]] == [98, 71]
    assert observed["windows"][0]["is_active"] is False
    assert observed["windows"][0]["duration_minutes"] == 300


def test_refresh_prefers_new_structured_resets_without_duplicate_windows(
    tmp_path, quota_environment, monkeypatch,
):
    exact_reset = "2033-05-19T07:20:00.500Z"
    local = read_claude(tmp_path, {
        "five_hour": {"utilization": 2, "resets_at": exact_reset},
        "seven_day": {"utilization": 29, "resets_at": exact_reset},
        "limits": [limit(kind="session", scope=None, is_active=False, percent=2,
                         resets_at=exact_reset),
                   limit(kind="weekly_all", scope=None, percent=29, resets_at=exact_reset)],
    })
    monkeypatch.setattr(usage, "_claude_status", lambda: deepcopy(local))
    monkeypatch.setattr(usage, "_local_claude_plan", lambda: "pro")
    monkeypatch.setattr(usage, "_refresh_claude_status_impl", lambda *args, **kwargs: {
        "provider": "claude", "available": True, "stale": False,
        "windows": [{"name": "5 hours", "remaining_percent": 98, "resets_at": NOW + 100_000,
                     "duration_minutes": 300},
                    {"name": "7 days", "remaining_percent": 71, "resets_at": NOW + 100_000,
                     "duration_minutes": 10080}],
    })
    observed = real_refresh_claude_status()
    assert len(observed["windows"]) == 2
    assert [w["resets_at"] for w in observed["windows"]] == [NOW + 100_000.5, NOW + 100_000.5]
    assert [w["observed_at"] for w in observed["windows"]] == [NOW, NOW]
