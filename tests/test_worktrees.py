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
    assert worktree.path.exists()
    assert worktree.branch == "joe/run-1"
    assert (worktree.path / "README.md").read_text() == "base\n"
    (worktree.path / "README.md").write_text("isolated\n")
    assert (tmp_path / "README.md").read_text() == "base\n"
    WorktreeManager(tmp_path).remove(worktree)
    assert not worktree.path.exists()
    branches = subprocess.run(
        ["git", "branch", "--list", worktree.branch],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert not branches.stdout.strip()


def test_worktree_diff_and_integration(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "README.md").write_text("base\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

    manager = WorktreeManager(tmp_path)
    worktree = manager.create("integration-run")
    (worktree.path / "README.md").write_text("integrated\n")
    (worktree.path / "new.txt").write_text("one\ntwo\n")
    (worktree.path / ".agentflow").mkdir()
    (worktree.path / ".agentflow" / "session.md").write_text("private context\n")

    report = manager.diff(worktree)
    assert {item["path"] for item in report["files"]} == {"README.md", "new.txt"}
    assert report["insertions"] == 3
    assert report["deletions"] == 1

    commit = manager.integrate(worktree, "test: integrate task")
    assert len(commit) == 40
    assert (tmp_path / "README.md").read_text() == "integrated\n"
    assert (tmp_path / "new.txt").read_text() == "one\ntwo\n"
    assert not worktree.path.exists()
    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert ".agentflow/session.md" not in tracked.stdout
