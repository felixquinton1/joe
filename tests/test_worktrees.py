import subprocess

from joe.worktrees import WorktreeManager


def test_worktree_manager_creates_isolated_checkout(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("base\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    worktree = WorktreeManager(tmp_path).create("run-1")
    assert worktree.exists()
    assert (worktree / "README.md").read_text() == "base\n"
    (worktree / "README.md").write_text("isolated\n")
    assert (tmp_path / "README.md").read_text() == "base\n"
    WorktreeManager(tmp_path).remove(worktree)
    assert not worktree.exists()
