from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def run_experiment(
    command: list[str], workspace: Path, output_root: Path, *,
    working_directory: str = ".", metrics_path: str = "metrics.json",
    timeout_seconds: int = 600, cancel_event: threading.Event | None = None,
    stop_signal_path: str = "artifacts/STOP_REQUESTED", stop_grace_seconds: int = 30,
    checkpoint_path: str = "",
) -> dict[str, Any]:
    """Run one isolated, observable experiment without invoking a shell."""
    experiment_id = uuid.uuid4().hex
    output = output_root / experiment_id
    output.mkdir(parents=True, exist_ok=True)
    cwd = (workspace / working_directory).resolve()
    if workspace.resolve() not in (cwd, *cwd.parents):
        raise ValueError("Le dossier d'expérience doit rester dans le projet.")
    stop_signal = (cwd / stop_signal_path).resolve() if stop_signal_path else None
    if stop_signal is not None and cwd not in (stop_signal, *stop_signal.parents):
        raise ValueError("Le signal d'arrêt doit rester dans le projet.")
    if stop_signal is not None:
        stop_signal.unlink(missing_ok=True)
    if checkpoint_path:
        checkpoint = (cwd / checkpoint_path).resolve()
        if cwd not in (checkpoint, *checkpoint.parents):
            raise ValueError("Le checkpoint doit rester dans le projet.")
    started = time.time()
    status = "completed"
    exit_code: int | None = None
    error = None
    stdout_path, stderr_path = output / "stdout.log", output / "stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_file:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=stdout_file, stderr=stderr_file,
            text=True, shell=False,
        )
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            interrupted = cancel_event is not None and cancel_event.is_set()
            timed_out = time.monotonic() >= deadline
            if interrupted or timed_out:
                status = "interrupted" if interrupted else "timed_out"
                error = (
                    "Fenêtre d'exécution terminée."
                    if interrupted else f"Délai de {timeout_seconds} s dépassé."
                )
                if interrupted and stop_signal is not None:
                    stop_signal.parent.mkdir(parents=True, exist_ok=True)
                    stop_signal.write_text(
                        json.dumps({"requested_at": time.time()}), encoding="utf-8"
                    )
                    grace_deadline = time.monotonic() + stop_grace_seconds
                    while process.poll() is None and time.monotonic() < grace_deadline:
                        time.sleep(0.2)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                break
            time.sleep(0.2)
        exit_code = process.wait()
        if status == "completed" and exit_code != 0:
            status = "crashed"
            error = f"La commande s'est terminée avec le code {exit_code}."
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
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
        "checkpoint_available": _checkpoint_available(cwd, checkpoint_path),
    }
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _checkpoint_available(cwd: Path, checkpoint_path: str) -> bool:
    if not checkpoint_path:
        return False
    checkpoint = (cwd / checkpoint_path).resolve()
    if cwd not in (checkpoint, *checkpoint.parents):
        raise ValueError("Le checkpoint doit rester dans le projet.")
    return checkpoint.is_file()
