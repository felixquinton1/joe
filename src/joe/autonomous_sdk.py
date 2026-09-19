from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal


MetricDirection = Literal["min", "max"]


@dataclass(frozen=True)
class ResourceRequest:
    cpu_threads: int | None = None
    ram_mb: int | None = None
    accelerator: str | None = None
    vram_mb: int | None = None


@dataclass(frozen=True)
class ValidationSpec:
    strategy: str
    folds: int | None = None
    seed: int | None = None
    leakage_controls: tuple[str, ...] = ()
    split_fingerprint: str | None = None
    data_fingerprint: str | None = None


@dataclass(frozen=True)
class ExperimentSpec:
    hypothesis: str
    primary_metric: str
    direction: MetricDirection
    budget_seconds: int
    validation: ValidationSpec
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    experiment_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_experiment_id: str | None = None
    variant: str | None = None
    expected_outcome: str | None = None
    decision_rule: str | None = None
    estimated_gpu_minutes: float | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.hypothesis.strip():
            raise ValueError("Une expérience exige une hypothèse explicite.")
        if not self.primary_metric.strip():
            raise ValueError("Une expérience exige une métrique primaire.")
        if self.direction not in {"min", "max"}:
            raise ValueError("La direction doit être min ou max.")
        if self.budget_seconds <= 0:
            raise ValueError("Le budget expérimental doit être positif.")


@dataclass
class ExperimentOutcome:
    primary_value: float
    secondary_metrics: dict[str, float] = field(default_factory=dict)
    resources: dict[str, float | int | str | None] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ExperimentContext:
    def __init__(self, workspace: Path, spec: ExperimentSpec):
        self.workspace = workspace.resolve()
        self.spec = spec
        self.started_at = time.time()
        self.stop_signal = self.workspace / "artifacts" / "STOP_REQUESTED"
        self.checkpoint_dir = self.workspace / "checkpoints" / spec.experiment_id

    @property
    def should_stop(self) -> bool:
        return self.stop_signal.exists() or time.time() - self.started_at >= self.spec.budget_seconds

    def checkpoint_path(self, name: str) -> Path:
        safe = Path(name)
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError("Le checkpoint doit rester dans son dossier d'expérience.")
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        return self.checkpoint_dir / safe


def run_experiment(
    spec: ExperimentSpec,
    function: Callable[[ExperimentContext], ExperimentOutcome],
    *,
    workspace: str | Path = ".",
    output_path: str | Path = "artifacts/metrics.json",
) -> dict[str, Any]:
    """Execute project code and publish Joe's versioned result envelope."""
    root = Path(workspace).resolve()
    context = ExperimentContext(root, spec)
    status = "completed"
    error: dict[str, str] | None = None
    outcome: ExperimentOutcome | None = None
    try:
        outcome = function(context)
        if not isinstance(outcome, ExperimentOutcome):
            raise TypeError("Le callable doit retourner ExperimentOutcome.")
    except Exception as exc:
        status = "crashed"
        error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    result = build_result(spec, context, outcome, status=status, error=error)
    publish_result(result, root / output_path)
    print(json.dumps({"kind": "experiment", "status": status, "metrics": result}, ensure_ascii=False))
    return result


def build_result(
    spec: ExperimentSpec,
    context: ExperimentContext,
    outcome: ExperimentOutcome | None,
    *,
    status: str,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    finished = time.time()
    primary = None if outcome is None else float(outcome.primary_value)
    return {
        "schema_version": 1,
        "status": status,
        "experiment": {
            "id": spec.experiment_id,
            "parent_experiment_id": spec.parent_experiment_id,
            "variant": spec.variant,
            "hypothesis": spec.hypothesis,
            "expected_outcome": spec.expected_outcome,
            "decision_rule": spec.decision_rule,
            "estimated_gpu_minutes": spec.estimated_gpu_minutes,
            "budget_seconds": spec.budget_seconds,
            "tags": list(spec.tags),
        },
        "primary_metric": {
            "name": spec.primary_metric,
            "value": primary,
            "direction": spec.direction,
        },
        "secondary_metrics": {} if outcome is None else outcome.secondary_metrics,
        "resources": {**asdict(spec.resources), **({} if outcome is None else outcome.resources)},
        "validation": {
            **asdict(spec.validation),
            "leakage_controls": list(spec.validation.leakage_controls),
        },
        "artifacts": [] if outcome is None else outcome.artifacts,
        "metadata": {} if outcome is None else outcome.metadata,
        "reproducibility": _reproducibility(context.workspace),
        "timing": {
            "started_at": context.started_at,
            "finished_at": finished,
            "duration_seconds": round(finished - context.started_at, 3),
        },
        "error": error,
    }


def _reproducibility(workspace: Path) -> dict[str, Any]:
    commit = None
    try:
        value = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10, encoding="utf-8", errors="replace"
        )
        commit = value.stdout.strip() if value.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "git_commit": commit,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def publish_result(result: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
