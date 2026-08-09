from joe.autonomous_analysis import analyze_campaign, iteration_report
from joe.web_runs import _normalize_token_budget


def test_experiment_tree_excludes_interrupted_metric_from_best() -> None:
    campaign = {
        "metric_name": "loss",
        "metric_direction": "min",
        "history": [
            {
                "kind": "experiment", "id": "a", "iteration": 1,
                "status": "completed", "metrics": {
                    "primary_metric": {"name": "loss", "value": 0.5, "direction": "min"},
                    "validation": {
                        "strategy": "group_kfold", "folds": 3,
                        "leakage_controls": ["subject"], "split_fingerprint": "split-a",
                        "data_fingerprint": "data-a",
                    },
                },
            },
            {
                "kind": "experiment", "id": "b", "parent_experiment_id": "a",
                "iteration": 2, "status": "interrupted", "metrics": {
                    "primary_metric": {"name": "loss", "value": 0.1, "direction": "min"},
                    "validation": {
                        "strategy": "group_kfold", "folds": 3,
                        "leakage_controls": ["subject"], "split_fingerprint": "split-a",
                        "data_fingerprint": "data-a",
                    },
                },
                "checkpoint_available": True,
            },
        ],
    }

    analysis = analyze_campaign(campaign)

    assert analysis["summary"]["best_metric"] == 0.5
    assert analysis["summary"]["partial"] == 1
    assert analysis["experiments"][1]["parent_id"] == "a"
    assert analysis["experiments"][1]["quality"] == "partial"


def test_usage_calculates_known_tokens_and_marks_missing_telemetry() -> None:
    campaign = {
        "history": [{
            "kind": "agent_step", "status": "completed",
            "attempts": [
                {"provider": "codex", "usage": {
                    "input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 5,
                }},
                {"provider": "claude"},
            ],
        }],
        "token_budget": {"max_tokens": 1000, "max_model_calls": 4},
    }

    analysis = analyze_campaign(campaign)

    assert analysis["usage"] == {
        "prompts": 1, "model_calls": 2, "known_tokens": 125,
        "unknown_usage_calls": 1,
    }
    assert analysis["token_budget"]["tokens_remaining"] == 875
    assert analysis["token_budget"]["enforcement"] == "partial_provider_telemetry"


def test_model_call_budget_is_automatically_sized_by_workflow() -> None:
    assert _normalize_token_budget({}, 3, "fast")["max_model_calls"] == 4
    assert _normalize_token_budget({}, 3, "review")["max_model_calls"] == 8
    assert _normalize_token_budget({}, 3, "consensus")["max_model_calls"] == 12


def test_incompatible_split_is_visible_but_excluded_from_comparison() -> None:
    def metrics(value, split):
        return {
            "primary_metric": {"name": "loss", "value": value, "direction": "min"},
            "validation": {
                "strategy": "group_kfold", "folds": 3,
                "leakage_controls": ["subject"], "split_fingerprint": split,
            },
        }

    analysis = analyze_campaign({
        "metric_name": "loss", "metric_direction": "min",
        "history": [
            {"kind": "experiment", "status": "completed", "metrics": metrics(.5, "a")},
            {"kind": "experiment", "status": "completed", "metrics": metrics(.1, "b")},
        ],
    })

    assert analysis["summary"]["best_metric"] == .5
    assert analysis["summary"]["comparable"] == 1
    assert analysis["experiments"][1]["validation_status"] == "incompatible"
    assert analysis["compute"]["experiments"] == 2


def test_iteration_report_is_concise_and_hides_raw_telemetry() -> None:
    result = {
        "status": "completed", "duration_seconds": 8.237,
        "metrics": {
            "schema_version": 1,
            "experiment": {
                "variant": "regional-logreg",
                "hypothesis": "Les agrégats régionaux amélioreront la généralisation.",
            },
            "primary_metric": {"name": "log_loss", "value": 0.569634, "direction": "min"},
            "secondary_metrics": {"accuracy": 0.74, "balanced_accuracy": 0.71, "auc": 0.8},
            "resources": {"gpu_peak_mb": 1220, "cpu_peak_percent": 80},
            "validation": {"strategy": "group_kfold", "folds": 3, "split_fingerprint": "same", "leakage_controls": ["subject"]},
            "reproducibility": {"python": "3.12", "platform": "very long telemetry"},
        },
    }
    campaign = {
        "iteration": 1, "metric_name": "log_loss", "metric_direction": "min",
        "history": [{"kind": "experiment", **result}],
    }
    report = iteration_report(campaign, result, analyze_campaign(campaign))

    assert "**Expérience terminée** · 8.2 s · `regional-logreg`" in report
    assert "| **log_loss** | **0.569634** |" in report
    assert "| Validation | comparable · group_kfold · 3 folds |" in report
    assert "accuracy" in report and "balanced_accuracy" in report
    assert "auc" not in report  # Two secondary indicators at most.
    assert "schema_version" not in report
    assert "reproducibility" not in report
    assert "gpu_peak_mb" not in report
