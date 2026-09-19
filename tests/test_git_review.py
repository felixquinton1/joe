import subprocess

from joe.git_review import build_report, deliver, reject, snapshot


def git(project, *args):
    return subprocess.run(
        ["git", *args],
        cwd=project,
        text=True,
        capture_output=True,
        check=True, encoding="utf-8"
    )


def repository(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "joe@example.test")
    git(tmp_path, "config", "user.name", "Joe Tests")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-m", "initial")
    (tmp_path / ".agentflow" / "runs").mkdir(parents=True)
    return tracked


def test_report_tracks_files_and_can_restore_clean_start(tmp_path):
    tracked = repository(tmp_path)
    before = snapshot(tmp_path)
    tracked.write_text("one\ntwo\n", encoding="utf-8")
    created = tmp_path / "created.txt"
    created.write_text("new\n", encoding="utf-8")

    report, rejection = build_report(
        tmp_path, before, "run-1", concurrent_run=False
    )

    assert report["head_before"] == report["head_after"]
    assert report["insertions"] == 2
    assert {item["path"] for item in report["files"]} == {
        "created.txt",
        "tracked.txt",
    }
    assert report["rejectable"] is True

    restored, _ = reject(tmp_path, rejection)
    assert restored is True
    assert tracked.read_text(encoding="utf-8") == "one\n"
    assert not created.exists()


def test_report_does_not_offer_rejection_over_preexisting_work(tmp_path):
    tracked = repository(tmp_path)
    tracked.write_text("existing change\n", encoding="utf-8")
    before = snapshot(tmp_path)
    tracked.write_text("agent change\n", encoding="utf-8")

    report, rejection = build_report(
        tmp_path, before, "run-2", concurrent_run=False
    )

    assert report["preexisting_dirty"] is True
    assert report["rejectable"] is False
    assert rejection is None


def test_reject_can_restore_only_selected_files(tmp_path):
    tracked = repository(tmp_path)
    before = snapshot(tmp_path)
    tracked.write_text("one\ntwo\n", encoding="utf-8")
    created = tmp_path / "created.txt"
    created.write_text("new\n", encoding="utf-8")
    _, rejection = build_report(
        tmp_path, before, "run-selected", concurrent_run=False
    )

    restored, _ = reject(
        tmp_path,
        rejection,
        selected_files=["created.txt"],
    )

    assert restored is True
    assert tracked.read_text(encoding="utf-8") == "one\ntwo\n"
    assert not created.exists()


def test_reject_can_restore_selected_tracked_file(tmp_path):
    tracked = repository(tmp_path)
    before = snapshot(tmp_path)
    tracked.write_text("one\ntwo\n", encoding="utf-8")
    created = tmp_path / "created.txt"
    created.write_text("new\n", encoding="utf-8")
    _, rejection = build_report(
        tmp_path, before, "run-selected-tracked", concurrent_run=False
    )

    restored, _ = reject(
        tmp_path,
        rejection,
        selected_files=["tracked.txt"],
    )

    assert restored is True
    assert tracked.read_text(encoding="utf-8") == "one\n"
    assert created.read_text(encoding="utf-8") == "new\n"


def test_unchanged_preexisting_files_are_not_attributed_to_run(tmp_path):
    tracked = repository(tmp_path)
    tracked.write_text("existing change\n", encoding="utf-8")
    untracked = tmp_path / "existing-output.txt"
    untracked.write_text("existing output\n", encoding="utf-8")
    before = snapshot(tmp_path)

    report, _ = build_report(
        tmp_path, before, "run-3", concurrent_run=False
    )

    assert report["files"] == []
    assert report["insertions"] == 0
    assert report["deletions"] == 0


def test_fetch_head_change_is_reported_even_when_remote_ref_is_unchanged(tmp_path):
    repository(tmp_path)
    before = snapshot(tmp_path)
    (tmp_path / ".git" / "FETCH_HEAD").write_text("same remote commit\n", encoding="utf-8")

    report, _ = build_report(
        tmp_path, before, "run-4", concurrent_run=False
    )

    assert report["fetch_observed"] is True
    assert report["origin_dev_before"] == report["origin_dev_after"]


def test_deliver_commits_and_pushes_only_attributed_changes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    tracked = repository(project)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(project, "remote", "add", "origin", str(remote))
    branch = git(project, "branch", "--show-current").stdout.strip()
    git(project, "push", "-u", "origin", branch)
    before = snapshot(project)
    tracked.write_text("one\ntwo\n", encoding="utf-8")
    report, _ = build_report(
        project, before, "run-delivery", concurrent_run=False
    )

    delivery = deliver(project, report, "test: deliver")

    assert delivery["status"] == "pushed"
    assert git(project, "diff", "--exit-code").stdout == ""
    assert git(
        project, "ls-remote", "origin", f"refs/heads/{branch}"
    ).stdout.startswith(delivery["commit"])
