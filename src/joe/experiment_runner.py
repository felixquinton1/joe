from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def validate_experiment_command(command: list[str], cwd: Path) -> str | None:
    """Return a user-facing error when a command references a missing entrypoint."""
    if not command:
        return "La commande d'expérience est vide."
    lowered = [part.lower() for part in command]
    candidate: str | None = None
    if "-file" in lowered:
        index = lowered.index("-file") + 1
        if index < len(command):
            candidate = command[index]
    elif len(command) > 1 and Path(command[1]).suffix.lower() in {".py", ".ps1", ".cmd", ".bat"}:
        candidate = command[1]
    if not candidate:
        return None
    entrypoint = Path(candidate)
    if not entrypoint.is_absolute():
        entrypoint = cwd / entrypoint
    if not entrypoint.is_file():
        return (
            f"Point d'entrée d'expérience introuvable : {candidate}. "
            "Le runner contractuel doit exister avant le lancement."
        )
    return None


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
    validation_error = validate_experiment_command(command, cwd)
    if validation_error:
        result = {
            "id": experiment_id, "status": "crashed", "exit_code": None,
            "duration_seconds": round(time.time() - started, 3), "metrics": {},
            "stdout_tail": "", "stderr_tail": validation_error,
            "error": validation_error, "artifacts": str(output),
            "checkpoint_available": _checkpoint_available(cwd, checkpoint_path),
            "failure_signature": f"missing-entrypoint:{validation_error}",
        }
        (output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_file:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=stdout_file, stderr=stderr_file,
            text=True, shell=False,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
            start_new_session=os.name != "nt",
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
                    _terminate_process_tree(process)
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
    if not metrics:
        metrics = _metrics_from_stdout(stdout)
    if metrics.get("schema_version") == 1 and metrics.get("status") == "crashed":
        status = "crashed"
        structured_error = metrics.get("error")
        if isinstance(structured_error, dict):
            error = str(structured_error.get("message") or structured_error.get("type") or error)
    result = {
        "id": experiment_id, "status": status, "exit_code": exit_code,
        "duration_seconds": round(time.time() - started, 3), "metrics": metrics,
        "stdout_tail": stdout[-4000:], "stderr_tail": stderr[-4000:], "error": error,
        "artifacts": str(output),
        "checkpoint_available": _checkpoint_available(cwd, checkpoint_path),
    }
    if status == "crashed":
        result["failure_signature"] = _failure_signature(exit_code, stderr, error)
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate the experiment and every child it spawned."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, text=True, check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _failure_signature(exit_code: int | None, stderr: str, error: str | None) -> str:
    tail = " ".join(stderr.split())[-500:]
    return f"exit:{exit_code}|{tail or error or 'unknown'}"


def _checkpoint_available(cwd: Path, checkpoint_path: str) -> bool:
    if not checkpoint_path:
        return False
    checkpoint = (cwd / checkpoint_path).resolve()
    if cwd not in (checkpoint, *checkpoint.parents):
        raise ValueError("Le checkpoint doit rester dans le projet.")
    return checkpoint.exists()


def _metrics_from_stdout(stdout: str) -> dict[str, Any]:
    """Recover a runner's final sanitized metric envelope when no file was found."""
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or value.get("kind") != "experiment":
            continue
        metrics = value.get("metrics")
        if isinstance(metrics, dict):
            return metrics
    return {}
