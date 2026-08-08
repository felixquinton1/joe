from __future__ import annotations

from pathlib import Path


def read_utf8_compatible(path: Path) -> str:
    """Read current UTF-8 state or migrate legacy Windows CP-1252 state."""
    payload = path.read_bytes()
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("cp1252")
