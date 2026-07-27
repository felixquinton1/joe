from __future__ import annotations

import os
import json
import queue
import signal
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import Intent, ProviderResult

StreamCallback = Callable[[str, str], None]


class ProviderCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    executable: str

    def command(
        self,
        prompt: str,
        cwd: Path,
        intent: Intent,
        model: str | None = None,
        effort: str | None = None,
        execution_mode: str | None = None,
    ) -> list[str]:
        modifying = intent is Intent.MODIFY
        if self.name == "codex":
            sandbox = execution_mode if execution_mode in {
                "read-only", "workspace-write"
            } else ("workspace-write" if modifying else "read-only")
            command = [
                self.executable, "--ask-for-approval", "never",
            ]
            if effort:
                command.extend(["--config", f'model_reasoning_effort="{effort}"'])
            command.extend([
                "exec", "--json",
                "--ephemeral", "--skip-git-repo-check", "--color", "never",
                "--sandbox", sandbox,
            ])
            if model:
                command.extend(["--model", model])
            return [*command, "-C", str(cwd), prompt]
        if self.name == "claude":
            permission = (
                execution_mode
                if execution_mode in {"plan", "acceptEdits", "dontAsk"}
                else ("acceptEdits" if modifying else "plan")
            )
            command = [
                self.executable, "--print", "--output-format", "stream-json",
                "--verbose",
                "--permission-mode", permission, "--no-session-persistence",
            ]
            if effort:
                command.extend(["--effort", effort])
            if model:
                command.extend(["--model", model])
            return [*command, prompt]
        if self.name == "gemini":
            approval = (
                execution_mode
                if execution_mode in {"plan", "auto_edit"}
                else ("auto_edit" if modifying else "plan")
            )
            command = [
                self.executable, "--output-format", "stream-json",
                "--approval-mode", approval, "--skip-trust",
            ]
            if model:
                command.extend(["--model", model])
            return [*command, "--prompt", prompt]
        if self.name == "copilot":
            allow_modify = modifying and execution_mode != "plan"
            args = [
                self.executable, "--silent", "--no-color",
                "--no-remote", "--no-remote-export", "--no-ask-user",
            ]
            if model:
                args.extend(["--model", model])
            if effort:
                args.extend(["--effort", effort])
            if execution_mode == "plan":
                args.append("--plan")
            if allow_modify:
                args.extend(["--allow-tool=write", "--allow-tool=shell"])
            else:
                args.extend(["--available-tools=read,search"])
            return [*args, "--prompt", prompt]
        raise ValueError(f"Unknown provider: {self.name}")

    def run(
        self,
        prompt: str,
        cwd: Path,
        intent: Intent,
        timeout: int,
        *,
        model: str | None = None,
        effort: str | None = None,
        execution_mode: str | None = None,
        cancel_event: threading.Event | None = None,
        on_stream: StreamCallback | None = None,
    ) -> ProviderResult:
        if not shutil.which(self.executable):
            return ProviderResult(
                self.name, [self.executable], "", "executable not found", 127, 0,
                error_kind="unavailable",
            )
        command = self.command(
            prompt, cwd, intent, model, effort, execution_mode
        )
        env = os.environ.copy()
        secret_values = _secret_values(env)
        start = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                start_new_session=True,
            )
            stdout, stderr = _collect_streams(
                process,
                timeout,
                on_stream,
                secret_values,
                self.name,
                cancel_event,
            )
            stdout = _final_output(self.name, stdout)
            duration = time.monotonic() - start
            kind = classify_error(stderr, process.returncode)
            return ProviderResult(
                self.name, command, stdout, stderr,
                process.returncode, duration, error_kind=kind,
            )
        except TimeoutError as exc:
            return ProviderResult(
                self.name,
                command,
                getattr(exc, "stdout", ""),
                getattr(exc, "stderr", ""),
                124,
                time.monotonic() - start,
                timed_out=True,
                error_kind="timeout",
            )
        except ProviderCancelled as exc:
            return ProviderResult(
                self.name,
                command,
                getattr(exc, "stdout", ""),
                getattr(exc, "stderr", ""),
                130,
                time.monotonic() - start,
                error_kind="cancelled",
            )


def classify_error(stderr: str, returncode: int) -> str | None:
    if returncode == 0:
        return None
    lower = stderr.lower()
    if any(word in lower for word in ("auth", "login", "unauthorized", "credential")):
        return "authentication"
    if any(word in lower for word in ("quota", "rate limit", "capacity", "overloaded")):
        return "quota"
    return "process"


def default_providers() -> dict[str, Provider]:
    return {
        name: Provider(name, name)
        for name in ("codex", "claude", "gemini", "copilot")
    }


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _collect_streams(
    process: subprocess.Popen[str],
    timeout: int,
    callback: StreamCallback | None,
    secret_values: tuple[str, ...] = (),
    provider: str | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str]:
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    output = {"stdout": [], "stderr": []}

    def read(stream_name: str, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                events.put((stream_name, line))
        finally:
            events.put((stream_name, None))

    threads = [
        threading.Thread(target=read, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + timeout
    closed = 0
    while closed < 2:
        if cancel_event and cancel_event.is_set():
            _stop_process(process)
            error = ProviderCancelled("provider cancelled")
            error.stdout = "".join(output["stdout"])  # type: ignore[attr-defined]
            error.stderr = "".join(output["stderr"])  # type: ignore[attr-defined]
            raise error
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(process, force=True)
            error = TimeoutError("provider timed out")
            error.stdout = "".join(output["stdout"])  # type: ignore[attr-defined]
            error.stderr = "".join(output["stderr"])  # type: ignore[attr-defined]
            raise error
        try:
            stream_name, line = events.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue
        if line is None:
            closed += 1
            continue
        line = _redact_values(line, secret_values)
        output[stream_name].append(line)
        if callback:
            activity = _activity(provider, stream_name, line)
            if activity:
                callback("activity", json.dumps(activity, ensure_ascii=False))
            elif stream_name == "stderr" or provider not in {
                "codex",
                "claude",
                "gemini",
            }:
                callback(stream_name, line)
    process.wait()
    return "".join(output["stdout"]), "".join(output["stderr"])


def _stop_process(process: subprocess.Popen[str], force: bool = False) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def _activity(
    provider: str | None, stream_name: str, line: str
) -> dict[str, str] | None:
    if stream_name != "stdout" or provider not in {"codex", "claude", "gemini"}:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if provider == "codex":
        item = event.get("item") or {}
        item_type = item.get("type")
        if item_type in {"command_execution", "mcp_tool_call", "file_change"}:
            detail = (
                item.get("command")
                or item.get("name")
                or item.get("path")
                or item.get("changes")
                or ""
            )
            return {"kind": item_type, "label": _activity_label(item_type), "detail": str(detail)}
        if event.get("type") == "turn.started":
            return {"kind": "status", "label": "Analyse de la demande", "detail": ""}
    if provider == "claude":
        message = event.get("message") or {}
        for block in message.get("content", []):
            if block.get("type") == "tool_use":
                detail = _tool_detail(block.get("input"))
                return {
                    "kind": "tool",
                    "label": str(block.get("name") or "Outil"),
                    "detail": detail,
                }
        if event.get("type") == "system":
            return {"kind": "status", "label": "Session Claude prête", "detail": ""}
    if provider == "gemini":
        event_type = event.get("type")
        if event_type in {"tool_use", "tool_call"}:
            return {
                "kind": "tool",
                "label": str(event.get("tool_name") or event.get("name") or "Outil"),
                "detail": _tool_detail(event.get("parameters") or event.get("args")),
            }
        if event_type == "init":
            return {"kind": "status", "label": "Session Gemini prête", "detail": ""}
    return None


def _activity_label(kind: str) -> str:
    return {
        "command_execution": "Commande",
        "mcp_tool_call": "Outil MCP",
        "file_change": "Modification de fichier",
    }.get(kind, kind)


def _tool_detail(value: object) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        for key in ("file_path", "path", "command", "query", "pattern"):
            if value.get(key):
                return str(value[key])
    return json.dumps(value, ensure_ascii=False)[:500]


def _final_output(provider: str, stdout: str) -> str:
    if provider not in {"codex", "claude", "gemini"}:
        return stdout
    text_parts: list[str] = []
    result_text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if provider == "codex":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                text_parts.append(str(item["text"]))
        elif provider == "claude":
            if event.get("type") == "result" and event.get("result"):
                result_text = str(event["result"])
        elif provider == "gemini":
            if event.get("type") == "message" and event.get("role") == "assistant":
                content = event.get("content")
                if content:
                    text_parts.append(str(content))
            elif event.get("type") == "result" and event.get("response"):
                result_text = str(event["response"])
    return result_text or "\n".join(text_parts)


def _secret_values(env: dict[str, str]) -> tuple[str, ...]:
    markers = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")
    values = {
        value
        for name, value in env.items()
        if value and len(value) >= 8 and any(marker in name.upper() for marker in markers)
    }
    return tuple(sorted(values, key=len, reverse=True))


def _redact_values(text: str, values: tuple[str, ...]) -> str:
    for value in values:
        text = text.replace(value, "[REDACTED]")
    return text
