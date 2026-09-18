import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from joe.autonomous import AutonomousStore, build_autonomous_skill
from joe.autonomous_schedule import normalize_schedule, schedule_state
from joe.experiment_runner import run_experiment, validate_experiment_command
from joe.web_runs import RunManager, _autonomous_metric_value


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


def test_autonomous_store_persists_deterministic_preflight(tmp_path: Path):
    values = campaign_values() | {
        "preflight": {"status": "completed", "metrics": {"cuda": True}}
    }
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**values)
    assert campaign["preflight"]["status"] == "completed"
    assert campaign["preflight"]["metrics"]["cuda"] is True


def test_new_campaign_waits_in_preparing_state_for_start_time_preflight(tmp_path: Path):
    store = AutonomousStore(tmp_path)
    store.ensure()
    campaign = store.create(**(
        campaign_values()
        | {
            "preflight": {"status": "pending", "metrics": {}},
            "preflight_command": [sys.executable, "-m", "joe.autonomous_preflight"],
            "resource_policy": {"mode": "gpu_only", "gpu_index": 0},
        }
    ))

    assert campaign["state"] == "preparing"
    assert campaign["phase"] == "preparing"
    assert campaign["started_at"] is None
    assert campaign["preflight"]["status"] == "pending"
    assert campaign["resource_policy"]["mode"] == "gpu_only"


def test_scheduler_does_not_start_preflight_before_window(tmp_path: Path, monkeypatch):
    manager = RunManager(tmp_path)
    campaign = manager.autonomous.create(**campaign_values())
    started = []
    monkeypatch.setattr("joe.web_runs.schedule_state", lambda *_: {
        "active": False, "next_start": 12345.0, "window_end": None,
    })
    monkeypatch.setattr(manager, "_start_autonomous_preflight", started.append)

    manager._advance_autonomous()

    persisted = manager.autonomous.get(campaign["id"])
    assert started == []
    assert persisted["state"] == "preparing"
    assert persisted["started_at"] is None
    assert persisted["next_start_at"] == 12345.0


def test_scheduler_starts_budget_and_preflight_when_window_opens(tmp_path: Path, monkeypatch):
    manager = RunManager(tmp_path)
    campaign = manager.autonomous.create(**campaign_values())
    started = []
    monkeypatch.setattr("joe.web_runs.schedule_state", lambda *_: {
        "active": True, "next_start": None, "window_end": None,
    })
    monkeypatch.setattr(manager, "_start_autonomous_preflight", started.append)

    manager._advance_autonomous()

    persisted = manager.autonomous.get(campaign["id"])
    assert [item["id"] for item in started] == [campaign["id"]]
    assert persisted["started_at"] is not None
    assert persisted["active_window_started_at"] is not None


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
    assert "undertrained configuration" in skill
    assert "hours of local computation" in skill
    assert "compute_scale_audit" in iteration
    assert "dette de run substantiel" in iteration
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


def test_runner_recovers_sanitized_metrics_from_stdout(tmp_path: Path):
    script = tmp_path / "experiment.py"
    script.write_text(
        "import json\n"
        "print('setup complete')\n"
        "print(json.dumps({'kind': 'experiment', 'status': 'completed', "
        "'metrics': {'log_loss': .42}}))\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "checkpoints" / "latest"
    checkpoint.mkdir(parents=True)
    result = run_experiment(
        [sys.executable, "experiment.py"], tmp_path, tmp_path / "artifacts",
        metrics_path="artifacts/missing.json", checkpoint_path="checkpoints/latest",
    )
    assert result["metrics"] == {"log_loss": .42}
    assert result["checkpoint_available"] is True


def test_runner_detects_crash(tmp_path: Path):
    script = tmp_path / "experiment.py"
    script.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    result = run_experiment(
        [sys.executable, "experiment.py"], tmp_path, tmp_path / "artifacts",
    )
    assert result["status"] == "crashed"
    assert result["exit_code"] != 0


def test_runner_rejects_missing_contractual_entrypoint_without_spawning(tmp_path: Path):
    result = run_experiment(
        ["powershell", "-NoProfile", "-File", "autonomous_run.ps1"],
        tmp_path, tmp_path / "artifacts",
    )
    assert result["status"] == "crashed"
    assert result["exit_code"] is None
    assert result["failure_signature"].startswith("missing-entrypoint:")


def test_command_validation_accepts_existing_powershell_entrypoint(tmp_path: Path):
    (tmp_path / "autonomous_run.ps1").write_text("exit 0\n", encoding="utf-8")
    assert validate_experiment_command(
        ["powershell", "-File", "autonomous_run.ps1"], tmp_path
    ) is None


def test_metric_contract_reads_common_aggregate_envelopes():
    assert _autonomous_metric_value(
        {"summary": {"selected_log_loss": 0.42}}, "log_loss"
    ) == 0.42
    assert _autonomous_metric_value(
        {"aggregate": {"score_mean": 0.7}}, "score"
    ) == 0.7


def test_interrupted_metric_never_becomes_campaign_best(tmp_path: Path):
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    campaign = manager.autonomous.create(**(
        campaign_values()
        | {"conversation_id": conversation["id"], "metric_name": "score"}
    ))
    manager.autonomous.transition(campaign["id"], "ready", "preflight_accepted")
    manager.autonomous.transition(campaign["id"], "experimenting", "experiment_started")
    manager.autonomous.add_event(campaign["id"], "experiment", {
        "id": "partial", "status": "interrupted", "metrics": {"score": 0.99},
        "duration_seconds": 10, "checkpoint_available": False,
    })
    manager.autonomous.transition(
        campaign["id"], "evaluating", "experiment_finished",
        phase="evaluation", current_experiment_id="partial",
    )

    manager._finish_autonomous_iteration(manager.autonomous.get(campaign["id"]))

    assert manager.autonomous.get(campaign["id"])["best_metric"] is None


def test_scheduler_stops_before_new_prompt_when_model_call_budget_is_spent(
    tmp_path: Path, monkeypatch,
):
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    campaign = manager.autonomous.create(**(
        campaign_values()
        | {
            "conversation_id": conversation["id"],
            "token_budget": {"max_model_calls": 1, "max_tokens": None},
        }
    ))
    manager.autonomous.transition(
        campaign["id"], "ready", "preflight_accepted", phase="planning"
    )
    manager.autonomous.add_event(campaign["id"], "agent_step", {
        "status": "completed", "provider": "codex",
        "attempts": [{"provider": "codex", "usage": {"input_tokens": 10}}],
    })
    monkeypatch.setattr("joe.web_runs.schedule_state", lambda *_: {
        "active": True, "next_start": None, "window_end": None,
    })

    manager._advance_autonomous()

    persisted = manager.autonomous.get(campaign["id"])
    assert persisted["status"] == "completed"
    assert persisted["state_history"][-1]["reason"] == "model_call_budget_reached"


def test_manual_handoff_preserves_history_and_elapsed_budget_on_resume(tmp_path: Path):
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    campaign = manager.autonomous.create(**(
        campaign_values() | {"conversation_id": conversation["id"]}
    ))
    manager.autonomous.transition(campaign["id"], "ready", "preflight_accepted")
    manager.autonomous.add_event(campaign["id"], "experiment", {
        "id": "kept", "status": "completed", "metrics": {},
    })
    manager.autonomous.update(
        campaign["id"], active_elapsed_seconds=120, started_at=time.time() - 120,
    )

    handed = manager.handoff_autonomous(campaign["id"])
    resumed = manager.resume_autonomous(campaign["id"])

    assert handed["manual_hold"] is True
    assert resumed["manual_hold"] is False
    assert resumed["active_elapsed_seconds"] >= 120
    assert any(event.get("id") == "kept" for event in resumed["history"])


def test_plateau_triggers_method_research_before_stopping(tmp_path: Path):
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    campaign = manager.autonomous.create(**(
        campaign_values()
        | {"conversation_id": conversation["id"], "max_iterations": 10,
           "metric_name": "loss", "metric_direction": "min", "plateau_patience": 2}
    ))
    manager.autonomous.transition(campaign["id"], "ready", "preflight_accepted")

    def finish(iteration: int, value: float):
        manager.autonomous.transition(campaign["id"], "experimenting", "experiment_started")
        manager.autonomous.add_event(campaign["id"], "experiment", {
            "id": f"e{iteration}", "iteration": iteration, "status": "completed",
            "metrics": {
                "primary_metric": {"name": "loss", "value": value, "direction": "min"},
                "validation": {"strategy": "group_kfold", "folds": 3,
                               "leakage_controls": ["subject"], "split_fingerprint": "same"},
            },
        })
        manager.autonomous.transition(
            campaign["id"], "evaluating", "experiment_finished",
            phase="evaluation", current_experiment_id=f"e{iteration}", iteration=iteration,
        )
        manager._finish_autonomous_iteration(manager.autonomous.get(campaign["id"]))

    finish(1, .5)
    finish(2, .6)
    finish(3, .7)

    persisted = manager.autonomous.get(campaign["id"])
    assert persisted["phase"] == "research_refresh"
    assert persisted["plateau_refresh_iteration"] == 3


def test_timeout_terminates_experiment_children(tmp_path: Path):
    (tmp_path / "metrics.json").write_text('{"score": 999}', encoding="utf-8")
    marker = tmp_path / "orphan.txt"
    child = tmp_path / "child.py"
    child.write_text(
        "import pathlib,time\ntime.sleep(2)\npathlib.Path('orphan.txt').write_text('bad')\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    result = run_experiment(
        [sys.executable, "parent.py"], tmp_path, tmp_path / "out", timeout_seconds=1
    )
    time.sleep(1.5)
    assert result["status"] == "timed_out"
    assert result["metrics"] == {}
    assert not marker.exists()


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
