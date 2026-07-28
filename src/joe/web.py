from __future__ import annotations

import json
import hashlib
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .capabilities import cached_provider_capabilities, provider_capabilities
from .conversations import ConversationStore
from .git_review import GitSnapshot, build_report, reject, snapshot
from .models import Intent, Mode
from .orchestrator import Orchestrator
from .usage import balance_route, cached_usage_status, usage_status


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
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(default_factory=threading.Condition)
    git_before: GitSnapshot | None = None

    def emit(self, event: dict[str, Any]) -> None:
        with self.condition:
            self.events.append({"at": time.time(), **event})
            self.condition.notify_all()


class RunManager:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.orchestrator = Orchestrator(self.project)
        self.orchestrator.memory.ensure()
        self.conversations = ConversationStore(
            self.orchestrator.memory.root,
            self.orchestrator.memory.runs,
            backup_path=_conversation_backup_path(self.project),
        )
        self.conversations.ensure()
        self.live: dict[str, LiveRun] = {}
        self.git_rejections: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
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
        *,
        run_id: str | None = None,
        resumed: bool = False,
    ) -> LiveRun:
        workspace, _, _ = self._project_scope(conversation_id)
        run = LiveRun(
            run_id or uuid.uuid4().hex,
            request,
            conversation_id,
            workspace=workspace,
            git_before=snapshot(workspace),
        )
        if not resumed:
            self.conversations.append_message(
                conversation_id, "user", request, run.run_id
            )
        with self.lock:
            self.live[run.run_id] = run
            self._write_pending(
                run,
                agent,
                mode,
                model,
                effort,
                execution_mode,
            )
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
        thread.start()
        return run

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
            workspace, additional_roots, remote_access = self._project_scope(
                run.conversation_id
            )
            orchestrator = Orchestrator(
                workspace,
                additional_roots=additional_roots,
                remote_access=remote_access,
            )
            routing_started = time.monotonic()
            forced_mode = Mode(mode) if mode else None
            route = orchestrator.router.route(
                run.request,
                forced_agent=agent,
                forced_mode=forced_mode,
                previous_provider=self.conversations.previous_provider(
                    run.conversation_id
                ),
            )
            if not agent:
                route = balance_route(route, cached_usage_status())
            if _complex_request(run.request, route):
                effort = effort or "high"
                model = model or _latest_model(route.primary)
            elif route.mode is Mode.FAST and route.intent is Intent.ANSWER:
                effort = effort or "low"
                model = model or _latest_model(route.primary)
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
                    "routing_ms": round(
                        (time.monotonic() - routing_started) * 1000
                    ),
                    "health_check": "health-check" in route.reason,
                }
            )
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
                "sandbox or permission check is Refusé, never Vérifié."
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
            self.conversations.append_message(
                run.conversation_id,
                "assistant",
                response,
                run.run_id,
                provider=route.primary,
                git_report=git_report,
            )
            run.emit({"type": "git_report", "run_id": run.run_id, **git_report})
            run.emit(
                {
                    "type": "complete",
                    "response": response,
                    "log": str(log),
                }
            )
            self._schedule_compaction(
                run.conversation_id,
                orchestrator,
            )
        except Exception as exc:
            git_report = self._capture_git_report(run)
            if run.cancel_event.is_set():
                self.conversations.remove_run(run.conversation_id, run.run_id)
                run.emit(
                    {"type": "git_report", "run_id": run.run_id, **git_report}
                )
                run.emit({"type": "cancelled"})
                return
            self.conversations.append_message(
                run.conversation_id,
                "assistant",
                f"Erreur : {exc}",
                run.run_id,
                git_report=git_report,
            )
            run.emit({"type": "git_report", "run_id": run.run_id, **git_report})
            run.emit({"type": "error", "message": str(exc)})
        finally:
            self._remove_pending(run.run_id)
            with run.condition:
                run.done = True
                run.condition.notify_all()

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
        config = self.orchestrator.memory.config()
        run.emit(
            build_quota_notice(
                str(event["provider"]),
                usage_status(),
                provider_capabilities(),
                config.get("fallbacks", {}),
            )
        )

    def _capture_git_report(self, run: LiveRun) -> dict[str, Any]:
        try:
            concurrent_run = any(
                other.run_id != run.run_id and not other.done
                for other in self.live.values()
            )
            report, rejection = build_report(
                run.workspace or self.project,
                run.git_before or snapshot(run.workspace or self.project),
                run.run_id,
                concurrent_run=concurrent_run,
            )
            if rejection:
                rejection["_workspace"] = str(run.workspace or self.project)
                self.git_rejections[run.run_id] = rejection
            return report
        except (OSError, subprocess.SubprocessError):
            return {"available": False}

    def cancel(self, run_id: str) -> bool:
        with self.lock:
            run = self.live.get(run_id)
        if not run or run.done:
            return False
        run.cancel_event.set()
        return True

    def reject_changes(
        self,
        run_id: str,
        selected_files: list[str] | None = None,
    ) -> tuple[bool, str]:
        rejection = self.git_rejections.get(run_id)
        review_path = self.orchestrator.memory.runs / f"{run_id}.reject.json"
        if not rejection and run_id.replace("-", "").isalnum():
            try:
                rejection = json.loads(review_path.read_text())
            except (OSError, json.JSONDecodeError):
                rejection = None
        if not rejection:
            return False, "Ces modifications ne peuvent pas être restaurées automatiquement."
        if any(not run.done for run in self.live.values()):
            return False, "Attends la fin des autres tâches avant de restaurer."
        restored, message = reject(
            _existing_directory(rejection.get("_workspace")) or self.project,
            rejection,
            selected_files=selected_files,
        )
        if restored:
            self.git_rejections.pop(run_id, None)
            try:
                Path(rejection["patch"]).unlink(missing_ok=True)
                review_path.unlink(missing_ok=True)
            except OSError:
                pass
        return restored, message

    def _project_scope(
        self, conversation_id: str
    ) -> tuple[Path, tuple[Path, ...], bool]:
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
        workspace = workspace or self.project
        roots_list = []
        for value in project.get("additional_roots", []):
            root = _existing_directory(value)
            if not root:
                raise ValueError(f"Racine autorisée inaccessible : {value}")
            if root != workspace:
                roots_list.append(root)
        roots = tuple(roots_list)
        return workspace, roots, bool(project.get("remote_access"))

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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    os.replace(temporary, path)

class JoeServer(ThreadingHTTPServer):
    manager: RunManager


class Handler(BaseHTTPRequestHandler):
    server: JoeServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self._asset("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._asset("app.js", "text/javascript; charset=utf-8")
        if path == "/style.css":
            return self._asset("style.css", "text/css; charset=utf-8")
        if path == "/api/status":
            return self._json(
                {
                    "version": __version__,
                    "project": str(self.server.manager.project),
                    "conversation_store": str(
                        self.server.manager.conversations.path
                    ),
                    "conversation_backup": str(
                        self.server.manager.conversations.backup_path
                    ),
                    "providers": ["codex", "claude", "gemini", "copilot"],
                    "modes": ["fast", "review", "consensus"],
                }
            )
        if path == "/api/capabilities":
            return self._json(provider_capabilities())
        if path == "/api/usage":
            force = parse_qs(parsed.query).get("force") == ["1"]
            return self._json(usage_status(force=force))
        if path == "/api/conversations":
            return self._json(self.server.manager.conversations.list())
        if path == "/api/projects":
            return self._json(self.server.manager.conversations.list_projects())
        if path.startswith("/api/projects/"):
            item = self.server.manager.conversations.get_project(
                unquote(path.rsplit("/", 1)[1])
            )
            return self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
        if path.startswith("/api/conversations/"):
            item = self.server.manager.conversations.get(
                unquote(path.rsplit("/", 1)[1])
            )
            return self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
        if path == "/api/history":
            return self._json(self.server.manager.history())
        if path.startswith("/api/history/"):
            item = self.server.manager.history_item(unquote(path.rsplit("/", 1)[1]))
            return self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)
        if path.startswith("/api/events/"):
            return self._events(unquote(path.rsplit("/", 1)[1]))
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/runs/") and path.endswith("/reject"):
            run_id = unquote(path.split("/")[-2])
            size = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(size)) if size else {}
            except json.JSONDecodeError:
                return self._json(
                    {"restored": False, "message": "Requête invalide."},
                    HTTPStatus.BAD_REQUEST,
                )
            selected_files = payload.get("files")
            if selected_files is not None and (
                not isinstance(selected_files, list)
                or not all(isinstance(path, str) for path in selected_files)
            ):
                return self._json(
                    {"restored": False, "message": "Sélection invalide."},
                    HTTPStatus.BAD_REQUEST,
                )
            restored, message = self.server.manager.reject_changes(
                run_id,
                selected_files,
            )
            return self._json(
                {"restored": restored, "message": message},
                HTTPStatus.OK if restored else HTTPStatus.CONFLICT,
            )
        if path.startswith("/api/runs/") and path.endswith("/cancel"):
            run_id = unquote(path.split("/")[-2])
            cancelled = self.server.manager.cancel(run_id)
            return self._json(
                {"cancelled": cancelled},
                HTTPStatus.ACCEPTED if cancelled else HTTPStatus.NOT_FOUND,
            )
        if path == "/api/conversations":
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size)) if size else {}
            return self._json(
                self.server.manager.conversations.create(payload.get("project_id")),
                HTTPStatus.CREATED,
            )
        if path == "/api/projects":
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size)) if size else {}
            return self._json(
                self.server.manager.conversations.create_project(
                    str(payload.get("name", "Nouveau sous-projet"))
                ),
                HTTPStatus.CREATED,
            )
        if path != "/api/runs":
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            request = str(payload.get("request", "")).strip()
            conversation_id = str(payload.get("conversation_id", "")).strip()
            if not request:
                raise ValueError("request is required")
            if not self.server.manager.conversations.get(conversation_id):
                raise ValueError("valid conversation_id is required")
            agent = payload.get("agent") or None
            mode = payload.get("mode") or None
            model = str(payload.get("model", "")).strip() or None
            effort = str(payload.get("effort", "")).strip() or None
            execution_mode = str(payload.get("execution_mode", "")).strip() or None
            if agent not in {None, "codex", "claude", "gemini", "copilot"}:
                raise ValueError("invalid agent")
            if mode not in {None, "fast", "review", "consensus"}:
                raise ValueError("invalid mode")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        run = self.server.manager.start(
            request, conversation_id, agent, mode, model, effort, execution_mode
        )
        self._json({"run_id": run.run_id}, HTTPStatus.ACCEPTED)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        is_conversation = path.startswith("/api/conversations/")
        is_project = path.startswith("/api/projects/")
        if not is_conversation and not is_project:
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)
        identifier = unquote(path.rsplit("/", 1)[1])
        item = (
            self.server.manager.conversations.update(identifier, payload)
            if is_conversation
            else self.server.manager.conversations.update_project(identifier, payload)
        )
        self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/conversations/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        conversation_id = unquote(path.rsplit("/", 1)[1])
        if any(
            run.conversation_id == conversation_id and not run.done
            for run in self.server.manager.live.values()
        ):
            return self._json(
                {"error": "Interromps la tâche avant de supprimer la conversation."},
                HTTPStatus.CONFLICT,
            )
        deleted = self.server.manager.conversations.delete(conversation_id)
        self._json(
            {"deleted": deleted},
            HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND,
        )

    def _events(self, run_id: str) -> None:
        run = self.server.manager.live.get(run_id)
        if not run:
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        index = 0
        try:
            while True:
                with run.condition:
                    if index >= len(run.events) and not run.done:
                        run.condition.wait(timeout=15)
                    events = run.events[index:]
                    index = len(run.events)
                    done = run.done and index >= len(run.events)
                if not events:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                for event in events:
                    data = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                if done:
                    self.close_connection = True
                    return
        except (BrokenPipeError, ConnectionResetError):
            return

    def _asset(self, name: str, content_type: str) -> None:
        data = files("joe").joinpath("web_assets", name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(project: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = JoeServer((host, port), Handler)
    server.manager = RunManager(project)
    server.serve_forever()
