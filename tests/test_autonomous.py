import json
import sys
from pathlib import Path

import pytest

from joe.autonomous import AutonomousStore
from joe.experiment_runner import run_experiment


def campaign_values():
    return {
        "title": "Research",
        "project_id": "p1",
        "conversation_id": "c1",
        "objective": "Improve a synthetic baseline",
        "command": [sys.executable, "experiment.py"],
        "max_iterations": 3,
    }


def test_autonomous_store_is_durable(tmp_path: Path):
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**campaign_values())
    store.add_event(campaign["id"], "experiment", {"status": "completed"})
    restored = AutonomousStore(tmp_path).get(campaign["id"])
    assert restored["status"] == "scheduled"
    assert restored["history"][0]["kind"] == "experiment"


def test_autonomous_store_bounds_iterations(tmp_path: Path):
    values = campaign_values()
    values["max_iterations"] = 999
    store = AutonomousStore(tmp_path)
    store.ensure()
    assert store.create(**values)["max_iterations"] == 50


def test_autonomous_store_persists_time_and_data_boundaries(tmp_path: Path):
    values = campaign_values()
    values.update({"max_duration_seconds": 3600, "restricted_data": True})
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**values)
    assert campaign["max_duration_seconds"] == 3600
    assert campaign["restricted_data"] is True
    assert campaign["deadline_at"] is None


def test_runner_collects_metrics_and_logs(tmp_path: Path):
    script = tmp_path / "experiment.py"
    script.write_text(
        "import json\nfrom pathlib import Path\n"
        "Path('metrics.json').write_text(json.dumps({'score': .7}), encoding='utf-8')\n"
        "print('done')\n",
        encoding="utf-8",
    )
    result = run_experiment(
        [sys.executable, "experiment.py"], tmp_path, tmp_path / "artifacts",
    )
    assert result["status"] == "completed"
    assert result["metrics"] == {"score": .7}
    assert "done" in result["stdout_tail"]


def test_runner_detects_crash(tmp_path: Path):
    script = tmp_path / "experiment.py"
    script.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    result = run_experiment(
        [sys.executable, "experiment.py"], tmp_path, tmp_path / "artifacts",
    )
    assert result["status"] == "crashed"
    assert result["exit_code"] != 0


def test_runner_rejects_working_directory_escape(tmp_path: Path):
    with pytest.raises(ValueError):
        run_experiment([sys.executable, "-V"], tmp_path, tmp_path / "out", working_directory="..")
