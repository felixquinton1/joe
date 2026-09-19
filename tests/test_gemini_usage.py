import json
import threading

from joe.usage import _gemini_status, record_gemini_usage
from conftest import assert_mode


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
    assert_mode(path, 0o600)


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


def test_gemini_usage_understands_stream_result_stats(tmp_path):
    path = tmp_path / "gemini_usage.json"
    raw = json.dumps(
        {
            "type": "result",
            "stats": {
                "models": {
                    "gemini-flash": {
                        "total_tokens": 321,
                        "input_tokens": 300,
                    }
                }
            },
        }
    )

    record_gemini_usage(raw, path)

    day = next(iter(json.loads(path.read_text())["days"].values()))
    assert day["tokens"] == 321
    assert day["requests"] == 1


def test_gemini_usage_concurrent_updates_are_not_lost(tmp_path):
    path = tmp_path / "gemini_usage.json"
    raw = json.dumps(
        {
            "stats": {
                "models": {
                    "gemini-flash": {
                        "api": {"totalRequests": 1},
                        "tokens": {"total": 10},
                    }
                }
            }
        }
    )
    threads = [
        threading.Thread(
            target=record_gemini_usage,
            args=(raw, path),
        )
        for _ in range(8)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    day = next(iter(json.loads(path.read_text())["days"].values()))
    assert day["tokens"] == 80
    assert day["requests"] == 8
