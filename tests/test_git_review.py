import subprocess

from joe.git_review import build_report, reject, snapshot


def git(project, *args):
    return subprocess.run(
        ["git", *args],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )


def repository(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "joe@example.test")
    git(tmp_path, "config", "user.name", "Joe Tests")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("one\n")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-m", "initial")
    (tmp_path / ".agentflow" / "runs").mkdir(parents=True)
    return tracked


def test_report_tracks_files_and_can_restore_clean_start(tmp_path):
    tracked = repository(tmp_path)
    before = snapshot(tmp_path)
    tracked.write_text("one\ntwo\n")
    created = tmp_path / "created.txt"
    created.write_text("new\n")

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
    assert tracked.read_text() == "one\n"
    assert not created.exists()


def test_report_does_not_offer_rejection_over_preexisting_work(tmp_path):
    tracked = repository(tmp_path)
    tracked.write_text("existing change\n")
    before = snapshot(tmp_path)
    tracked.write_text("agent change\n")

    report, rejection = build_report(
        tmp_path, before, "run-2", concurrent_run=False
    )

    assert report["preexisting_dirty"] is True
    assert report["rejectable"] is False
    assert rejection is None


def test_reject_can_restore_only_selected_files(tmp_path):
    tracked = repository(tmp_path)
    before = snapshot(tmp_path)
    tracked.write_text("one\ntwo\n")
    created = tmp_path / "created.txt"
    created.write_text("new\n")
    _, rejection = build_report(
        tmp_path, before, "run-selected", concurrent_run=False
    )

    restored, _ = reject(
        tmp_path,
        rejection,
        selected_files=["created.txt"],
    )

    assert restored is True
    assert tracked.read_text() == "one\ntwo\n"
    assert not created.exists()


def test_reject_can_restore_selected_tracked_file(tmp_path):
    tracked = repository(tmp_path)
    before = snapshot(tmp_path)
    tracked.write_text("one\ntwo\n")
    created = tmp_path / "created.txt"
    created.write_text("new\n")
    _, rejection = build_report(
        tmp_path, before, "run-selected-tracked", concurrent_run=False
    )

    restored, _ = reject(
        tmp_path,
        rejection,
        selected_files=["tracked.txt"],
    )

    assert restored is True
    assert tracked.read_text() == "one\n"
    assert created.read_text() == "new\n"


def test_unchanged_preexisting_files_are_not_attributed_to_run(tmp_path):
    tracked = repository(tmp_path)
    tracked.write_text("existing change\n")
    untracked = tmp_path / "existing-output.txt"
    untracked.write_text("existing output\n")
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
    (tmp_path / ".git" / "FETCH_HEAD").write_text("same remote commit\n")

    report, _ = build_report(
        tmp_path, before, "run-4", concurrent_run=False
    )

    assert report["fetch_observed"] is True
    assert report["origin_dev_before"] == report["origin_dev_after"]
