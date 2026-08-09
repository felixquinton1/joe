import json
from pathlib import Path

import pytest

from joe.autonomous_sdk import (
    ExperimentOutcome,
    ExperimentSpec,
    ResourceRequest,
    ValidationSpec,
    run_experiment,
)
from joe.web_runs import _autonomous_metric_value


def _spec() -> ExperimentSpec:
    return ExperimentSpec(
        hypothesis="A compact model improves the grouped validation score.",
        primary_metric="mae",
        direction="min",
        budget_seconds=60,
        validation=ValidationSpec(
            strategy="group_kfold", folds=3, seed=42,
            leakage_controls=("subject_id",),
        ),
        resources=ResourceRequest(cpu_threads=4, accelerator="cuda", vram_mb=16000),
    )


def test_sdk_publishes_versioned_atomic_result(tmp_path: Path) -> None:
    result = run_experiment(
        _spec(),
        lambda context: ExperimentOutcome(
            primary_value=0.42,
            secondary_metrics={"rmse": 0.71},
            resources={"gpu_peak_vram_mb": 2048},
            artifacts=["artifacts/predictions.csv"],
        ),
        workspace=tmp_path,
    )

    persisted = json.loads((tmp_path / "artifacts" / "metrics.json").read_text(encoding="utf-8"))
    assert result == persisted
    assert result["schema_version"] == 1
    assert result["validation"]["strategy"] == "group_kfold"
    assert result["resources"]["accelerator"] == "cuda"
    assert result["resources"]["gpu_peak_vram_mb"] == 2048
    assert _autonomous_metric_value(result, "mae") == 0.42
    assert not (tmp_path / "artifacts" / "metrics.json.tmp").exists()


def test_sdk_converts_experiment_exception_to_structured_failure(tmp_path: Path) -> None:
    def fail(_context):
        raise RuntimeError("training failed")

    result = run_experiment(_spec(), fail, workspace=tmp_path)

    assert result["status"] == "crashed"
    assert result["primary_metric"]["value"] is None
    assert result["error"] == {"type": "RuntimeError", "message": "training failed"}


def test_sdk_rejects_invalid_spec_and_checkpoint_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ExperimentSpec(
            hypothesis="", primary_metric="mae", direction="min",
            budget_seconds=1, validation=ValidationSpec(strategy="holdout"),
        )

    def escape(context):
        context.checkpoint_path("../outside.bin")
        return ExperimentOutcome(primary_value=1.0)

    result = run_experiment(_spec(), escape, workspace=tmp_path)
    assert result["status"] == "crashed"
    assert result["error"]["type"] == "ValueError"
