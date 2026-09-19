from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class WorktreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_commit: str


class WorktreeManager:
    """Create isolated worktrees only when a project explicitly enables them."""

    def __init__(self, repository: Path):
        self.repository = repository.resolve()

    def create(self, run_id: str) -> Worktree:
        if not (self.repository / ".git").exists():
            raise WorktreeError("Le projet n’est pas un dépôt Git.")
        target = self.repository / ".agentflow" / "worktrees" / run_id
        branch = f"joe/{run_id[:12]}"
        target.parent.mkdir(parents=True, exist_ok=True)
        branch_exists = self._git(
            "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
        ).returncode == 0
        base_commit = (
            self._value("merge-base", "HEAD", branch)
            if branch_exists
            else self._value("rev-parse", "HEAD")
        )
        if target.exists():
            return Worktree(target, branch, base_commit)
        arguments = (
            ["worktree", "add", str(target), branch]
            if branch_exists
            else ["worktree", "add", "-b", branch, str(target), "HEAD"]
        )
        result = self._git(*arguments)
        if result.returncode:
            raise WorktreeError(result.stderr.strip() or "Création du worktree impossible.")
        return Worktree(target, branch, base_commit)

    def diff(self, worktree: Worktree) -> dict[str, object]:
        if not worktree.path.exists():
            raise WorktreeError("Le worktree de cette tâche n’existe plus.")
        diff = self._run_at(
            worktree.path,
            "diff", "--numstat", worktree.base_commit, "--",
        )
        files = []
        insertions = deletions = 0
        for line in diff.stdout.splitlines():
            added, removed, path = line.split("\t", 2)
            if path.startswith(".agentflow/"):
                continue
            added_count = int(added) if added.isdigit() else 0
            removed_count = int(removed) if removed.isdigit() else 0
            files.append({
                "path": path,
                "insertions": added_count,
                "deletions": removed_count,
            })
            insertions += added_count
            deletions += removed_count
        tracked = {str(item["path"]) for item in files}
        untracked = self._run_at(
            worktree.path, "ls-files", "--others", "--exclude-standard",
        )
        for path in filter(None, untracked.stdout.splitlines()):
            if path in tracked or path.startswith(".agentflow/"):
                continue
            count = _line_count(worktree.path / path)
            files.append({"path": path, "insertions": count, "deletions": 0})
            insertions += count
        preview = self._run_at(
            worktree.path,
            "diff", "--no-ext-diff", "--unified=3", worktree.base_commit, "--",
        ).stdout
        return {
            "files": sorted(files, key=lambda item: str(item["path"])),
            "insertions": insertions,
            "deletions": deletions,
            "patch_preview": preview[:50_000],
        }

    def integrate(
        self,
        worktree: Worktree,
        message: str,
        resolver: Callable[[Worktree, list[str], int], None] | None = None,
    ) -> str:
        if not worktree.path.exists():
            raise WorktreeError("Le worktree de cette tâche n’existe plus.")
        if self._dirty(self.repository):
            raise WorktreeError(
                "Le dépôt principal contient des modifications. "
                "Committe ou range-les avant d’intégrer cette tâche."
            )
        if self._dirty(worktree.path):
            staged = self._run_at(
                worktree.path,
                "add",
                "-A",
                "--",
                ".",
                ":(exclude).agentflow/**",
            )
            if staged.returncode:
                raise WorktreeError(staged.stderr.strip())
            committed = self._run_at(worktree.path, "commit", "-m", message)
            if committed.returncode:
                raise WorktreeError(
                    committed.stderr.strip() or "Commit du worktree impossible."
                )
        if self._value("rev-list", "--count", f"HEAD..{worktree.branch}") == "0":
            raise WorktreeError("Cette tâche ne contient aucune modification à intégrer.")
        self._synchronize(worktree, resolver)
        merged = self._git("merge", "--ff-only", worktree.branch)
        if merged.returncode:
            raise WorktreeError(
                "La branche principale a changé pendant l’intégration. "
                "Relance l’opération."
            )
        commit = self._value("rev-parse", "HEAD")
        # La fusion est acquise : un échec de nettoyage ne doit pas faire
        # rapporter un conflit pour une intégration qui a bien eu lieu.
        try:
            self.remove(worktree, delete_branch=True)
        except WorktreeError:
            pass
        return commit

    def _synchronize(
        self,
        worktree: Worktree,
        resolver: Callable[[Worktree, list[str], int], None] | None,
    ) -> None:
        if self._git(
            "merge-base", "--is-ancestor", "HEAD", worktree.branch
        ).returncode == 0:
            return
        rebased = self._run_at(
            worktree.path,
            "rebase",
            self._value("rev-parse", "HEAD"),
        )
        attempt = 0
        while rebased.returncode:
            conflicts = self._unmerged(worktree.path)
            if not conflicts or resolver is None or attempt >= 3:
                self._run_at(worktree.path, "rebase", "--abort")
                raise WorktreeError(
                    "Conflit avec les changements déjà intégrés. "
                    "Le worktree a été conservé."
                )
            attempt += 1
            try:
                resolver(worktree, conflicts, attempt)
                if self._contains_conflict_markers(worktree.path, conflicts):
                    raise WorktreeError(
                        "La résolution contient encore des marqueurs de conflit."
                    )
                staged = self._run_at(
                    worktree.path,
                    "add",
                    "-A",
                    "--",
                    ".",
                    ":(exclude).agentflow/**",
                )
                if staged.returncode:
                    raise WorktreeError(staged.stderr.strip())
                rebased = self._run_at(
                    worktree.path,
                    "-c",
                    "core.editor=true",
                    "rebase",
                    "--continue",
                )
            except Exception:
                self._run_at(worktree.path, "rebase", "--abort")
                raise

    def remove(self, worktree: Worktree, *, delete_branch: bool = True) -> None:
        result = self._git("worktree", "remove", "--force", str(worktree.path))
        if result.returncode and worktree.path.exists():
            raise WorktreeError(
                result.stderr.strip() or "Suppression du worktree impossible."
            )
        if delete_branch:
            self._git("branch", "-D", worktree.branch)

    def abort_rebase(self, worktree: Worktree) -> None:
        """Restore a recoverable branch after a server interruption."""
        if worktree.path.exists():
            self._run_at(worktree.path, "rebase", "--abort")

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self._run_at(self.repository, *arguments)

    @staticmethod
    def _run_at(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=path,
            text=True,
            capture_output=True,
            check=False,
        )

    def _value(self, *arguments: str) -> str:
        result = self._git(*arguments)
        if result.returncode:
            raise WorktreeError(result.stderr.strip() or "Commande Git impossible.")
        return result.stdout.strip()

    @classmethod
    def _dirty(cls, path: Path) -> bool:
        result = cls._run_at(path, "status", "--porcelain", "--untracked-files=all")
        return bool(
            result.stdout
            and any(
                not line[3:].startswith(".agentflow/")
                for line in result.stdout.splitlines()
            )
        )

    @classmethod
    def _unmerged(cls, path: Path) -> list[str]:
        result = cls._run_at(
            path, "diff", "--name-only", "--diff-filter=U",
        )
        return [item for item in result.stdout.splitlines() if item]

    @staticmethod
    def _contains_conflict_markers(path: Path, conflicts: list[str]) -> bool:
        for relative in conflicts:
            try:
                content = (path / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "<<<<<<< " in content or ">>>>>>> " in content:
                return True
        return False


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0
