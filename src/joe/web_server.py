from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__
from .auth import LocalAuth, auth_token_path, required_role, rotate_token
from .doctor import doctor_report
from .files import MAX_FILE_BYTES
from .http_utils import RequestBodyError, read_json_body, validate_bind
from .provider_registry import get_provider_catalog, get_provider_names
from .skills import import_skill, list_global_skills, list_skills, promote_skill
from .web_runs import ActiveConversationError, RunManager, _existing_directory
from .worktrees import WorktreeError

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
    "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
    "/app_auth.js": ("app_auth.js", "text/javascript; charset=utf-8"),
    "/app_usage.js": ("app_usage.js", "text/javascript; charset=utf-8"),
    "/app_conversations.js": ("app_conversations.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
API_VERSION = "1.1"


class JoeServer(ThreadingHTTPServer):
    manager: RunManager
    auth = LocalAuth("", "viewer", True)
    auth_path = auth_token_path()


class Handler(BaseHTTPRequestHandler):
    server: JoeServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in _ASSETS:
            name, content_type = _ASSETS[path]
            return self._asset(name, content_type)
        if path == "/api/status":
            return self._json(
                {
                    "version": __version__,
                    "api_version": API_VERSION,
                    "project": str(self.server.manager.project),
                    "conversation_store": str(
                        self.server.manager.conversations.path
                    ),
                    "conversation_backup": str(
                        self.server.manager.conversations.backup_path
                    ),
                    "providers": get_provider_names(),
                    "provider_catalog": get_provider_catalog(),
                    "modes": ["fast", "review", "consensus"],
                    "auth_required": self.server.auth.enabled,
                    "profile": self.server.auth.role,
                }
            )
        force_usage = path == "/api/usage" and parse_qs(parsed.query).get(
            "force"
        ) == ["1"]
        required = "maintainer" if force_usage else required_role("GET", path)
        if not self._authorize(required):
            return
        facade = _web_facade()
        if path == "/api/capabilities":
            return self._json(facade.provider_capabilities())
        if path == "/api/doctor":
            return self._json(doctor_report(self.server.manager.project))
        if path == "/api/files":
            project_id = parse_qs(parsed.query).get("project", ["free"])[0]
            return self._json(self.server.manager.files.list(project_id))
        if path.startswith("/api/files/") and path.endswith("/download"):
            item_id = unquote(path.split("/")[-2])
            item = self.server.manager.files.get(item_id)
            if not item:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self._file(item)
        if path == "/api/usage":
            force = parse_qs(parsed.query).get("force") == ["1"]
            return self._json(facade.usage_status(force=force))
        if path == "/api/conversations":
            return self._json(self.server.manager.conversations.list())
        if path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            project_id = parse_qs(parsed.query).get("project", [None])[0]
            return self._json(self.server.manager.conversations.search(query, project_id))
        if path == "/api/analytics":
            project_id = parse_qs(parsed.query).get("project", [None])[0]
            return self._json(self.server.manager.conversations.analytics(project_id))
        if path == "/api/preferences":
            return self._json(self.server.manager.conversations.preferences())
        if path == "/api/runs/active":
            return self._json(
                [
                    {
                        "run_id": run.run_id,
                        "conversation_id": run.conversation_id,
                        "request": run.request,
                    }
                    for run in self.server.manager.active_runs()
                ]
            )
        if path == "/api/tasks":
            return self._json(self.server.manager.list_tasks())
        if path.startswith("/api/tasks/") and path.endswith("/diff"):
            task_id = unquote(path.split("/")[-2])
            try:
                report = self.server.manager.task_diff(task_id)
            except WorktreeError as error:
                return self._json(
                    {"error": str(error)},
                    HTTPStatus.CONFLICT,
                )
            return self._json(
                report,
                HTTPStatus.OK if report is not None else HTTPStatus.NOT_FOUND,
            )
        if path == "/api/projects":
            return self._json(self.server.manager.conversations.list_projects())
        if path == "/api/skills/global":
            return self._json(list_global_skills())
        if path.startswith("/api/projects/") and path.endswith("/skills"):
            project_id = unquote(path.split("/")[-2])
            project = self.server.manager.conversations.get_project(project_id)
            if not project:
                return self._json({}, HTTPStatus.NOT_FOUND)
            workspace = _existing_directory(project.get("workspace_root")) or self.server.manager.project
            return self._json(list_skills(workspace))
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
            return self._events(
                unquote(path.rsplit("/", 1)[1]),
                _event_cursor(
                    self.headers.get("Last-Event-ID"),
                    parse_qs(parsed.query).get("after", ["0"])[0],
                ),
            )
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/pair":
            return self._pair()
        if not self._authorize(required_role("POST", path)):
            return
        if path == "/api/auth/rotate":
            token = rotate_token(self.server.auth_path)
            self.server.auth = LocalAuth(token, self.server.auth.role, True)
            return self._json({"rotated": True})
        if path.startswith("/api/tasks/") and path.endswith("/integrate"):
            task_id = unquote(path.split("/")[-2])
            try:
                task = self.server.manager.integrate_task(task_id)
            except KeyError:
                return self._json({}, HTTPStatus.NOT_FOUND)
            except WorktreeError as error:
                return self._json(
                    {"error": str(error)},
                    HTTPStatus.CONFLICT,
                )
            return self._json(task, HTTPStatus.ACCEPTED)
        if path.startswith("/api/runs/") and path.endswith("/reject"):
            run_id = unquote(path.split("/")[-2])
            payload = self._read_payload(allow_empty=True)
            if payload is None:
                return
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
            payload = self._read_payload(allow_empty=True)
            if payload is None:
                return
            return self._json(
                self.server.manager.conversations.create(payload.get("project_id")),
                HTTPStatus.CREATED,
            )
        if path == "/api/files":
            payload = self._read_payload(max_bytes=MAX_FILE_BYTES * 2)
            if payload is None:
                return
            project_id = str(payload.get("project_id", "free"))
            if not self.server.manager.conversations.get_project(project_id):
                return self._json(
                    {"error": "Projet inconnu."},
                    HTTPStatus.BAD_REQUEST,
                )
            try:
                item = self.server.manager.files.add(
                    project_id,
                    str(payload.get("name", "file")),
                    str(payload.get("content_type", "")),
                    str(payload.get("data", "")),
                )
            except (OSError, ValueError) as error:
                return self._json(
                    {"error": str(error)},
                    HTTPStatus.BAD_REQUEST,
                )
            return self._json(item, HTTPStatus.CREATED)
        if path == "/api/projects":
            payload = self._read_payload(allow_empty=True)
            if payload is None:
                return
            return self._json(
                self.server.manager.conversations.create_project(
                    str(payload.get("name", "Nouveau sous-projet"))
                ),
                HTTPStatus.CREATED,
            )
        if path.startswith("/api/projects/") and path.endswith("/skills/import"):
            project_id = unquote(path.split("/")[-3])
            project = self.server.manager.conversations.get_project(project_id)
            if not project:
                return self._json({}, HTTPStatus.NOT_FOUND)
            payload = self._read_payload(allow_empty=True)
            if payload is None:
                return
            workspace = _existing_directory(project.get("workspace_root")) or self.server.manager.project
            try:
                imported = import_skill(
                    workspace,
                    Path(str(payload.get("source", ""))),
                    name=str(payload.get("name", "")).strip() or None,
                    source_provider=str(payload.get("provider", "unknown")),
                )
            except (OSError, ValueError) as error:
                return self._json({"message": str(error)}, HTTPStatus.BAD_REQUEST)
            return self._json(imported, HTTPStatus.CREATED)
        if path.startswith("/api/projects/") and path.endswith("/skills/promote"):
            project_id = unquote(path.split("/")[-3])
            project = self.server.manager.conversations.get_project(project_id)
            if not project:
                return self._json({}, HTTPStatus.NOT_FOUND)
            payload = self._read_payload(allow_empty=True)
            if payload is None:
                return
            workspace = _existing_directory(project.get("workspace_root")) or self.server.manager.project
            try:
                promoted = promote_skill(workspace, str(payload.get("name", "")).strip())
            except (OSError, ValueError) as error:
                return self._json({"message": str(error)}, HTTPStatus.BAD_REQUEST)
            return self._json(promoted, HTTPStatus.CREATED)
        if path != "/api/runs":
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            payload = self._read_payload()
            if payload is None:
                return
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
            attachments = payload.get("attachments") or []
            if (
                not isinstance(attachments, list)
                or len(attachments) > 24
                or not all(isinstance(item, str) for item in attachments)
            ):
                raise ValueError("invalid attachments")
            full_access_approved = payload.get("full_access_approved") is True
            if agent is not None and agent not in set(get_provider_names()):
                raise ValueError("invalid agent")
            if mode not in {None, "fast", "review", "consensus"}:
                raise ValueError("invalid mode")
        except ValueError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        needs_full_access = self.server.manager.requires_full_access_approval(
            request,
            conversation_id,
            agent,
            mode,
            execution_mode,
        )
        if needs_full_access and not self.server.auth.allows("maintainer"):
            return self._json(
                {"error": "Le profil maintainer est requis pour cet accès."},
                HTTPStatus.FORBIDDEN,
            )
        if needs_full_access and not full_access_approved:
            return self._json(
                {
                    "error": (
                        "Cette tâche demande un accès complet aux seules racines "
                        "déclarées pour ce projet. Confirme l’autorisation pour "
                        "ce run."
                    ),
                    "approval": "full-access",
                },
                HTTPStatus.PRECONDITION_REQUIRED,
            )
        try:
            run = self.server.manager.start(
                request,
                conversation_id,
                agent,
                mode,
                model,
                effort,
                execution_mode,
                attachments,
            )
        except ActiveConversationError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
        self._json({"run_id": run.run_id}, HTTPStatus.ACCEPTED)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize(required_role("PATCH", path)):
            return
        if path == "/api/preferences":
            payload = self._read_payload()
            if payload is None:
                return
            return self._json(
                self.server.manager.conversations.update_preferences(payload)
            )
        is_conversation = path.startswith("/api/conversations/")
        is_project = path.startswith("/api/projects/")
        if not is_conversation and not is_project:
            return self.send_error(HTTPStatus.NOT_FOUND)
        payload = self._read_payload()
        if payload is None:
            return
        identifier = unquote(path.rsplit("/", 1)[1])
        item = (
            self.server.manager.conversations.update(identifier, payload)
            if is_conversation
            else self.server.manager.conversations.update_project(identifier, payload)
        )
        self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize(required_role("DELETE", path)):
            return
        if path.startswith("/api/tasks/"):
            task_id = unquote(path.rsplit("/", 1)[1])
            try:
                deleted = self.server.manager.delete_task(task_id)
            except WorktreeError as error:
                return self._json(
                    {"error": str(error)},
                    HTTPStatus.CONFLICT,
                )
            return self._json(
                {"deleted": deleted},
                HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND,
            )
        if path.startswith("/api/files/"):
            item_id = unquote(path.rsplit("/", 1)[1])
            deleted = self.server.manager.files.delete(item_id)
            return self._json(
                {"deleted": deleted},
                HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND,
            )
        if not path.startswith("/api/conversations/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        conversation_id = unquote(path.rsplit("/", 1)[1])
        if self.server.manager.has_active_conversation(conversation_id):
            return self._json(
                {"error": "Interromps la tâche avant de supprimer la conversation."},
                HTTPStatus.CONFLICT,
            )
        deleted = self.server.manager.conversations.delete(conversation_id)
        self._json(
            {"deleted": deleted},
            HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND,
        )

    def _events(self, run_id: str, after: int = 0) -> None:
        run = self.server.manager.get_run(run_id)
        if not run:
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        index = min(after, len(run.events))
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
                    self.wfile.write(
                        f"id: {event['event_id']}\ndata: {data}\n\n".encode()
                    )
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

    def _pair(self) -> None:
        authorization = self.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.startswith("Bearer ") else None
        if not self.server.auth.accepts(token):
            return self._json(
                {"error": "Jeton d’appairage invalide."},
                HTTPStatus.UNAUTHORIZED,
            )
        self._json(
            {"paired": True},
            headers={
                "Set-Cookie": (
                    f"joe_token={self.server.auth.token}; "
                    "HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000"
                )
            },
        )

    def _authorize(self, required: str) -> bool:
        if not self.server.auth.enabled:
            return True
        token = None
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        if not token:
            for item in self.headers.get("Cookie", "").split(";"):
                key, separator, value = item.strip().partition("=")
                if separator and key == "joe_token":
                    token = value
                    break
        if not self.server.auth.accepts(token):
            self._json({"error": "Authentification Joe requise."}, HTTPStatus.UNAUTHORIZED)
            return False
        if not self.server.auth.allows(required):
            self._json(
                {
                    "error": (
                        f"Le profil {required} est requis. "
                        "Relance Joe avec --profile maintainer ou réduis les "
                        "permissions du projet."
                    )
                },
                HTTPStatus.FORBIDDEN,
            )
            return False
        return True

    def _read_payload(
        self,
        *,
        allow_empty: bool = False,
        max_bytes: int = 1024 * 1024,
    ) -> dict[str, Any] | None:
        try:
            return read_json_body(
                self.headers,
                self.rfile,
                allow_empty=allow_empty,
                max_bytes=max_bytes,
            )
        except RequestBodyError as exc:
            self._json({"error": str(exc)}, exc.status)
            return None

    def _file(self, item: dict[str, Any]) -> None:
        path = Path(str(item["path"]))
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        content_type = str(
            item.get("content_type")
            or mimetypes.guess_type(str(item.get("name", "")))[0]
            or "application/octet-stream"
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(str(item['name']))}",
        )
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(
    project: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    allow_remote: bool = False,
    profile: str = "maintainer",
) -> None:
    validate_bind(host, allow_remote=allow_remote)
    server = JoeServer((host, port), Handler)
    server.auth = LocalAuth.enabled_for(profile)
    server.manager = RunManager(project, profile=profile)
    server.serve_forever()


def _web_facade():
    from . import web as facade

    return facade


def _event_cursor(header: str | None, query: str | None) -> int:
    for value in (header, query):
        if value in {None, ""}:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0
