from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .autonomous_resources import normalize_resource_policy


def inspect_resources(policy: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_resource_policy(policy)
    gpus = _nvidia_gpus()
    selected = normalized["gpu_index"]
    errors: list[str] = []
    if normalized["mode"] == "gpu_only" and not gpus:
        errors.append("La campagne exige un GPU, mais aucun GPU NVIDIA accessible n'a été détecté.")
    if selected is not None and not any(gpu["index"] == selected for gpu in gpus):
        errors.append(f"Le GPU {selected} demandé n'est pas accessible.")
    return {
        "ok": not errors,
        "policy": normalized,
        "resources": {
            "cpu_threads": os.cpu_count(),
            "ram_mb": _ram_mb(),
            "disk_free_mb": shutil.disk_usage(Path.cwd()).free // (1024 * 1024),
            "gpus": gpus,
            "selected_gpu_index": selected,
        },
        "errors": errors,
    }


def _nvidia_gpus() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi", "--query-gpu=index,name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, check=False, timeout=15, encoding="utf-8", errors="replace"
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    gpus = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            gpus.append({
                "index": int(parts[0]), "name": parts[1],
                "vram_mb": int(parts[2]), "driver": parts[3],
            })
        except ValueError:
            continue
    return gpus


def _ram_mb() -> int | None:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong), ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total_physical // (1024 * 1024)
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (AttributeError, OSError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/preflight.json")
    parser.add_argument("--policy-json", default="{}")
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy_json)
    except json.JSONDecodeError:
        policy = {}
    result = inspect_resources(policy)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"kind": "experiment", "status": "completed", "metrics": result}))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
