import time

import pytest

from joe.models import Intent, Mode, ProviderResult, Route
from joe.provider_health import apply_cooldowns, recent_failure, record_result
from joe.usage import admit_route


def failed(kind, status, retry=None):
    return ProviderResult(
        "deepseek", [], "", "", 1, 0,
        error_kind=kind, http_status=status,
        error_message=f"HTTP {status}: DeepSeek fixture failure",
        retry_after_seconds=retry,
    )


@pytest.mark.parametrize("kind,status", [
    ("authentication", 401), ("insufficient_balance", 402),
    ("rate_limit", 429), ("unavailable", 503),
])
def test_deepseek_failure_is_visible_without_changing_codex_quota(kind, status):
    record_result(failed(kind, status))
    codex = {"provider": "codex", "available": True,
             "windows": [{"remaining_percent": 80, "resets_at": 1000}]}
    result, ordinary = apply_cooldowns([
        {"provider": "deepseek", "available": True, "windows": []}, codex,
    ])
    assert ordinary == codex
    assert not result["available"]
    assert result["http_status"] == status
    assert str(status) in result["message"]
    assert recent_failure("codex") is None


def test_insufficient_balance_stays_stopped_without_a_fictitious_reset(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("joe.provider_health.time.monotonic", lambda: now[0])
    record_result(failed("insufficient_balance", 402, retry=60))
    now[0] += 365 * 86400
    result = apply_cooldowns([{"provider": "deepseek", "available": True, "windows": []}])[0]
    assert result["availability_state"] == "balance_exhausted"
    assert result.get("cooldown_seconds") is None
    assert result.get("retry_at") is None
    assert result["windows"] == []
    assert recent_failure("deepseek") == ("insufficient_balance", float("inf"))
    record_result(ProviderResult("deepseek", [], "OK", "", 0, 0))
    assert recent_failure("deepseek") is None


@pytest.mark.parametrize("retry,expected", [(None, None), (15, 15)])
def test_rate_limit_only_waits_until_an_explicit_retry_time(monkeypatch, retry, expected):
    monkeypatch.setattr("joe.provider_health.time.monotonic", lambda: 100.0)
    monkeypatch.setattr("joe.provider_health.time.time", lambda: 1000.0)
    record_result(failed("rate_limit", 429, retry))
    item = apply_cooldowns([{"provider": "deepseek", "available": True, "windows": []}])[0]
    assert not item["available"]
    assert item.get("cooldown_seconds") == expected
    assert item.get("retry_at") == (1015 if retry else None)
    assert recent_failure("deepseek")[1] == (expected if retry else float("inf"))


def test_reserved_deepseek_balance_failure_never_schedules_automatic_resume():
    record_result(failed("insufficient_balance", 402))
    statuses = apply_cooldowns([{"provider": "deepseek", "available": True, "windows": []}])
    route = Route(Intent.ANSWER, Mode.FAST, "deepseek")
    selected, notice = admit_route(route, statuses, forced_agent=True, wait_for_provider=True)
    assert selected == route
    assert notice["blocked"]
    assert notice["automatic_retry"] is False
    assert notice["retry_at"] is None


def test_reserved_rate_limit_uses_only_the_reported_retry_time(monkeypatch):
    monkeypatch.setattr("joe.provider_health.time.monotonic", lambda: 100.0)
    monkeypatch.setattr("joe.provider_health.time.time", lambda: 1000.0)
    record_result(failed("rate_limit", 429, 15))
    statuses = apply_cooldowns([{"provider": "deepseek", "available": True, "windows": []}])
    _, notice = admit_route(Route(Intent.ANSWER, Mode.FAST, "deepseek"), statuses,
                            forced_agent=True, wait_for_provider=True, now=1000)
    assert notice["retry_at"] == 1015
    assert notice["automatic_retry"] is True


def test_reserved_unknown_balance_failure_stops_web_run_instead_of_waiting(tmp_path, monkeypatch):
    from joe.web_runs import RunManager

    record_result(failed("insufficient_balance", 402))
    statuses = apply_cooldowns([{"provider": "deepseek", "available": True, "windows": []}])
    monkeypatch.setattr("joe.web_runs.routing_usage_status", lambda: statuses)
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    manager.conversations.update_project(conversation["project_id"], {"quota_provider": "deepseek"})
    run = manager.start("Reply hello", conversation["id"], None, None, None, None, None)
    with run.condition:
        deadline = time.monotonic() + 5
        while not run.done and time.monotonic() < deadline:
            run.condition.wait(.05)
    assert run.done
    assert any(event["type"] == "error" for event in run.events)
    assert not any(event["type"] == "quota_scheduled" for event in run.events)


def test_usage_cache_applies_new_health_without_probing_clis(monkeypatch):
    from joe import usage

    monkeypatch.setattr(usage, "_cache", (time.monotonic(), [
        {"provider": "codex", "available": True, "windows": []},
        {"provider": "deepseek", "available": True, "windows": []},
    ]))
    record_result(failed("insufficient_balance", 402))
    statuses = usage.usage_status()
    assert statuses[0]["available"]
    assert not statuses[1]["available"]


def test_doctor_reports_deepseek_reason_without_a_live_probe(tmp_path, monkeypatch):
    from joe.doctor import doctor_report, format_doctor

    record_result(failed("insufficient_balance", 402))
    monkeypatch.setattr("joe.doctor.resolve_executable", lambda *args, **kwargs: None)
    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr("joe.doctor.usage_status", lambda **kwargs: [])
    report = doctor_report(tmp_path, providers={"deepseek": object()})
    assert report["providers"][0]["last_failure"]["http_status"] == 402
    assert "HTTP 402" in format_doctor(report)
