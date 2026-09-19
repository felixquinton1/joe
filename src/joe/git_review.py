from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GitSnapshot:
    available: bool
    branch: str | None
    head: str | None
    origin_dev: str | None
    dirty_files: frozenset[str]
    file_signatures: dict[str, tuple[int, int] | None]
    fetch_head_signature: tuple[int, int] | None

    @property
    def clean(self) -> bool:
        return not self.dirty_files


def snapshot(project: Path) -> GitSnapshot:
    if not (project / ".git").exists():
        return GitSnapshot(False, None, None, None, frozenset(), {}, None)
    status = _git(
        project,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    dirty_files = frozenset(
        path
        for path in _status_paths(status.stdout)
        if not path.startswith(".agentflow/")
    )
    return GitSnapshot(
        True,
        _value(project, "branch", "--show-current"),
        _value(project, "rev-parse", "HEAD"),
        _value(project, "rev-parse", "--verify", "refs/remotes/origin/dev"),
        dirty_files,
        {path: _file_signature(project / path) for path in dirty_files},
        _fetch_head_signature(project),
    )


def build_report(
    project: Path,
    before: GitSnapshot,
    review_id: str,
    *,
    concurrent_run: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    after = snapshot(project)
    if not before.available or not after.available:
        return {"available": False}, None

    base = before.head or "HEAD"
    diff = _git(project, "diff", "--numstat", base, "--")
    files = [
        item
        for item in _numstat(diff.stdout, before.dirty_files)
        if _changed_since_snapshot(project, item["path"], before)
    ]
    untracked = _untracked(project)
    known = {item["path"] for item in files}
    for path in untracked:
        if path in known:
            continue
        if not _changed_since_snapshot(project, path, before):
            continue
        files.append(
            {
                "path": path,
                "insertions": _line_count(project / path),
                "deletions": 0,
                "preexisting": path in before.dirty_files,
                "untracked": True,
            }
        )
    files.sort(key=lambda item: item["path"])

    integrated = None
    if after.origin_dev and after.head:
        integrated = (
            _git(
                project,
                "merge-base",
                "--is-ancestor",
                after.origin_dev,
                after.head,
            ).returncode
            == 0
        )
    rejectable = (
        before.clean
        and before.head == after.head
        and bool(files)
        and not concurrent_run
    )
    rejection = None
    if rejectable:
        patch_path = project / ".agentflow" / "runs" / f"{review_id}.reject.patch"
        patch = _git(project, "diff", "--binary", base, "--").stdout
        patch_path.write_text(patch, encoding="utf-8")
        hashes = {
            path: _file_hash(project / path)
            for path in untracked
            if (project / path).is_file()
        }
        rejection = {
            "patch": str(patch_path),
            "head": after.head,
            "tracked": [
                item["path"] for item in files if not item["untracked"]
            ],
            "untracked": hashes,
        }
        review_path = (
            project / ".agentflow" / "runs" / f"{review_id}.reject.json"
        )
        review_path.write_text(
            json.dumps(rejection, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    report = {
        "available": True,
        "branch": after.branch,
        "head_before": before.head,
        "head_after": after.head,
        "origin_dev_before": before.origin_dev,
        "origin_dev_after": after.origin_dev,
        "fetch_observed": (
            before.fetch_head_signature != after.fetch_head_signature
        ),
        "origin_dev_integrated": integrated,
        "files": files,
        "patch_preview": _git(
            project, "diff", "--no-ext-diff", "--unified=3", base, "--"
        ).stdout[:50_000],
        "insertions": sum(item["insertions"] for item in files),
        "deletions": sum(item["deletions"] for item in files),
        "preexisting_dirty": not before.clean,
        "accepted": True,
        "rejectable": rejectable,
        "reject_reason": (
            None
            if rejectable
            else _reject_reason(before, after, concurrent_run, files)
        ),
    }
    return report, rejection


def reject(
    project: Path,
    rejection: dict[str, Any],
    selected_files: list[str] | None = None,
) -> tuple[bool, str]:
    if _value(project, "rev-parse", "HEAD") != rejection["head"]:
        return False, "Le commit courant a changé depuis la fin de la tâche."
    tracked = rejection.get("tracked", [])
    untracked = rejection.get("untracked", {})
    available = set(tracked) | set(untracked)
    selected = available if selected_files is None else set(selected_files)
    if not selected:
        return False, "Sélectionne au moins un fichier à rejeter."
    if not selected <= available:
        return False, "La sélection contient un fichier inconnu."
    selected_tracked = [path for path in tracked if path in selected]
    selected_untracked = {
        path: file_hash
        for path, file_hash in untracked.items()
        if path in selected
    }
    for relative, expected_hash in selected_untracked.items():
        path = (project / relative).resolve()
        if path.is_file() and _file_hash(path) != expected_hash:
            return False, f"{relative} a changé depuis la fin de la tâche."
    patch = Path(rejection["patch"])
    if selected_tracked and patch.exists() and patch.stat().st_size:
        includes = [
            f"--include={relative}" for relative in selected_tracked
        ]
        check = subprocess.run(
            [
                "git",
                "apply",
                "--check",
                "--reverse",
                *includes,
                str(patch),
            ],
            cwd=project,
            text=True,
            capture_output=True,
            check=False,
        )
        if check.returncode:
            return False, "Certains fichiers ont changé depuis la fin de la tâche."
        applied = subprocess.run(
            ["git", "apply", "--reverse", *includes, str(patch)],
            cwd=project,
            text=True,
            capture_output=True,
            check=False,
        )
        if applied.returncode:
            return False, applied.stderr.strip() or "Échec de restauration Git."
        if selected_tracked:
            subprocess.run(
                [
                    "git",
                    "reset",
                    "--mixed",
                    "HEAD",
                    "--",
                    *selected_tracked,
                ],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
    for relative in selected_untracked:
        path = (project / relative).resolve()
        if not path.is_relative_to(project.resolve()) or not path.is_file():
            continue
        path.unlink()
    count = len(selected)
    return True, (
        f"{count} fichier{'s' if count > 1 else ''} restauré"
        f"{'s' if count > 1 else ''}."
    )


def deliver(
    project: Path,
    report: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    if not report.get("rejectable"):
        return {
            "status": "skipped",
            "message": "Livraison ignorée : attribution Git insuffisante.",
        }
    files = [str(item["path"]) for item in report.get("files", [])]
    if not files:
        return {"status": "skipped", "message": "Aucune modification à livrer."}
    branch = str(report.get("branch") or "")
    if not branch:
        return {"status": "failed", "message": "Branche Git introuvable."}
    staged = _git(project, "add", "--", *files)
    if staged.returncode:
        return {"status": "failed", "message": staged.stderr.strip()}
    committed = _git(project, "commit", "-m", message)
    if committed.returncode:
        _git(project, "reset", "--mixed", "HEAD", "--", *files)
        return {"status": "failed", "message": committed.stderr.strip()}
    commit = _value(project, "rev-parse", "HEAD")
    pushed = _git(project, "push", "origin", branch)
    return {
        "status": "pushed" if pushed.returncode == 0 else "committed",
        "commit": commit,
        "branch": branch,
        "message": (
            "Commit et push effectués."
            if pushed.returncode == 0
            else pushed.stderr.strip() or "Commit créé, push refusé."
        ),
    }


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=project,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            ["git", *args], 124, "", str(exc)
        )


def _value(project: Path, *args: str) -> str | None:
    result = _git(project, *args)
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _status_paths(value: str) -> list[str]:
    entries = value.split("\0")
    paths = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[:2] in {"R ", "C ", "RM", "CM"} and index < len(entries):
            index += 1
    return paths


def _untracked(project: Path) -> list[str]:
    result = _git(
        project, "ls-files", "--others", "--exclude-standard", "-z"
    )
    return [
        item
        for item in result.stdout.split("\0")
        if item and not item.startswith(".agentflow/")
    ]


def _numstat(value: str, preexisting: frozenset[str]) -> list[dict[str, Any]]:
    files = []
    for line in value.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        files.append(
            {
                "path": path,
                "insertions": int(added) if added.isdigit() else 0,
                "deletions": int(deleted) if deleted.isdigit() else 0,
                "preexisting": path in preexisting,
                "untracked": False,
            }
        )
    return files


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _fetch_head_signature(project: Path) -> tuple[int, int] | None:
    git_dir = _value(project, "rev-parse", "--git-dir")
    if not git_dir:
        return None
    path = Path(git_dir)
    if not path.is_absolute():
        path = project / path
    return _file_signature(path / "FETCH_HEAD")


def _changed_since_snapshot(
    project: Path,
    relative: str,
    before: GitSnapshot,
) -> bool:
    if relative not in before.dirty_files:
        return True
    return _file_signature(project / relative) != before.file_signatures.get(relative)


def _reject_reason(
    before: GitSnapshot,
    after: GitSnapshot,
    concurrent_run: bool,
    files: list[dict[str, Any]],
) -> str:
    if not files:
        return "Aucune modification de fichier détectée."
    if not before.clean:
        return "Le dépôt contenait déjà des modifications avant cette tâche."
    if before.head != after.head:
        return "L’historique Git a changé (commit, merge ou pull)."
    if concurrent_run:
        return "Une autre tâche travaillait simultanément sur ce dépôt."
    return "La restauration automatique n’est pas sûre."
