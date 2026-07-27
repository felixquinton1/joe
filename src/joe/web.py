from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import __version__
from .capabilities import provider_capabilities
from .conversations import ConversationStore
from .models import Intent, Mode
from .orchestrator import Orchestrator
from .usage import usage_status


@dataclass
class LiveRun:
    run_id: str
    request: str
    conversation_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    condition: threading.Condition = field(default_factory=threading.Condition)

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
            self.orchestrator.memory.root, self.orchestrator.memory.runs
        )
        self.conversations.ensure()
        self.live: dict[str, LiveRun] = {}
        self.lock = threading.Lock()

    def start(
        self,
        request: str,
        conversation_id: str,
        agent: str | None,
        mode: str | None,
        model: str | None,
        effort: str | None,
        execution_mode: str | None,
    ) -> LiveRun:
        run = LiveRun(uuid.uuid4().hex, request, conversation_id)
        self.conversations.append_message(
            conversation_id, "user", request, run.run_id
        )
        with self.lock:
            self.live[run.run_id] = run
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
            forced_mode = Mode(mode) if mode else None
            route = self.orchestrator.router.route(
                run.request,
                forced_agent=agent,
                forced_mode=forced_mode,
                previous_provider=self.conversations.previous_provider(
                    run.conversation_id
                ),
            )
            run.emit(
                {
                    "type": "route",
                    "mode": route.mode.value,
                    "intent": route.intent.value,
                    "primary": route.primary,
                    "reviewer": route.reviewer,
                }
            )
            if route.intent is Intent.MODIFY and not os.access(self.project, os.W_OK):
                raise PermissionError(
                    "Le projet n'est pas accessible en écriture. "
                    "Relance Joe depuis un montage inscriptible."
                )
            response, log = self.orchestrator.execute(
                run.request,
                route,
                model=model,
                effort=effort,
                execution_mode=execution_mode,
                extra_context=self.conversations.context(run.conversation_id),
                on_event=run.emit,
            )
            self.conversations.append_message(
                run.conversation_id,
                "assistant",
                response,
                run.run_id,
                provider=route.primary,
            )
            run.emit(
                {
                    "type": "complete",
                    "response": response,
                    "log": str(log),
                }
            )
        except Exception as exc:
            self.conversations.append_message(
                run.conversation_id, "assistant", f"Erreur : {exc}", run.run_id
            )
            run.emit({"type": "error", "message": str(exc)})
        finally:
            with run.condition:
                run.done = True
                run.condition.notify_all()

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


class JoeServer(ThreadingHTTPServer):
    manager: RunManager


class Handler(BaseHTTPRequestHandler):
    server: JoeServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
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
                    "providers": ["codex", "claude", "gemini", "copilot"],
                    "modes": ["fast", "review", "consensus"],
                }
            )
        if path == "/api/capabilities":
            return self._json(provider_capabilities())
        if path == "/api/usage":
            return self._json(usage_status())
        if path == "/api/conversations":
            return self._json(self.server.manager.conversations.list())
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
        if path == "/api/conversations":
            return self._json(
                self.server.manager.conversations.create(), HTTPStatus.CREATED
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
        if not path.startswith("/api/conversations/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)
        item = self.server.manager.conversations.update(
            unquote(path.rsplit("/", 1)[1]), payload
        )
        self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

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
