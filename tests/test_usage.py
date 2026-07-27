import json

from joe.usage import _claude_status, normalize_codex_usage


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
                    },
                }
            }
        )
    )

    status = _claude_status(path)

    assert status["available"] is True
    assert status["windows"][0]["remaining_percent"] == 70
    assert status["windows"][1]["remaining_percent"] == 25


def test_claude_status_rejects_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("joe.usage.time.time", lambda: 3_000)
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "cachedUsageUtilization": {
                    "fetchedAtMs": 1_000_000,
                    "utilization": {},
                }
            }
        )
    )

    status = _claude_status(path)

    assert status["available"] is False
    assert "périmées" in status["message"]
