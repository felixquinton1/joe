from joe.usage import normalize_codex_usage


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
