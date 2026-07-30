from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    """Create isolated worktrees only when a project explicitly enables them."""

    def __init__(self, repository: Path):
        self.repository = repository.resolve()

    def create(self, run_id: str) -> Path:
        if not (self.repository / ".git").exists():
            raise WorktreeError("Le projet n’est pas un dépôt Git.")
        target = self.repository / ".agentflow" / "worktrees" / run_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(target), "HEAD"],
            cwd=self.repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise WorktreeError(result.stderr.strip() or "Création du worktree impossible.")
        return target

    def remove(self, path: Path) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=self.repository,
            text=True,
            capture_output=True,
            check=False,
        )
