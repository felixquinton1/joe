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
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Run one isolated, observable and recoverable experiment.

    ``experiment_id`` is a durable idempotency key. Reusing it after a Joe
    restart returns the completed result, monitors the still-running process,
    or reconstructs an interrupted result; it never starts the command twice.
    """
    experiment_id = experiment_id or uuid.uuid4().hex
    output = output_root / experiment_id
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    manifest_path = output / "execution.json"
    recovered = _recover_existing(
        experiment_id, result_path, manifest_path, workspace,
        working_directory, metrics_path, checkpoint_path, output,
    )
    if recovered is not None:
        return recovered
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
    candidate = (cwd / metrics_path).resolve()
    if cwd not in (candidate, *candidate.parents):
        raise ValueError("Le fichier de métriques doit rester dans le dossier d'expérience.")
    # A run must never inherit a score published by an earlier experiment.
    candidate.unlink(missing_ok=True)
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
        _write_json(result_path, result)
        _write_json(manifest_path, {
            "version": 1, "id": experiment_id, "status": "finished",
            "result": str(result_path), "finished_at": time.time(),
        })
        return result
    _write_json(manifest_path, {
        "version": 1, "id": experiment_id, "status": "starting",
        "command": command, "started_at": started,
        "working_directory": working_directory,
        "metrics_path": metrics_path, "checkpoint_path": checkpoint_path,
    })
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_file:
        environment = os.environ.copy()
        environment.update({
            "JOE_AUTONOMOUS_ATTEMPT_ID": experiment_id,
            "JOE_AUTONOMOUS_OUTPUT_DIR": str(output),
        })
        process = subprocess.Popen(
            command, cwd=cwd, stdout=stdout_file, stderr=stderr_file,
            text=True, shell=False, env=environment,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
            start_new_session=os.name != "nt", encoding="utf-8", errors="replace"
        )
        _write_json(manifest_path, {
            "version": 1, "id": experiment_id, "status": "running",
            "pid": process.pid, "command": command, "started_at": started,
            "working_directory": working_directory,
            "metrics_path": metrics_path, "checkpoint_path": checkpoint_path,
        })
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
    if candidate.exists():
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                metrics = value
        except (OSError, json.JSONDecodeError) as exc:
            error = f"Métriques illisibles : {exc}"
            status = "crashed"
    if not metrics:
        metrics = _metrics_from_stdout(stdout)
    if metrics.get("schema_version") and metrics.get("status") == "crashed":
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
    _write_json(result_path, result)
    _write_json(manifest_path, {
        "version": 1, "id": experiment_id, "status": "finished",
        "pid": process.pid, "result": str(result_path),
        "started_at": started, "finished_at": time.time(),
    })
    return result


def recover_experiment(
    workspace: Path,
    output_root: Path,
    experiment_id: str,
    *,
    working_directory: str = ".",
    metrics_path: str = "metrics.json",
    checkpoint_path: str = "",
) -> dict[str, Any] | None:
    """Recover a durable experiment without launching another process.

    ``None`` means the original process is still alive and can be monitored on
    the next scheduler pass.
    """
    output = output_root / experiment_id
    return _recover_existing(
        experiment_id, output / "result.json", output / "execution.json",
        workspace, working_directory, metrics_path, checkpoint_path, output,
    )


def _recover_existing(
    experiment_id: str,
    result_path: Path,
    manifest_path: Path,
    workspace: Path,
    working_directory: str,
    metrics_path: str,
    checkpoint_path: str,
    output: Path,
) -> dict[str, Any] | None:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result = None
    if isinstance(result, dict):
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    pid = manifest.get("pid")
    if manifest.get("status") == "running" and isinstance(pid, int) and _pid_alive(pid):
        return None

    cwd = (workspace / working_directory).resolve()
    candidate = (cwd / metrics_path).resolve()
    metrics: dict[str, Any] = {}
    if candidate.exists():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                metrics = payload
        except (OSError, json.JSONDecodeError):
            pass
    stdout_path, stderr_path = output / "stdout.log", output / "stderr.log"
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    if not metrics:
        metrics = _metrics_from_stdout(stdout)
    checkpoint_available = _checkpoint_available(cwd, checkpoint_path)
    completed = bool(metrics) and metrics.get("status") != "crashed"
    status = "completed" if completed else ("interrupted" if checkpoint_available else "crashed")
    error = None if completed else (
        "Processus interrompu avec checkpoint récupérable."
        if checkpoint_available else
        "Processus perdu lors du redémarrage de Joe, sans résultat ni checkpoint récupérable."
    )
    result = {
        "id": experiment_id, "status": status, "exit_code": None,
        "duration_seconds": round(max(0.0, time.time() - float(manifest.get("started_at", time.time()))), 3),
        "metrics": metrics, "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:], "error": error,
        "artifacts": str(output), "checkpoint_available": checkpoint_available,
        "recovered_after_restart": True,
    }
    _write_json(result_path, result)
    _write_json(manifest_path, {
        **manifest, "status": "recovered", "finished_at": time.time(),
        "result": str(result_path),
    })
    return result


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True,
            text=True, check=False, encoding="utf-8", errors="replace"
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate the experiment and every child it spawned."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, text=True, check=False, encoding="utf-8", errors="replace"
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
