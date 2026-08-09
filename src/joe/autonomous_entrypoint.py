from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    workspace = Path.cwd()
    candidates = (
        workspace / "research_batch.py",
        workspace / "autonomous_experiment.py",
        workspace / "experiment.py",
    )
    runner = next((path for path in candidates if path.is_file()), None)
    if runner is None:
        raise SystemExit(
            "No scientific runner exists yet. Implement research_batch.py in the project workspace."
        )
    project_python = workspace / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")
    python = str(project_python) if project_python.is_file() else sys.executable
    command = [python, str(runner)]
    if args.resume:
        command.append("--resume")
    return subprocess.call(command, cwd=workspace)


if __name__ == "__main__":
    raise SystemExit(main())
