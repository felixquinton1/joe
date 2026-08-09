import sys
from pathlib import Path

from joe.autonomous_resources import normalize_resource_policy
from joe import autonomous_preflight
from joe.experiment_runner import run_experiment


def test_resource_policy_defaults_and_sanitizes_optional_values():
    assert normalize_resource_policy(None) == {
        "mode": "auto", "gpu_index": None, "notes": "",
    }
    assert normalize_resource_policy({"mode": "gpu_only", "gpu_index": "2"})["gpu_index"] == 2
    assert normalize_resource_policy({"mode": "unknown"})["mode"] == "auto"


def test_preflight_enforces_requested_gpu_index(monkeypatch):
    monkeypatch.setattr(autonomous_preflight, "_nvidia_gpus", lambda: [
        {"index": 0, "name": "GPU", "vram_mb": 16000, "driver": "1"}
    ])

    result = autonomous_preflight.inspect_resources({"mode": "gpu_only", "gpu_index": 1})

    assert result["ok"] is False
    assert "GPU 1" in result["errors"][0]


def test_default_preflight_runs_as_project_subprocess(tmp_path: Path):
    result = run_experiment(
        [
            sys.executable, "-m", "joe.autonomous_preflight",
            "--output", "artifacts/preflight.json",
            "--policy-json", '{"mode":"cpu_only"}',
        ],
        tmp_path,
        tmp_path / "joe-results",
        metrics_path="artifacts/preflight.json",
    )

    assert result["status"] == "completed"
    assert result["metrics"]["ok"] is True
    assert result["metrics"]["policy"]["mode"] == "cpu_only"
