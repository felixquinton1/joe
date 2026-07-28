from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .http_utils import RequestBodyError, read_json_body, validate_bind
from .web_runs import RunManager

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
    "/app_usage.js": ("app_usage.js", "text/javascript; charset=utf-8"),
    "/app_conversations.js": ("app_conversations.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class JoeServer(ThreadingHTTPServer):
    manager: RunManager


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
        facade = _web_facade()
        if path == "/api/capabilities":
            return self._json(facade.provider_capabilities())
        if path == "/api/usage":
            force = parse_qs(parsed.query).get("force") == ["1"]
            return self._json(facade.usage_status(force=force))
        if path == "/api/conversations":
            return self._json(self.server.manager.conversations.list())
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
            if agent not in {None, "codex", "claude", "gemini", "copilot"}:
                raise ValueError("invalid agent")
            if mode not in {None, "fast", "review", "consensus"}:
                raise ValueError("invalid mode")
        except ValueError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        run = self.server.manager.start(
            request,
            conversation_id,
            agent,
            mode,
            model,
            effort,
            execution_mode,
        )
        self._json({"run_id": run.run_id}, HTTPStatus.ACCEPTED)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
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

    def _events(self, run_id: str) -> None:
        run = self.server.manager.get_run(run_id)
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

    def _read_payload(self, *, allow_empty: bool = False) -> dict[str, Any] | None:
        try:
            return read_json_body(
                self.headers,
                self.rfile,
                allow_empty=allow_empty,
            )
        except RequestBodyError as exc:
            self._json({"error": str(exc)}, exc.status)
            return None

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


def serve(
    project: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    allow_remote: bool = False,
) -> None:
    validate_bind(host, allow_remote=allow_remote)
    server = JoeServer((host, port), Handler)
    server.manager = RunManager(project)
    server.serve_forever()


def _web_facade():
    from . import web as facade

    return facade
