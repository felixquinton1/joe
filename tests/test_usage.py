import json
from datetime import datetime

from joe.usage import (
    _claude_status,
    _gemini_status,
    _parse_claude_usage_screen,
    cached_usage_status,
    normalize_codex_usage,
)


def test_normalize_codex_usage_windows():
    status = normalize_codex_usage(
        {
            "rateLimits": {
                "planType": "plus",
                "primary": {
                    "usedPercent": 25.5,
                    "resetsAt": 1_800_000_000,
                    "windowDurationMins": 300,
                },
                "secondary": {
                    "usedPercent": 80,
                    "resetsAt": 1_800_100_000,
                    "windowDurationMins": 10080,
                },
            }
        }
    )

    assert status["available"] is True
    assert status["plan"] == "plus"
    assert status["windows"][0]["remaining_percent"] == 74.5
    assert status["windows"][1]["remaining_percent"] == 20


def test_cached_usage_never_probes_providers(monkeypatch):
    monkeypatch.setattr("joe.usage._cache", None)
    monkeypatch.setattr(
        "joe.usage._codex_status",
        lambda: (_ for _ in ()).throw(AssertionError("must not probe")),
    )

    assert cached_usage_status() == []


def test_normalize_codex_usage_clamps_percentages():
    status = normalize_codex_usage(
        {"rateLimits": {"primary": {"usedPercent": 110}}}
    )

    assert status["windows"][0]["used_percent"] == 100
    assert status["windows"][0]["remaining_percent"] == 0


def test_claude_status_reads_fresh_local_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("joe.usage.time.time", lambda: 2_000)
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "cachedUsageUtilization": {
                    "fetchedAtMs": 1_900_000,
                    "utilization": {
                        "five_hour": {
                            "utilization": 30,
                            "resets_at": "2030-01-01T12:00:00Z",
                        },
                        "seven_day": {
                            "utilization": 75,
                            "resets_at": "2030-01-02T12:00:00+00:00",
                        },
                        "seven_day_opus": {
                            "utilization": 90,
                            "resets_at": "2030-01-03T12:00:00Z",
                        },
                    },
                }
            }
        )
    )

    status = _claude_status(path)

    assert status["available"] is True
    assert status["windows"][0]["remaining_percent"] == 70
    assert status["windows"][1]["remaining_percent"] == 25
    assert status["windows"][2]["name"] == "Opus · 7 jours"
    assert status["windows"][2]["remaining_percent"] == 10


def test_claude_status_keeps_recent_stale_cache_for_routing(tmp_path, monkeypatch):
    monkeypatch.setattr("joe.usage.time.time", lambda: 3_000)
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "cachedUsageUtilization": {
                    "fetchedAtMs": 1_000_000,
                    "utilization": {
                        "five_hour": {
                            "utilization": 40,
                            "resets_at": "2030-01-01T12:00:00Z",
                        }
                    },
                }
            }
        )
    )

    status = _claude_status(path)

    assert status["available"] is True
    assert status["stale"] is True
    assert status["windows"][0]["remaining_percent"] == 60


def test_claude_status_rejects_cache_older_than_six_hours(tmp_path, monkeypatch):
    monkeypatch.setattr("joe.usage.time.time", lambda: 30_000)
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "cachedUsageUtilization": {
                    "fetchedAtMs": 1_000_000,
                    "utilization": {
                        "five_hour": {
                            "utilization": 40,
                            "resets_at": "2030-01-01T12:00:00Z",
                        }
                    },
                }
            }
        )
    )

    status = _claude_status(path)

    assert status["available"] is False
    assert "trop anciennes" in status["message"]


def test_parse_claude_usage_screen_returns_live_windows():
    status = _parse_claude_usage_screen(
        """
        Current session
        13% used
        Resets 2:19pm (Europe/Paris)
        Current week (all models)
        15% used
        Resets Aug 2, 2:59pm (Europe/Paris)
        """,
        now=datetime.fromisoformat("2026-07-28T12:00:00+02:00"),
    )

    assert status is not None
    assert status["stale"] is False
    assert status["windows"][0]["remaining_percent"] == 87
    assert status["windows"][1]["remaining_percent"] == 85
    assert all(window["resets_at"] for window in status["windows"])


def test_parse_current_claude_usage_format_with_duplicate_percentages():
    status = _parse_claude_usage_screen(
        """
        Current session
        66% 66% used
        Resets 2:20pm (Europe/Paris)
        Current week (all models)
        21% 21% used
        Resets Aug 2, 3pm (Europe/Paris)
        """,
        now=datetime.fromisoformat("2026-07-28T12:00:00+02:00"),
    )

    assert status is not None
    assert status["windows"][0]["remaining_percent"] == 34
    assert status["windows"][1]["remaining_percent"] == 79
    assert all(window["resets_at"] for window in status["windows"])


def test_gemini_status_explains_api_key_quota(tmp_path):
    usage_path = tmp_path / "usage.json"
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {"security": {"auth": {"selectedType": "gemini-api-key"}}}
        )
    )

    status = _gemini_status(usage_path, settings_path)

    assert status["metrics"][0]["value"] == "Clé API · variable par modèle/offre"
    assert "réserve globale de tokens" in status["message"]
