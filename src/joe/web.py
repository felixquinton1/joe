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
    cancel_event: threading.Event = field(default_factory=threading.Event)
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
                cancel_event=run.cancel_event,
                on_event=lambda event: self._emit_run_event(run, event),
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
            if run.cancel_event.is_set():
                self.conversations.remove_run(run.conversation_id, run.run_id)
                run.emit({"type": "cancelled"})
                return
            self.conversations.append_message(
                run.conversation_id, "assistant", f"Erreur : {exc}", run.run_id
            )
            run.emit({"type": "error", "message": str(exc)})
        finally:
            with run.condition:
                run.done = True
                run.condition.notify_all()

    def _emit_run_event(self, run: LiveRun, event: dict[str, Any]) -> None:
        run.emit(event)
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

    def cancel(self, run_id: str) -> bool:
        with self.lock:
            run = self.live.get(run_id)
        if not run or run.done:
            return False
        run.cancel_event.set()
        return True


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
