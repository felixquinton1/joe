from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


def codex_status(
    version: str,
    unavailable: Callable[[str, str], dict[str, Any]],
    *,
    subprocess_module=subprocess,
    time_module=time,
    queue_module=queue,
    threading_module=threading,
    shutil_module=shutil,
) -> dict[str, Any]:
    initialize = {
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "joe", "version": version},
            "capabilities": {"experimentalApi": True},
        },
    }
    try:
        executable = windows_aware_executable("codex", shutil_module)
        if not executable:
            raise OSError("Codex executable unavailable")
        process = subprocess_module.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess_module.PIPE,
            stdout=subprocess_module.PIPE,
            stderr=subprocess_module.DEVNULL,
            env=codex_environment(),
        )
        if process.stdin is None or process.stdout is None:
            raise OSError("Codex stdio unavailable")
        send(process, initialize)
        messages: queue.Queue[bytes | None] = queue_module.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            try:
                for raw in iter(process.stdout.readline, b""):
                    messages.put(raw)
            finally:
                messages.put(None)

        threading_module.Thread(target=read_stdout, daemon=True).start()
        deadline = time_module.monotonic() + 8
        while time_module.monotonic() < deadline:
            try:
                raw = messages.get(
                    timeout=min(0.25, max(0.0, deadline - time_module.monotonic()))
                )
            except queue_module.Empty:
                continue
            if raw is None:
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 1:
                send(process, {"method": "initialized", "params": {}})
                send(
                    process,
                    {
                        "id": 2,
                        "method": "account/rateLimits/read",
                        "params": None,
                    },
                )
            if message.get("id") == 2 and isinstance(message.get("result"), dict):
                return normalize_codex_usage(message["result"])
    except OSError:
        return unavailable("codex", "Quota Codex temporairement indisponible")
    finally:
        if "process" in locals():
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess_module.TimeoutExpired:
                process.kill()
    return unavailable("codex", "Quota Codex temporairement indisponible")


def windows_aware_executable(name: str, shutil_module=shutil) -> str | None:
    """Prefer npm's executable batch shim on Windows when one exists."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            npm_shim = os.path.join(appdata, "npm", f"{name}.cmd")
            if os.path.isfile(npm_shim):
                return npm_shim
        command = shutil_module.which(f"{name}.cmd")
        if command:
            return command
    return shutil_module.which(name)


def codex_environment() -> dict[str, str]:
    """Give npm shims access to Node even when Joe starts before PATH refresh."""
    environment = dict(os.environ)
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs",
        Path(os.environ.get("APPDATA", "")) / "npm",
    ]
    additions = [str(path) for path in candidates if path.is_dir()]
    current = environment.get("PATH", "")
    environment["PATH"] = os.pathsep.join(additions + ([current] if current else []))
    return environment


def send(process: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write((json.dumps(message) + "\n").encode())
    process.stdin.flush()


def normalize_codex_usage(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("rateLimits") or {}
    windows = []
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if not isinstance(window, dict):
            continue
        duration = window.get("windowDurationMins")
        used = max(0.0, min(100.0, float(window.get("usedPercent", 0))))
        windows.append(
            {
                "name": window_name(duration),
                "used_percent": round(used, 1),
                "remaining_percent": round(100 - used, 1),
                "resets_at": window.get("resetsAt"),
                "duration_minutes": window.get("windowDurationMins"),
            }
        )
    individual = snapshot.get("individualLimit")
    if isinstance(individual, dict) and individual.get("remainingPercent") is not None:
        remaining = max(0.0, min(100.0, float(individual["remainingPercent"])))
        windows.append(
            {
                "name": "Limite personnelle",
                "remaining_percent": round(remaining, 1),
                "used_percent": round(100 - remaining, 1),
                "resets_at": individual.get("resetsAt"),
                "duration_minutes": None,
            }
        )
    return {
        "provider": "codex",
        "available": bool(windows),
        "plan": snapshot.get("planType"),
        "windows": windows,
        "message": None if windows else "Aucune limite communiquée",
    }


def window_name(duration: Any) -> str:
    if not isinstance(duration, (int, float)) or duration <= 0:
        return "Fenêtre de quota"
    if duration % 1440 == 0:
        days = int(duration / 1440)
        return f"{days} jour{'s' if days > 1 else ''}"
    if duration % 60 == 0:
        hours = int(duration / 60)
        return f"{hours} heure{'s' if hours > 1 else ''}"
    return f"{int(duration)} minutes"
