from __future__ import annotations

from typing import Any


RESOURCE_MODES = {"auto", "gpu_only", "cpu_only"}


def normalize_resource_policy(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    mode = str(raw.get("mode", "auto"))
    if mode not in RESOURCE_MODES:
        mode = "auto"
    gpu_index = raw.get("gpu_index")
    try:
        gpu_index = None if gpu_index in {None, ""} else max(0, int(gpu_index))
    except (TypeError, ValueError):
        gpu_index = None
    return {
        "mode": mode,
        "gpu_index": gpu_index,
        "notes": str(raw.get("notes", "")).strip()[:1000],
    }

