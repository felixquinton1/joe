from joe.autonomous_scale import compute_scale_policy, default_model_calls, scale_audit


def test_two_week_campaign_requires_ambitious_checkpointed_runs() -> None:
    policy = compute_scale_policy(14 * 24 * 3600, {"mode": "gpu_only", "gpu_index": 0})

    assert policy["horizon"] == "long"
    assert policy["target_substantive_run_seconds"] == 12 * 3600
    assert policy["minimum_substantive_run_seconds"] == 4 * 3600
    assert policy["minimum_substantive_runs"] == 2
    assert policy["gpu_expected"] is True


def test_scale_audit_uses_measured_duration_and_gpu_telemetry() -> None:
    campaign = {
        "max_duration_seconds": 14 * 24 * 3600,
        "resource_policy": {"mode": "gpu_only"},
        "history": [
            {"kind": "experiment", "duration_seconds": 600, "metrics": {
                "resources": {"gpu_util_mean_pct": 2},
            }},
            {"kind": "experiment", "duration_seconds": 5 * 3600, "metrics": {
                "resources": {"gpu_util_mean_pct": 70},
            }},
        ],
    }

    audit = scale_audit(campaign)

    assert audit["substantive_runs"] == 1
    assert audit["substantive_run_debt"] == 1
    assert audit["longest_run_seconds"] == 5 * 3600
    assert audit["measured_gpu_utilization_pct"] > 67


def test_long_horizon_caps_default_model_call_cadence() -> None:
    assert default_model_calls(50, "fast", 3600) == 51
    assert default_model_calls(50, "fast", 14 * 24 * 3600) == 34
