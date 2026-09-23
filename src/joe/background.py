from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class BackgroundState:
    pid: int
    port: int
    project: str
    host: str
    profile: str
    command: list[str]
    log: str
    started_at: str


def is_windows() -> bool:
    return sys.platform == "win32"


def runtime_dir() -> Path:
    if is_windows():
        root = Path(
            os.environ.get(
                "LOCALAPPDATA",
                Path.home() / "AppData" / "Local",
            )
        )
        return root / "Joe" / "run"
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "joe"


def state_path(port: int) -> Path:
    return runtime_dir() / f"server-{port}.json"


def log_path(port: int) -> Path:
    return runtime_dir() / f"server-{port}.log"


def load_state(port: int) -> BackgroundState | None:
    try:
        payload = json.loads(state_path(port).read_text(encoding="utf-8"))
        return BackgroundState(**payload)
    except (OSError, ValueError, TypeError):
        return None


def save_state(state: BackgroundState) -> None:
    target = state_path(state.port)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def start_windows_background(
    command: list[str],
    *,
    port: int,
    project: Path,
    host: str,
    profile: str,
) -> tuple[BackgroundState, subprocess.Popen[bytes]]:
    target_log = log_path(port)
    target_log.parent.mkdir(parents=True, exist_ok=True)
    stream = target_log.open("ab", buffering=0)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
    finally:
        stream.close()
    state = BackgroundState(
        pid=process.pid,
        port=port,
        project=str(project.resolve()),
        host=host,
        profile=profile,
        command=command,
        log=str(target_log),
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    save_state(state)
    return state, process


def stop_windows_background(port: int) -> bool:
    state = load_state(port)
    if state is None:
        return False
    stopped = not process_is_running(state.pid)
    if not stopped:
        result = subprocess.run(
            ["taskkill", "/PID", str(state.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        stopped = result.returncode == 0 or not process_is_running(state.pid)
    if stopped:
        state_path(port).unlink(missing_ok=True)
    return stopped


def managed_ports() -> list[int]:
    ports: list[int] = []
    try:
        candidates = runtime_dir().glob("server-*.json")
    except OSError:
        return ports
    for candidate in candidates:
        try:
            ports.append(int(candidate.stem.removeprefix("server-")))
        except ValueError:
            continue
    return sorted(set(ports))
