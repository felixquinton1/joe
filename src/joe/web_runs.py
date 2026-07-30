from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .capabilities import cached_provider_capabilities, select_model
from .approvals import ApprovalStore
from .conversations import ConversationStore, FREE_PROJECT_ID
from .files import FileLibrary
from .git_review import GitSnapshot, build_report, reject, snapshot
from .models import Intent, Mode, Route
from .orchestrator import Orchestrator
from .router import _routing_text
from .routing import resolve_route
from .tasks import TaskStore
from .usage import cached_usage_status, usage_status
from .worktrees import Worktree, WorktreeError, WorktreeManager


class ActiveConversationError(RuntimeError):
    """Raised when a conversation already owns an active run."""


def _conversation_backup_path(project: Path) -> Path:
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    )
    key = hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:16]
    return data_home / "joe" / "backups" / key / "conversations.json"


@dataclass
class LiveRun:
    run_id: str
    request: str
    conversation_id: str
    workspace: Path | None = None
    base_workspace: Path | None = None
    isolated_worktree: bool = False
    branch: str | None = None
    base_commit: str | None = None
    attachments: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(default_factory=threading.Condition)
    git_before: GitSnapshot | None = None
    track_changes: bool = False
    finished_at: float | None = None

    def emit(self, event: dict[str, Any]) -> None:
        with self.condition:
            self.events.append(
                {
                    "at": time.time(),
                    **event,
                    "event_id": len(self.events) + 1,
                }
            )
            self.condition.notify_all()


class RunManager:
    LIVE_RUN_TTL_SECONDS = 300
    MAX_COMPLETED_RUNS = 50

    def __init__(self, project: Path, *, profile: str = "maintainer"):
        self.project = project.resolve()
        self.profile = profile
        self.orchestrator = Orchestrator(self.project)
        self.orchestrator.memory.ensure()
        self.conversations = ConversationStore(
            self.orchestrator.memory.root,
            self.orchestrator.memory.runs,
            backup_path=_conversation_backup_path(self.project),
        )
        self.conversations.ensure()
        self.tasks = TaskStore(self.orchestrator.memory.root)
        self.tasks.ensure()
        self.files = FileLibrary(self.orchestrator.memory.root)
        self.files.ensure()
        self.approvals = ApprovalStore(self.orchestrator.memory.root)
        self.approvals.ensure()
        for task in self.tasks.list():
            if task.get("status") in {"integrating", "resolving"}:
                if (
                    task.get("isolated")
                    and task.get("workspace")
                    and task.get("branch")
                    and task.get("base_commit")
                ):
                    WorktreeManager(Path(task["base_workspace"])).abort_rebase(
                        self._task_worktree(task)
                    )
                self.tasks.update(
                    task["id"],
                    status="conflict",
                    error=(
                        "Intégration interrompue par un redémarrage. "
                        "Relance-la depuis le panneau des tâches."
                    ),
                )
        self.live: dict[str, LiveRun] = {}
        self.git_rejections: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.integrating: set[str] = set()
        self.compacting: set[str] = set()
        self.pending_path = self.orchestrator.memory.root / "pending_runs.json"
        self._recover_pending()

    def start(
        self,
        request: str,
        conversation_id: str,
        agent: str | None,
        mode: str | None,
        model: str | None,
        effort: str | None,
        execution_mode: str | None,
        attachments: list[str] | None = None,
        *,
        run_id: str | None = None,
        resumed: bool = False,
    ) -> LiveRun:
        run_id = run_id or uuid.uuid4().hex
        workspace, _, _, _ = self._project_scope(conversation_id)
        base_workspace = workspace
        isolated = self._project_uses_worktree(conversation_id)
        with self.lock:
            self._prune_live_locked()
            if any(
                active.conversation_id == conversation_id and not active.done
                for active in self.live.values()
            ):
                raise ActiveConversationError(
                    "Une tâche est déjà active dans cette conversation."
                )
        worktree = None
        if isolated:
            worktree = WorktreeManager(workspace).create(run_id)
            workspace = worktree.path
        conversation = self.conversations.get(conversation_id) or {}
        run = LiveRun(
            run_id,
            request,
            conversation_id,
            workspace=workspace,
            base_workspace=base_workspace,
            isolated_worktree=isolated,
            branch=worktree.branch if worktree else None,
            base_commit=worktree.base_commit if worktree else None,
            attachments=list(attachments or []),
            git_before=snapshot(workspace),
        )
        self.tasks.create(
            run_id,
            request,
            conversation_id,
            str(conversation.get("project_id", "main")),
            workspace=workspace,
            base_workspace=base_workspace,
            isolated=isolated,
            branch=run.branch,
            base_commit=run.base_commit,
        )
        with self.lock:
            self._prune_live_locked()
            if any(
                active.conversation_id == conversation_id and not active.done
                for active in self.live.values()
            ):
                if isolated:
                    WorktreeManager(base_workspace).remove(worktree)
                self.tasks.delete(run_id)
                raise ActiveConversationError(
                    "Une tâche est déjà active dans cette conversation."
                )
            if not resumed:
                self.conversations.append_message(
                    conversation_id,
                    "user",
                    request,
                    run.run_id,
                )
            self.live[run.run_id] = run
            try:
                self._write_pending(
                    run,
                    agent,
                    mode,
                    model,
                    effort,
                    execution_mode,
                )
            except Exception:
                self.live.pop(run.run_id, None)
                if isolated and not resumed:
                    WorktreeManager(base_workspace).remove(worktree)
                self.tasks.update(run_id, status="failed")
                if not resumed:
                    self.conversations.remove_run(conversation_id, run.run_id)
                raise
        if resumed:
            run.emit(
                {
                    "type": "recovered",
                    "message": "Tâche relancée après le redémarrage de Joe",
                }
            )
        thread = threading.Thread(
            target=self._execute,
            args=(run, agent, mode, model, effort, execution_mode),
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            with self.lock:
                self.live.pop(run.run_id, None)
            self._remove_pending(run.run_id)
            if (
                not resumed
                and run.isolated_worktree
                and run.base_workspace
                and run.workspace
            ):
                WorktreeManager(run.base_workspace).remove(
                    self._task_worktree(self.tasks.get(run.run_id) or {})
                )
            self.tasks.update(run.run_id, status="failed")
            if not resumed:
                self.conversations.remove_run(conversation_id, run.run_id)
            raise
        return run

    def requires_full_access_approval(
        self,
        request: str,
        conversation_id: str,
        agent: str | None,
        mode: str | None,
        execution_mode: str | None,
    ) -> bool:
        _, _, _, project_execution_mode = self._project_scope(conversation_id)
        forced_mode = Mode(mode) if mode else None
        route = self.orchestrator.router.route(
            request,
            forced_agent=agent,
            forced_mode=forced_mode,
            previous_provider=self.conversations.previous_provider(
                conversation_id
            ),
        )
        resolved = _resolve_execution_mode(
            execution_mode,
            project_execution_mode,
            route,
            request,
        )
        return resolved == "danger-full-access"

    def _execute(
        self,
        run: LiveRun,
        agent: str | None,
        mode: str | None,
        model: str | None,
        effort: str | None,
        execution_mode: str | None,
    ) -> None:
        try:
            (
                workspace,
                additional_roots,
                remote_access,
                project_execution_mode,
            ) = self._project_scope(run.conversation_id)
            workspace = run.workspace or workspace
            conversation = self.conversations.get(run.conversation_id) or {}
            project_id = str(conversation.get("project_id", FREE_PROJECT_ID))
            attachments = self.files.resolve(run.attachments, project_id)
            attachment_roots = tuple(
                sorted(
                    {Path(item["path"]).parent for item in attachments},
                    key=str,
                )
            )
            orchestrator = Orchestrator(
                workspace,
                additional_roots=additional_roots + attachment_roots,
                remote_access=remote_access,
            )
            routing_started = time.monotonic()
            forced_mode = Mode(mode) if mode else None
            decision = resolve_route(
                orchestrator.router,
                run.request,
                cached_usage_status(),
                forced_agent=agent,
                forced_mode=forced_mode,
                previous_provider=self.conversations.previous_provider(
                    run.conversation_id
                ),
            )
            route = decision.route
            quota_admission = decision.quota_admission
            execution_mode = _resolve_execution_mode(
                execution_mode,
                project_execution_mode,
                route,
                run.request,
            )
            run.track_changes = (
                (
                    route.intent is Intent.MODIFY
                    and route.mode is not Mode.CONSENSUS
                )
                or _write_enabled(execution_mode)
            )
            if _complex_request(run.request, route):
                effort = effort or "high"
                model = model or select_model(route.primary, complex_request=True)
            elif route.mode is Mode.FAST:
                effort = effort or "low"
                model = model or select_model(route.primary, complex_request=False)
            if "health-check" in route.reason:
                effort = effort or "low"
                if route.primary == "gemini":
                    model = model or "gemini-3-flash-preview"
            run.emit(
                {
                    "type": "route",
                    "mode": route.mode.value,
                    "intent": route.intent.value,
                    "primary": route.primary,
                    "reviewer": route.reviewer,
                    "reason": route.reason,
                    "model": model,
                    "effort": effort,
                    "execution_mode": execution_mode,
                    "routing_ms": round(
                        (time.monotonic() - routing_started) * 1000
                    ),
                    "health_check": "health-check" in route.reason,
                    "profile": self.profile,
                }
            )
            self.tasks.update(
                run.run_id,
                provider=route.primary,
                model=model,
                mode=route.mode.value,
            )
            if quota_admission:
                run.emit({"type": "quota_admission", **quota_admission})
                if quota_admission.get("blocked"):
                    raise RuntimeError(quota_admission["message"])
            run.emit(
                {
                    "type": "evidence",
                    "status": "inferred",
                    "label": "Routage",
                    "detail": (
                        f"{route.mode.value} vers {route.primary}, décidé par "
                        "les règles locales"
                    ),
                }
            )
            if route.intent is Intent.MODIFY and not os.access(workspace, os.W_OK):
                raise PermissionError(
                    "Le projet n'est pas accessible en écriture. "
                    "Relance Joe depuis un montage inscriptible."
                )
            evidence_context = (
                self.conversations.context(run.conversation_id)
                + "\n\n# Evidence policy\n"
                "Never claim that a file, command, remote state, or URL was checked "
                "unless the corresponding tool completed successfully. In reports, "
                "separate material claims as Vérifié, Inféré, or Refusé. A blocked "
                "sandbox or permission check is Refusé, never Vérifié.\n"
                f"The active workspace is {workspace}. Use only instructions and "
                "skills whose repository scope matches this workspace. Never apply "
                "a skill belonging to another project."
                + self._attachment_context(attachments)
            )
            response, log = orchestrator.execute(
                run.request,
                route,
                model=model,
                effort=effort,
                execution_mode=execution_mode,
                extra_context=evidence_context,
                cancel_event=run.cancel_event,
                on_event=lambda event: self._emit_run_event(run, event),
            )
            git_report = self._capture_git_report(run)
            self._update_task_git(run, git_report)
            self._deliver_if_enabled(run, git_report)
            self.conversations.append_message(
                run.conversation_id,
                "assistant",
                response,
                run.run_id,
                provider=route.primary,
                git_report=git_report,
                run_summary=_run_summary(run),
            )
            run.emit({"type": "git_report", "run_id": run.run_id, **git_report})
            run.emit(
                {
                    "type": "complete",
                    "response": response,
                    "log": str(log),
                }
            )
            self._schedule_compaction(run.conversation_id, orchestrator)
            self.tasks.update(
                run.run_id,
                status=(
                    "review"
                    if run.isolated_worktree and git_report.get("files")
                    else "completed"
                ),
            )
        except Exception as exc:
            git_report = self._capture_git_report(run)
            self._update_task_git(run, git_report)
            if run.cancel_event.is_set():
                self.conversations.remove_run(run.conversation_id, run.run_id)
                run.emit(
                    {"type": "git_report", "run_id": run.run_id, **git_report}
                )
                run.emit({"type": "cancelled"})
                self.tasks.update(run.run_id, status="cancelled")
                return
            self.conversations.append_message(
                run.conversation_id,
                "assistant",
                f"Erreur : {exc}",
                run.run_id,
                git_report=git_report,
                run_summary=_run_summary(run),
            )
            run.emit({"type": "git_report", "run_id": run.run_id, **git_report})
            run.emit({"type": "error", "message": str(exc)})
            self.tasks.update(run.run_id, status="failed", error=str(exc))
        finally:
            self._remove_pending(run.run_id)
            with run.condition:
                run.done = True
                run.finished_at = time.time()
                run.condition.notify_all()
            with self.lock:
                self._prune_live_locked()
            cleanup = threading.Timer(
                self.LIVE_RUN_TTL_SECONDS,
                self._expire_live_run,
                args=(run.run_id, run.finished_at),
            )
            cleanup.daemon = True
            cleanup.start()

    def _emit_run_event(self, run: LiveRun, event: dict[str, Any]) -> None:
        run.emit(event)
        if event.get("type") == "provider_end":
            ok = bool(event.get("ok"))
            run.emit(
                {
                    "type": "evidence",
                    "status": "verified" if ok else "refused",
                    "label": str(event.get("provider", "fournisseur")),
                    "detail": (
                        "Exécution terminée avec succès"
                        if ok
                        else f"Accès ou exécution interrompu : {event.get('error') or 'erreur'}"
                    ),
                }
            )
        if event.get("type") != "provider_end" or event.get("error") != "quota":
            return
        facade = _web_facade()
        config = self.orchestrator.memory.config()
        run.emit(
            facade.build_quota_notice(
                str(event["provider"]),
                facade.usage_status(),
                facade.provider_capabilities(),
                config.get("fallbacks", {}),
            )
        )

    def _capture_git_report(self, run: LiveRun) -> dict[str, Any]:
        if not run.track_changes:
            return {
                "available": False,
                "reason": "Requête exécutée en lecture seule",
            }
        try:
            with self.lock:
                concurrent_run = any(
                    other.run_id != run.run_id and not other.done
                    for other in self.live.values()
                )
            workspace = run.workspace or self.project
            facade = _web_facade()
            report, rejection = facade.build_report(
                workspace,
                run.git_before or facade.snapshot(workspace),
                run.run_id,
                concurrent_run=concurrent_run,
            )
            if rejection:
                rejection["_workspace"] = str(workspace)
                with self.lock:
                    self.git_rejections[run.run_id] = rejection
            return report
        except (OSError, subprocess.SubprocessError):
            return {"available": False}

    def _deliver_if_enabled(
        self,
        run: LiveRun,
        report: dict[str, Any],
    ) -> None:
        conversation = self.conversations.get(run.conversation_id) or {}
        project = self.conversations.get_project(
            str(conversation.get("project_id", "main"))
        ) or {}
        if not project.get("auto_commit_push"):
            return
        if run.isolated_worktree:
            report["delivery"] = {
                "status": "blocked",
                "message": (
                    "Livraison automatique suspendue : ce run est dans un worktree "
                    "isolé. Examine ou fusionne le worktree explicitement."
                ),
            }
            return
        workspace = run.workspace or self.project
        delivery = _web_facade().deliver(
            workspace,
            report,
            "chore: apply validated Joe changes",
        )
        report["delivery"] = delivery
        if delivery.get("status") not in {"pushed", "committed"}:
            return
        report["rejectable"] = False
        report["reject_reason"] = "Modifications déjà commitées automatiquement."
        with self.lock:
            rejection = self.git_rejections.pop(run.run_id, None)
        if not rejection:
            return
        for path in (
            rejection.get("patch"),
            self.orchestrator.memory.runs / f"{run.run_id}.reject.json",
        ):
            try:
                Path(path).unlink(missing_ok=True)
            except (OSError, TypeError):
                pass

    def cancel(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        if not run or run.done:
            return False
        run.cancel_event.set()
        return True

    def reject_changes(
        self,
        run_id: str,
        selected_files: list[str] | None = None,
    ) -> tuple[bool, str]:
        with self.lock:
            rejection = self.git_rejections.get(run_id)
        review_path = self.orchestrator.memory.runs / f"{run_id}.reject.json"
        if not rejection and run_id.replace("-", "").isalnum():
            try:
                rejection = json.loads(review_path.read_text())
            except (OSError, json.JSONDecodeError):
                rejection = None
        if not rejection:
            return False, "Ces modifications ne peuvent pas être restaurées automatiquement."
        if self.active_runs():
            return False, "Attends la fin des autres tâches avant de restaurer."
        restored, message = reject(
            _existing_directory(rejection.get("_workspace")) or self.project,
            rejection,
            selected_files=selected_files,
        )
        if restored:
            with self.lock:
                self.git_rejections.pop(run_id, None)
            try:
                Path(rejection["patch"]).unlink(missing_ok=True)
                review_path.unlink(missing_ok=True)
            except OSError:
                pass
        return restored, message

    def get_run(self, run_id: str) -> LiveRun | None:
        with self.lock:
            self._prune_live_locked()
            return self.live.get(run_id)

    def active_runs(self) -> list[LiveRun]:
        with self.lock:
            self._prune_live_locked()
            return [run for run in self.live.values() if not run.done]

    def has_active_conversation(self, conversation_id: str) -> bool:
        return any(
            run.conversation_id == conversation_id
            for run in self.active_runs()
        )

    def _prune_live_locked(self, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        completed = sorted(
            (run for run in self.live.values() if run.done),
            key=lambda run: run.finished_at or timestamp,
            reverse=True,
        )
        removable = {
            run.run_id
            for index, run in enumerate(completed)
            if index >= self.MAX_COMPLETED_RUNS
            or (
                run.finished_at is not None
                and timestamp - run.finished_at >= self.LIVE_RUN_TTL_SECONDS
            )
        }
        for run_id in removable:
            self.live.pop(run_id, None)

    def _expire_live_run(
        self,
        run_id: str,
        finished_at: float | None,
    ) -> None:
        with self.lock:
            run = self.live.get(run_id)
            if run and run.done and run.finished_at == finished_at:
                self.live.pop(run_id, None)

    def _project_scope(
        self, conversation_id: str
    ) -> tuple[Path, tuple[Path, ...], bool, str]:
        conversation = self.conversations.get(conversation_id) or {}
        project = self.conversations.get_project(
            str(conversation.get("project_id", "main"))
        ) or {}
        configured_workspace = project.get("workspace_root")
        workspace = _existing_directory(configured_workspace)
        if configured_workspace and not workspace:
            raise ValueError(
                f"Racine de projet inaccessible : {configured_workspace}"
            )
        if str(conversation.get("project_id")) == FREE_PROJECT_ID and not workspace:
            workspace = self._free_workspace()
        workspace = workspace or self.project
        roots_list = []
        for value in project.get("additional_roots", []):
            root = _existing_directory(value)
            if not root:
                raise ValueError(f"Racine autorisée inaccessible : {value}")
            if root != workspace:
                roots_list.append(root)
        roots = tuple(roots_list)
        return (
            workspace,
            roots,
            (
                str(conversation.get("settings", {}).get("web_access", "on"))
                != "off"
                and bool(project.get("web_access", True))
            ),
            str(project.get("default_execution_mode", "")),
        )

    def _project_uses_worktree(self, conversation_id: str) -> bool:
        conversation = self.conversations.get(conversation_id) or {}
        project = self.conversations.get_project(conversation.get("project_id", "main")) or {}
        return bool(project.get("isolated_worktrees"))

    def _free_workspace(self) -> Path:
        data_home = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
        key = hashlib.sha256(str(self.project).encode()).hexdigest()[:16]
        workspace = data_home / "joe" / "workspaces" / key / "free"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    @staticmethod
    def _attachment_context(items: list[dict[str, Any]]) -> str:
        if not items:
            return ""
        lines = [
            "\n\n# Attached files",
            "The user explicitly attached these project-scoped local files:",
        ]
        for item in items:
            path = Path(str(item["path"]))
            lines.append(
                f"- {item['name']} ({item.get('content_type')}, "
                f"{item.get('size', 0)} bytes): {path}"
            )
            content_type = str(item.get("content_type", ""))
            if (
                content_type.startswith("text/")
                or path.suffix.lower() in {
                    ".md", ".txt", ".py", ".json", ".yaml", ".yml",
                    ".toml", ".csv", ".js", ".ts", ".html", ".css",
                }
            ) and int(item.get("size", 0)) <= 100_000:
                try:
                    lines.extend(
                        [
                            f"\n## {item['name']}",
                            "```",
                            path.read_text(errors="replace"),
                            "```",
                        ]
                    )
                except OSError:
                    lines.append("  (content unavailable; inspect the path)")
        return "\n".join(lines)

    def _schedule_compaction(
        self,
        conversation_id: str,
        orchestrator: Orchestrator,
    ) -> None:
        config = orchestrator.memory.config().get("semantic_compaction", {})
        if not config.get("enabled", True):
            return
        candidate = self.conversations.compaction_candidate(
            conversation_id,
            threshold_chars=int(config.get("threshold_chars", 30000)),
            keep_recent=int(config.get("keep_recent_messages", 8)),
        )
        if not candidate:
            return
        with self.lock:
            if conversation_id in self.compacting:
                return
            self.compacting.add(conversation_id)
        threading.Thread(
            target=self._compact_conversation,
            args=(conversation_id, orchestrator, candidate),
            daemon=True,
        ).start()

    def _compact_conversation(
        self,
        conversation_id: str,
        orchestrator: Orchestrator,
        candidate: dict[str, Any],
    ) -> None:
        try:
            config = orchestrator.memory.config().get("semantic_compaction", {})
            provider = str(config.get("provider", "gemini"))
            prompt = (
                "Compact this conversation for handoff between coding agents. "
                "Preserve user goals, verified facts, decisions, rejected options, "
                "open tasks, commands, and relevant file paths. Do not invent facts. "
                "Return concise Markdown only. Do not use tools or inspect files.\n\n"
                f"# Previous summary\n{candidate['previous_summary'] or 'None'}\n\n"
                f"# New transcript\n{candidate['transcript']}"
            )
            result = orchestrator.providers[provider].run(
                prompt,
                orchestrator.project,
                Intent.ANSWER,
                60,
                model=str(config.get("model", "gemini-3-flash-preview")),
                execution_mode="plan",
            )
            if result.ok and result.stdout.strip():
                self.conversations.save_compaction(
                    conversation_id,
                    result.stdout,
                    int(candidate["message_count"]),
                )
        finally:
            with self.lock:
                self.compacting.discard(conversation_id)

    def _write_pending(
        self,
        run: LiveRun,
        agent: str | None,
        mode: str | None,
        model: str | None,
        effort: str | None,
        execution_mode: str | None,
    ) -> None:
        pending = self._read_pending()
        pending[run.run_id] = {
            "request": run.request,
            "conversation_id": run.conversation_id,
            "agent": agent,
            "mode": mode,
            "model": model,
            "effort": effort,
            "execution_mode": execution_mode,
            "attachments": run.attachments,
        }
        _atomic_json(self.pending_path, pending)

    def _remove_pending(self, run_id: str) -> None:
        with self.lock:
            pending = self._read_pending()
            if pending.pop(run_id, None) is not None:
                _atomic_json(self.pending_path, pending)

    def _read_pending(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.pending_path.read_text())
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _recover_pending(self) -> None:
        for run_id, item in self._read_pending().items():
            if not self.conversations.get(str(item.get("conversation_id", ""))):
                continue
            self.start(
                str(item.get("request", "")),
                str(item["conversation_id"]),
                item.get("agent"),
                item.get("mode"),
                item.get("model"),
                item.get("effort"),
                item.get("execution_mode"),
                item.get("attachments") or [],
                run_id=run_id,
                resumed=True,
            )

    def history(self) -> list[dict[str, Any]]:
        self.orchestrator.memory.ensure()
        items = []
        for path in sorted(self.orchestrator.memory.runs.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            items.append(
                {
                    "id": path.stem,
                    "request": payload.get("request", ""),
                    "route": payload.get("route", {}),
                    "final": payload.get("final", ""),
                }
            )
        return items[:100]

    def history_item(self, run_id: str) -> dict[str, Any] | None:
        if not run_id.replace(".", "").isalnum():
            return None
        path = self.orchestrator.memory.runs / f"{run_id}.json"
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def list_tasks(self) -> list[dict[str, Any]]:
        return [
            {**task, "pipeline": _task_pipeline(task)}
            for task in self.tasks.list()
        ]

    def search(
        self,
        query: str,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = " ".join(query.casefold().split())
        if not needle:
            return []
        results = [
            {**item, "type": "conversation"}
            for item in self.conversations.search(query, project_id)
        ]
        for task in self.tasks.list():
            if project_id and task.get("project_id") != project_id:
                continue
            haystack = f"{task.get('title', '')}\n{task.get('request', '')}"
            if needle in haystack.casefold():
                results.append(
                    {
                        "type": "task",
                        "task_id": task["id"],
                        "conversation_id": task["conversation_id"],
                        "project_id": task["project_id"],
                        "title": task["title"],
                        "snippet": task["request"][:300],
                        "updated_at": task["updated_at"],
                    }
                )
        project_ids = (
            [project_id]
            if project_id
            else [
                str(project["id"])
                for project in self.conversations.list_projects()
            ]
        )
        for identifier in project_ids:
            for item in self.files.list(identifier):
                if needle in str(item["name"]).casefold():
                    results.append(
                        {
                            "type": "file",
                            "file_id": item["id"],
                            "project_id": identifier,
                            "title": item["name"],
                            "snippet": (
                                f"{item.get('content_type')} · "
                                f"{item.get('size', 0)} octets"
                            ),
                            "updated_at": item["created_at"],
                        }
                    )
        return sorted(
            results,
            key=lambda item: float(item.get("updated_at", 0)),
            reverse=True,
        )[:100]

    def task_diff(self, task_id: str) -> dict[str, Any] | None:
        task = self.tasks.get(task_id)
        if not task:
            return None
        if not task.get("isolated"):
            return {
                "files": [],
                "insertions": task.get("insertions", 0),
                "deletions": task.get("deletions", 0),
                "patch_preview": "",
                "message": "Cette tâche n’utilise pas de worktree isolé.",
            }
        return WorktreeManager(Path(task["base_workspace"])).diff(
            self._task_worktree(task)
        )

    def integrate_task(self, task_id: str) -> dict[str, Any]:
        task = self.tasks.get(task_id)
        if not task:
            raise KeyError(task_id)
        if task.get("status") == "running":
            raise WorktreeError("La tâche est encore en cours.")
        if not task.get("isolated"):
            raise WorktreeError("Cette tâche n’utilise pas de worktree isolé.")
        if task.get("status") == "integrated" or not task.get("workspace"):
            raise WorktreeError("Cette tâche est déjà intégrée.")
        with self.lock:
            if task_id in self.integrating:
                raise WorktreeError("L’intégration de cette tâche est déjà en cours.")
            self.integrating.add(task_id)
        updated = self.tasks.update(
            task_id,
            status="integrating",
            error=None,
        )
        thread = threading.Thread(
            target=self._integrate_task_worker,
            args=(task_id,),
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            with self.lock:
                self.integrating.discard(task_id)
            self.tasks.update(
                task_id,
                status="conflict",
                error="Impossible de démarrer l’intégration.",
            )
            raise
        return updated or task

    def delete_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task.get("status") in {"running", "integrating", "resolving"}:
            raise WorktreeError("Interromps la tâche avant de la supprimer.")
        if task.get("isolated") and task.get("workspace"):
            WorktreeManager(Path(task["base_workspace"])).remove(
                self._task_worktree(task)
            )
        return self.tasks.delete(task_id)

    def _integrate_task_worker(self, task_id: str) -> None:
        try:
            task = self.tasks.get(task_id)
            if not task:
                return
            manager = WorktreeManager(Path(task["base_workspace"]))
            commit = manager.integrate(
                self._task_worktree(task),
                f"chore: integrate Joe task {task['title']}",
                resolver=lambda worktree, conflicts, attempt: (
                    self._resolve_task_conflicts(
                        task_id,
                        worktree,
                        conflicts,
                        attempt,
                    )
                ),
            )
            self.tasks.update(
                task_id,
                status="integrated",
                integrated_commit=commit,
                workspace=None,
                error=None,
            )
        except Exception as error:
            self.tasks.update(
                task_id,
                status="conflict",
                error=str(error),
            )
        finally:
            with self.lock:
                self.integrating.discard(task_id)

    def _resolve_task_conflicts(
        self,
        task_id: str,
        worktree: Worktree,
        conflicts: list[str],
        attempt: int,
    ) -> None:
        task = self.tasks.get(task_id)
        if not task:
            raise WorktreeError("Tâche introuvable pendant la résolution.")
        self.tasks.update(
            task_id,
            status="resolving",
            error=(
                f"Résolution automatique {attempt}/3 : "
                + ", ".join(conflicts)
            ),
        )
        conversation_id = str(task["conversation_id"])
        _, additional_roots, remote_access, _ = self._project_scope(
            conversation_id
        )
        orchestrator = Orchestrator(
            worktree.path,
            additional_roots=additional_roots,
            remote_access=remote_access,
        )
        provider = str(task.get("provider") or "codex")
        route = Route(
            Intent.MODIFY,
            Mode.FAST,
            provider,
            reason="worktree-conflict-resolution",
        )
        prompt = (
            "Résous les conflits Git actuellement présents dans ce worktree. "
            "Préserve à la fois les changements déjà intégrés dans la branche "
            "principale et l’objectif de la tâche ci-dessous. Inspecte chaque "
            "fichier en conflit, retire tous les marqueurs, puis lance les tests "
            "ciblés pertinents. Ne lance ni git rebase, ni git commit, ni git "
            "merge : Joe terminera l’opération. Modifie uniquement les fichiers "
            "nécessaires.\n\n"
            f"Objectif de la tâche :\n{task['request']}\n\n"
            "Fichiers en conflit :\n- "
            + "\n- ".join(conflicts)
        )

        def on_event(event: dict[str, Any]) -> None:
            if event.get("type") == "provider_start":
                self.tasks.update(
                    task_id,
                    provider=event.get("provider"),
                    model=event.get("model"),
                )

        orchestrator.execute(
            prompt,
            route,
            effort="high",
            execution_mode="workspace-write",
            extra_context=(
                "This is a bounded conflict-resolution pass. A successful "
                "answer requires actual edits in the conflicted worktree and "
                "relevant validation, not a proposed plan."
            ),
            on_event=on_event,
        )
        self.tasks.update(task_id, status="integrating", error=None)

    def _update_task_git(
        self,
        run: LiveRun,
        report: dict[str, Any],
    ) -> None:
        files = report.get("files") or []
        self.tasks.update(
            run.run_id,
            files=len(files),
            insertions=report.get("insertions", 0),
            deletions=report.get("deletions", 0),
        )

    @staticmethod
    def _task_worktree(task: dict[str, Any]) -> Worktree:
        return Worktree(
            Path(str(task["workspace"])),
            str(task["branch"]),
            str(task["base_commit"]),
        )


def build_quota_notice(
    provider: str,
    usage: list[dict[str, Any]],
    capabilities: dict[str, Any],
    fallbacks: dict[str, list[str]],
) -> dict[str, Any]:
    provider_usage = next(
        (item for item in usage if item.get("provider") == provider),
        {},
    )
    alternatives = []
    for name in fallbacks.get(provider, []):
        capability = capabilities.get(name, {})
        if not capability.get("available"):
            continue
        alternatives.append(
            {
                "provider": name,
                "models": [
                    model.get("label", model.get("id"))
                    for model in capability.get("models", [])
                    if model.get("label") or model.get("id")
                ],
            }
        )
    return {
        "type": "quota_notice",
        "provider": provider,
        "windows": provider_usage.get("windows", []),
        "usage_message": provider_usage.get("message"),
        "alternatives": alternatives,
        "automatic_fallback": bool(alternatives),
    }


def _complex_request(request: str, route: Route) -> bool:
    markers = {
        "architecture",
        "analyse",
        "audit",
        "debug",
        "implémente",
        "implémenter",
        "migration",
        "refactor",
        "scientifique",
        "expérimental",
    }
    request = _routing_text(request)
    words = set(request.lower().replace(",", " ").replace(".", " ").split())
    return (
        route.mode is not Mode.FAST
        or route.intent is Intent.MODIFY
        and (len(request) >= 240 or bool(words & markers))
        or route.intent is Intent.ANALYZE
        and bool(words & markers)
    )


def _latest_model(provider: str) -> str | None:
    if provider not in {"codex", "claude"}:
        return None
    models = cached_provider_capabilities().get(provider, {}).get("models", [])
    return str(models[0]["id"]) if models else None


def _existing_directory(value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser().resolve()
    return path if path.is_dir() else None


def _write_enabled(execution_mode: str | None) -> bool:
    return execution_mode in {
        "workspace-write",
        "danger-full-access",
        "acceptEdits",
        "auto_edit",
        "modify",
    }


def _resolve_execution_mode(
    explicit: str | None,
    project_default: str | None,
    route: Route,
    request: str,
) -> str | None:
    if route.mode is Mode.CONSENSUS:
        return None
    if explicit:
        return explicit
    if route.intent is Intent.MODIFY or _operational_validation(request):
        return project_default or None
    return None


def _run_summary(run: LiveRun) -> dict[str, Any]:
    route = None
    quota_admission = None
    workflow: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    for event in run.events:
        event_type = event.get("type")
        if event_type == "route":
            route = {
                key: event.get(key)
                for key in (
                    "mode", "intent", "primary", "reviewer", "reason",
                    "model", "effort", "execution_mode", "profile",
                )
            }
        elif event_type == "quota_admission":
            quota_admission = {
                key: event.get(key)
                for key in ("level", "message", "forced")
            }
        elif event_type == "workflow_update":
            stage = str(event.get("stage", ""))
            workflow[stage] = {
                **workflow.get(stage, {}),
                **{
                    key: event.get(key)
                    for key in (
                        "type", "mode", "stage", "provider", "label",
                        "status", "content",
                    )
                    if event.get(key) is not None
                },
            }
        elif event_type == "provider_fallback":
            for item in workflow.values():
                if (
                    item.get("status") == "running"
                    and item.get("provider") == event.get("provider")
                ):
                    item["fallback_from"] = event.get("provider")
                    item["provider"] = event.get("fallback")
        elif event_type == "provider_start":
            attempts.append(
                {
                    "provider": event.get("provider"),
                    "model": event.get("model"),
                    "effort": event.get("effort"),
                    "status": "running",
                }
            )
            for item in workflow.values():
                if (
                    item.get("status") == "running"
                    and item.get("provider") == event.get("provider")
                ):
                    item["model"] = event.get("model")
                    item["effort"] = event.get("effort")
        elif event_type == "usage":
            for attempt in reversed(attempts):
                if attempt.get("provider") == event.get("provider") and attempt.get("status") == "running":
                    attempt["usage"] = {
                        key: event.get(key)
                        for key in (
                            "input_tokens", "output_tokens", "cache_read_tokens",
                            "cache_creation_tokens", "reasoning_tokens", "cost_usd",
                            "cost_status", "source",
                        )
                    }
                    break
        elif event_type == "provider_end":
            for attempt in reversed(attempts):
                if (
                    attempt["status"] == "running"
                    and attempt["provider"] == event.get("provider")
                ):
                    attempt["status"] = (
                        "complete" if event.get("ok") else "failed"
                    )
                    attempt["error"] = event.get("error")
                    break
    return {
        "route": route,
        "quota_admission": quota_admission,
        "workflow": list(workflow.values()),
        "attempts": attempts,
        "isolated_worktree": run.isolated_worktree,
        "workspace": str(run.workspace) if run.workspace else None,
        "base_workspace": str(run.base_workspace) if run.base_workspace else None,
    }


def _task_pipeline(task: dict[str, Any]) -> list[dict[str, str]]:
    status = str(task.get("status", "running"))
    integrated = status == "integrated"
    failed = status in {"failed", "conflict", "cancelled"}
    return [
        {"id": "request", "label": "Demande", "status": "complete"},
        {
            "id": "implementation",
            "label": "Réalisation",
            "status": (
                "failed"
                if failed and status != "conflict"
                else "running"
                if status == "running"
                else "complete"
            ),
        },
        {
            "id": "validation",
            "label": "Validation",
            "status": (
                "running"
                if status in {"integrating", "resolving"}
                else "failed"
                if status == "conflict"
                else "complete"
                if status in {"review", "completed", "integrated"}
                else "pending"
            ),
        },
        {
            "id": "review",
            "label": "Diff",
            "status": (
                "complete"
                if integrated or status == "completed"
                else "waiting"
                if status in {"review", "conflict"}
                else "pending"
            ),
        },
        {
            "id": "delivery",
            "label": "Livraison",
            "status": "complete" if integrated else "pending",
        },
    ]


def _operational_validation(request: str) -> bool:
    lower = request.lower()
    markers = (
        "audit",
        "pytest",
        "suite de tests",
        "exécute les tests",
        "lance les tests",
        "smoke test",
        "git fetch",
        "fetch ",
        "origin/main",
        "origin/dev",
        "authentification",
    )
    return any(marker in lower for marker in markers)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    os.replace(temporary, path)


def _web_facade():
    from . import web as facade

    return facade
