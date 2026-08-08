import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from joe.autonomous import AutonomousStore, build_autonomous_skill
from joe.autonomous_schedule import normalize_schedule, schedule_state
from joe.experiment_runner import run_experiment
from joe.web_runs import RunManager


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


def test_terminal_campaign_can_be_deleted_with_its_private_skill(tmp_path: Path):
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**campaign_values())
    skill = tmp_path / campaign["skill_path"]
    store.cancel(campaign["id"])

    assert store.delete(campaign["id"]) is True
    assert store.get(campaign["id"]) is None
    assert not skill.exists()


def test_autonomous_store_bounds_iterations(tmp_path: Path):
    values = campaign_values()
    values["max_iterations"] = 999
    store = AutonomousStore(tmp_path)
    store.ensure()
    assert store.create(**values)["max_iterations"] == 50


def test_completed_campaign_can_resume_without_losing_history(tmp_path: Path):
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**campaign_values())
    store.add_event(campaign["id"], "experiment", {"status": "completed"})
    store.update(
        campaign["id"], status="completed", phase="done", iteration=3,
        active_elapsed_seconds=3600, error="old error",
    )

    resumed = store.resume(campaign["id"])

    assert resumed["status"] == "scheduled"
    assert resumed["phase"] == "planning"
    assert resumed["iteration"] == 3
    assert resumed["max_iterations"] == 6
    assert resumed["active_elapsed_seconds"] == 0
    assert resumed["history"][0]["kind"] == "experiment"


def test_autonomous_store_accepts_consensus_and_rejects_unknown_mode(tmp_path: Path):
    store = AutonomousStore(tmp_path)
    store.ensure()
    assert store.create(**(campaign_values() | {"mode": "consensus"}))["mode"] == "consensus"
    assert store.create(**(campaign_values() | {"mode": "mystery"}))["mode"] == "review"


def test_autonomous_store_persists_time_and_data_boundaries(tmp_path: Path):
    values = campaign_values()
    values.update({"max_duration_seconds": 3600, "restricted_data": True})
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**values)
    assert campaign["max_duration_seconds"] == 3600
    assert campaign["restricted_data"] is True
    assert campaign["deadline_at"] is None


def test_autonomous_store_persists_durable_context_and_research_cadence(tmp_path: Path):
    values = campaign_values() | {
        "campaign_context": "Official URLs and local environment contract",
        "research_refresh_interval": 3,
    }
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**values)
    assert campaign["campaign_context"].startswith("Official URLs")
    assert campaign["research_refresh_interval"] == 3
    assert campaign["autonomous_skill"].startswith("# Skill — Autonomous")
    assert (tmp_path / campaign["skill_path"]).is_file()


def test_autonomous_skill_keeps_objective_and_invariants_in_every_prompt():
    campaign = campaign_values() | {
        "campaign_context": "Official rules remain binding",
        "research_protocol": "Check public sources",
        "data_policy": "Never expose local rows",
    }
    skill = build_autonomous_skill(campaign)
    research = RunManager._autonomous_research_prompt(campaign)
    iteration = RunManager._autonomous_iteration_prompt(
        campaign | {"history": [], "max_iterations": 3}, 1
    )

    assert "Improve a synthetic baseline" in skill
    assert "Never replace, weaken" in skill
    assert "best rigorously validated primary metric" in skill
    assert "Minimize model calls" in skill
    assert "accelerated approach" in skill
    assert "substantive runs" in skill
    assert "results plateau" in skill
    assert skill in research
    assert skill in iteration
    assert "lot cohérent" in iteration
    assert "économie de prompts" in iteration


def test_autonomous_scheduler_hides_internal_skill_prompt_from_chat(tmp_path, monkeypatch):
    source = Path(__file__).parents[1] / "src" / "joe" / "web_runs.py"
    implementation = source.read_text(encoding="utf-8")

    assert "record_user_message=False" in implementation
    assert "if not resumed and record_user_message:" in implementation


def test_scheduled_campaign_requires_checkpoint_contract(tmp_path: Path):
    values = campaign_values()
    values["schedule"] = {
        "timezone": "Europe/Paris",
        "windows": [{"days": [0], "start": "08:00", "end": "18:00"}],
    }
    store = AutonomousStore(tmp_path)
    store.ensure()
    with pytest.raises(ValueError, match="checkpoint"):
        store.create(**values)


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


def test_schedule_supports_daily_and_overnight_windows():
    schedule = normalize_schedule({
        "timezone": "Europe/Paris",
        "windows": [{"days": list(range(7)), "start": "22:00", "end": "06:00"}],
    })
    zone = ZoneInfo("Europe/Paris")
    active = datetime(2026, 8, 10, 23, 0, tzinfo=zone).timestamp()
    inactive = datetime(2026, 8, 10, 12, 0, tzinfo=zone).timestamp()
    assert schedule_state(schedule, active)["active"] is True
    waiting = schedule_state(schedule, inactive)
    assert waiting["active"] is False
    assert waiting["next_start"] == datetime(2026, 8, 10, 22, 0, tzinfo=zone).timestamp()


def test_runner_interrupts_after_checkpoint_signal(tmp_path: Path):
    script = tmp_path / "experiment.py"
    script.write_text(
        "import pathlib,time\n"
        "signal=pathlib.Path('artifacts/STOP_REQUESTED')\n"
        "while not signal.exists(): time.sleep(.02)\n"
        "pathlib.Path('checkpoints').mkdir(exist_ok=True)\n"
        "pathlib.Path('checkpoints/latest.pt').write_text('checkpoint')\n",
        encoding="utf-8",
    )
    cancel = threading.Event()
    threading.Timer(.1, cancel.set).start()
    result = run_experiment(
        [sys.executable, "experiment.py"], tmp_path, tmp_path / "out",
        cancel_event=cancel, checkpoint_path="checkpoints/latest.pt",
        stop_grace_seconds=2,
    )
    assert result["status"] == "interrupted"
    assert result["checkpoint_available"] is True


def test_autonomous_prompt_leaves_method_and_reporting_choices_to_agent():
    campaign = {
        "title": "Challenge",
        "objective": "Solve the public challenge",
        "research_protocol": "Read the official rules",
        "data_policy": "Local data only",
        "campaign_context": "Official challenge URL",
    }
    prompt = RunManager._autonomous_research_prompt(campaign)
    assert "détermine toi-même" in prompt
    assert "aucune méthode" in prompt
    assert "Official challenge URL" in prompt
    assert "brief durable" in prompt.lower()
