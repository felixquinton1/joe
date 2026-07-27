import json

from joe.usage import _gemini_status, record_gemini_usage


def test_gemini_usage_accumulates_headless_stats(tmp_path):
    path = tmp_path / "gemini_usage.json"
    raw = json.dumps(
        {
            "response": "OK",
            "stats": {
                "models": {
                    "gemini-flash": {
                        "api": {"totalRequests": 1},
                        "tokens": {"total": 120},
                    }
                }
            },
        }
    )

    record_gemini_usage(raw, path, now=1_800_000_000)
    record_gemini_usage(raw, path, now=1_800_000_001)

    payload = json.loads(path.read_text())
    day = next(iter(payload["days"].values()))
    assert day["tokens"] == 240
    assert day["requests"] == 2
    assert path.stat().st_mode & 0o777 == 0o600


def test_gemini_status_exposes_metrics_without_inventing_remaining_quota(
    tmp_path,
):
    path = tmp_path / "gemini_usage.json"
    record_gemini_usage(
        json.dumps(
            {
                "stats": {
                    "models": {
                        "gemini-pro": {
                            "api": {"totalRequests": 1},
                            "tokens": {"total": 50},
                        }
                    }
                }
            }
        ),
        path,
    )

    status = _gemini_status(path)

    assert status["available"] is True
    assert status["windows"] == []
    assert status["metrics"]
    assert "/stats model" in status["message"]
