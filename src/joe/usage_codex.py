from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from typing import Any, Callable


def codex_status(
    version: str,
    unavailable: Callable[[str, str], dict[str, Any]],
    *,
    subprocess_module=subprocess,
    selectors_module=selectors,
    os_module=os,
    time_module=time,
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
        process = subprocess_module.Popen(
            ["codex", "app-server", "--stdio"],
            stdin=subprocess_module.PIPE,
            stdout=subprocess_module.PIPE,
            stderr=subprocess_module.DEVNULL,
        )
        if process.stdin is None or process.stdout is None:
            raise OSError("Codex stdio unavailable")
        send(process, initialize)
        selector = selectors_module.DefaultSelector()
        selector.register(process.stdout, selectors_module.EVENT_READ)
        buffer = b""
        deadline = time_module.monotonic() + 8
        while time_module.monotonic() < deadline:
            if not selector.select(timeout=0.25):
                continue
            chunk = os_module.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
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
