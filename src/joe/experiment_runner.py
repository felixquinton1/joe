from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any


def run_experiment(
    command: list[str], workspace: Path, output_root: Path, *,
    working_directory: str = ".", metrics_path: str = "metrics.json",
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run one isolated, observable experiment without invoking a shell."""
    experiment_id = uuid.uuid4().hex
    output = output_root / experiment_id
    output.mkdir(parents=True, exist_ok=True)
    cwd = (workspace / working_directory).resolve()
    if workspace.resolve() not in (cwd, *cwd.parents):
        raise ValueError("Le dossier d'expérience doit rester dans le projet.")
    started = time.time()
    status = "completed"
    exit_code: int | None = None
    error = None
    try:
        completed = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, errors="replace",
            timeout=timeout_seconds, shell=False,
        )
        exit_code = completed.returncode
        (output / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (output / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        if exit_code != 0:
            status = "crashed"
            error = f"La commande s'est terminée avec le code {exit_code}."
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        status, error = "timed_out", f"Délai de {timeout_seconds} s dépassé."
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        (output / "stdout.log").write_text(stdout, encoding="utf-8")
        (output / "stderr.log").write_text(stderr, encoding="utf-8")
    metrics: dict[str, Any] = {}
    candidate = (cwd / metrics_path).resolve()
    if cwd not in (candidate, *candidate.parents):
        error = "Le fichier de métriques doit rester dans le dossier d'expérience."
        status = "crashed"
    elif candidate.exists():
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                metrics = value
        except (OSError, json.JSONDecodeError) as exc:
            error = f"Métriques illisibles : {exc}"
            status = "crashed"
    result = {
        "id": experiment_id, "status": status, "exit_code": exit_code,
        "duration_seconds": round(time.time() - started, 3), "metrics": metrics,
        "stdout_tail": stdout[-4000:], "stderr_tail": stderr[-4000:], "error": error,
        "artifacts": str(output),
    }
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
