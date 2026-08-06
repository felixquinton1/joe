from __future__ import annotations

import os
import json
import queue
import re
import signal
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import Intent, ProviderResult
from .provider_registry import (
    get_provider_names,
    get_provider_spec,
    get_provider_specs,
)
from .usage import record_gemini_usage

StreamCallback = Callable[[str, str], None]
PROVIDER_HEARTBEAT_SECONDS = 10.0

READ_ONLY_MODES = {"read-only", "plan"}
WORKSPACE_WRITE_MODES = {
    "workspace-write",
    "acceptEdits",
    "auto_edit",
    "modify",
}
PROJECT_FULL_ACCESS_MODES = {"danger-full-access"}
RESTRICTED_MODES = {"dontAsk"}

# Fournisseurs dont la commande varie réellement selon l'accès réseau accordé.
# Seul Codex expose un commutateur d'egress
# (`sandbox_workspace_write.network_access`). Les CLI Claude, Gemini et Copilot
# n'offrent aucune option équivalente : le réglage « Accès Web » ne les
# contraint pas, et l'interface doit le dire au lieu de laisser croire à une
# garantie globale. Voir tests/test_providers.py::
# test_network_control_declaration_matches_commands.
NETWORK_CONTROLLED_PROVIDERS = ("codex",)


def _access_level(execution_mode: str | None, modifying: bool) -> str:
    if execution_mode in READ_ONLY_MODES:
        return "read"
    if execution_mode in WORKSPACE_WRITE_MODES:
        return "write"
    if execution_mode in PROJECT_FULL_ACCESS_MODES:
        return "project-full"
    if execution_mode in RESTRICTED_MODES:
        return "restricted"
    return "write" if modifying else "read"


class ProviderCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    executable: str
    additional_roots: tuple[Path, ...] = ()
    remote_access: bool = False
    watchdog_seconds: float | None = None
    """Override du délai déclaré au registre. `None` = valeur du registre."""

    @property
    def watchdog_delay(self) -> float | None:
        """Effective idle timeout: an explicit override, else the registry.

        La présence d'une valeur est le seul commutateur du chien de garde ;
        aucun test sur le nom du fournisseur n'intervient.
        """
        if self.watchdog_seconds is not None:
            return self.watchdog_seconds
        return get_provider_spec(self.name).watchdog_seconds

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
        access = _access_level(execution_mode, modifying)
        builder = _ARGV_BUILDERS.get(self.name)
        if builder is None:
            raise ValueError(f"Unknown provider: {self.name}")
        return builder(self, access, prompt, cwd, model, effort)

    def _roots(self, flag: str) -> list[str]:
        arguments: list[str] = []
        for root in self.additional_roots:
            arguments.extend([flag, str(root)])
        return arguments

    def _argv_codex(
        self,
        access: str,
        prompt: str,
        cwd: Path,
        model: str | None,
        effort: str | None,
    ) -> list[str]:
        sandbox = {
            "read": "read-only",
            "write": "workspace-write",
            "project-full": "workspace-write",
            "restricted": "read-only",
        }[access]
        command = [self.executable, "--ask-for-approval", "never"]
        if effort:
            command.extend(["--config", f'model_reasoning_effort="{effort}"'])
        command.extend([
            "exec", "--json",
            "--ephemeral", "--skip-git-repo-check", "--color", "never",
            "--sandbox", sandbox,
        ])
        if self.remote_access and sandbox == "workspace-write":
            command.extend(
                ["--config", "sandbox_workspace_write.network_access=true"]
            )
        command.extend(self._roots("--add-dir"))
        if model:
            command.extend(["--model", model])
        return [*command, "-C", str(cwd), prompt]

    def _argv_claude(
        self,
        access: str,
        prompt: str,
        cwd: Path,
        model: str | None,
        effort: str | None,
    ) -> list[str]:
        # `acceptEdits` n'auto-accepte que l'ÉDITION de fichiers : pytest, npm et
        # curl restent refusés, et `--print` n'offre aucun canal d'approbation
        # en cours de run. Un accès en écriture qui ne peut pas lancer les tests
        # qu'il vient d'écrire ne tient pas sa promesse : on ajoute Bash
        # explicitement. Le mode `auto` du CLI ne change rien (vérifié).
        permission = {
            "read": "plan",
            "write": "acceptEdits",
            "project-full": "bypassPermissions",
            "restricted": "dontAsk",
        }[access]
        command = [
            self.executable, "--print", "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", permission, "--no-session-persistence",
        ]
        if access == "write":
            command.extend(["--allowedTools", "Bash"])
        command.extend(self._roots("--add-dir"))
        if effort:
            command.extend(["--effort", effort])
        if model:
            command.extend(["--model", model])
        return [*command, prompt]

    def _argv_gemini(
        self,
        access: str,
        prompt: str,
        cwd: Path,
        model: str | None,
        effort: str | None,
    ) -> list[str]:
        # Même écart que pour Claude : `auto_edit` n'auto-approuve que les outils
        # d'édition. `yolo` est le seul mode de Gemini qui autorise le shell.
        approval = {
            "read": "plan",
            "write": "yolo",
            "project-full": "yolo",
            "restricted": "plan",
        }[access]
        command = [
            self.executable, "--output-format", "stream-json",
            "--approval-mode", approval, "--skip-trust",
        ]
        command.extend(self._roots("--include-directories"))
        if model:
            command.extend(["--model", model])
        return [*command, "--prompt", prompt]

    def _argv_copilot(
        self,
        access: str,
        prompt: str,
        cwd: Path,
        model: str | None,
        effort: str | None,
    ) -> list[str]:
        allow_modify = access in {"write", "project-full"}
        allow_shell = allow_modify
        args = [
            self.executable, "--silent", "--no-color",
            "--no-remote", "--no-remote-export", "--no-ask-user",
        ]
        args.extend(self._roots("--add-dir"))
        if model:
            args.extend(["--model", model])
        if effort:
            args.extend(["--effort", effort])
        if not allow_modify:
            args.append("--plan")
        if allow_modify:
            args.append("--allow-tool=write")
        else:
            args.append("--available-tools=read,search")
        if allow_shell:
            args.append("--allow-tool=shell")
        return [*args, "--prompt", prompt]

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
            group_options = _process_group_options()
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                **group_options,
            )
            stdout, stderr = _collect_streams(
                process,
                timeout,
                on_stream,
                secret_values,
                self.name,
                cancel_event,
                self.watchdog_delay,
            )
            if self.name == "gemini":
                record_gemini_usage(stdout)
            stdout = _final_output(self.name, stdout)
            duration = time.monotonic() - start
            kind = classify_error(
                f"{stdout}\n{stderr}",
                process.returncode,
                self.name,
            )
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


def classify_error(
    stderr: str,
    returncode: int,
    provider: str | None = None,
) -> str | None:
    lower = stderr.lower()
    if returncode == 0:
        return (
            "quota"
            if _terminal_quota(provider, stderr)
            else None
        )
    if any(word in lower for word in ("auth", "login", "unauthorized", "credential")):
        return "authentication"
    if any(
        word in lower
        for word in (
            "quota",
            "rate limit",
            "capacity",
            "overloaded",
            "usage limit",
            "hit your limit",
            "session limit",
            "limit reached",
            "rate_limit",
            '"api_error_status":429',
        )
    ):
        return "quota"
    return "process"


# Un fournisseur déclaré au registre sans constructeur d'argv — ou l'inverse —
# échoue ici, à l'import, au lieu de lever au premier run.
_ARGV_BUILDERS = {
    "codex": Provider._argv_codex,
    "claude": Provider._argv_claude,
    "gemini": Provider._argv_gemini,
    "copilot": Provider._argv_copilot,
}
if set(_ARGV_BUILDERS) != set(get_provider_names()):
    raise RuntimeError(
        "Fournisseurs déclarés et constructeurs d'argv désaccordés : "
        f"{sorted(set(_ARGV_BUILDERS) ^ set(get_provider_names()))}"
    )


def _terminal_quota(provider: str | None, text: str) -> bool:
    """A quota the provider itself reports as definitively exhausted."""
    markers = get_provider_spec(provider or "").terminal_quota_markers
    lower = text.lower()
    return any(marker in lower for marker in markers)


def default_providers(
    additional_roots: tuple[Path, ...] = (),
    remote_access: bool = False,
) -> dict[str, Provider]:
    return {
        name: Provider(name, name, additional_roots, remote_access)
        for name in get_provider_names()
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
    watchdog_seconds: float | None = None,
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
    started_at = time.monotonic()
    next_heartbeat = started_at + PROVIDER_HEARTBEAT_SECONDS
    # Le watchdog s'arme sur la seule présence d'un délai déclaré au registre.
    spec = get_provider_spec(provider or "")
    idle_deadline = (
        time.monotonic() + min(watchdog_seconds, timeout)
        if watchdog_seconds
        else None
    )
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
        if idle_deadline and time.monotonic() >= idle_deadline:
            if callback:
                callback(
                    "activity",
                    json.dumps(
                        {
                            "kind": "watchdog",
                            "label": f"{spec.label} ne répond plus",
                            "detail": "Arrêt et bascule automatique vers un autre agent",
                        },
                        ensure_ascii=False,
                    ),
                )
            _stop_process(process, force=True)
            error = TimeoutError("provider inactive")
            error.stdout = "".join(output["stdout"])  # type: ignore[attr-defined]
            error.stderr = f"{spec.name} watchdog: no output received"  # type: ignore[attr-defined]
            raise error
        try:
            stream_name, line = events.get(timeout=min(0.25, remaining))
        except queue.Empty:
            now = time.monotonic()
            if callback and now >= next_heartbeat:
                elapsed = max(0, int(now - started_at))
                minutes, seconds = divmod(elapsed, 60)
                duration = (
                    f"{minutes} min {seconds:02d} s"
                    if minutes
                    else f"{seconds} s"
                )
                callback(
                    "activity",
                    json.dumps(
                        {
                            "kind": "heartbeat",
                            "label": "Toujours en cours",
                            "detail": f"Processus actif depuis {duration}",
                        },
                        ensure_ascii=False,
                    ),
                )
                next_heartbeat = now + PROVIDER_HEARTBEAT_SECONDS
            continue
        if line is None:
            closed += 1
            continue
        line = _redact_values(line, secret_values)
        if watchdog_seconds:
            idle_deadline = time.monotonic() + min(watchdog_seconds, timeout)
        output[stream_name].append(line)
        if callback:
            activity = _activity(provider, stream_name, line)
            if activity:
                callback("activity", json.dumps(activity, ensure_ascii=False))
            elif stream_name == "stderr" or not spec.streams_json:
                callback(stream_name, line)
        if (
            spec.terminal_quota_markers
            and stream_name == "stderr"
            and _terminal_quota(provider, line)
        ):
            _stop_process(process)
    process.wait()
    return "".join(output["stdout"]), "".join(output["stderr"])


def _process_group_options(
    platform_name: str | None = None,
) -> dict[str, int | bool]:
    platform = os.name if platform_name is None else platform_name
    if platform == "nt":
        return {
            "creationflags": getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        }
    return {"start_new_session": True}


def _stop_process(
    process: subprocess.Popen[str],
    force: bool = False,
    platform_name: str | None = None,
) -> None:
    if process.poll() is not None:
        return
    platform = os.name if platform_name is None else platform_name
    if platform == "nt":
        try:
            process.kill() if force else process.terminate()
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
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
    """Turn one JSON stream line into a displayable activity, if any.

    Le parseur est choisi par déclaration : un fournisseur `streams_json` sans
    parseur enregistré échoue à l'import plutôt que de rester muet en silence.
    """
    if stream_name != "stdout" or not get_provider_spec(
        provider or ""
    ).streams_json:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    parser = _ACTIVITY_PARSERS.get(provider or "")
    return parser(event) if parser else None


def _activity_codex(event: dict) -> dict[str, str] | None:
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
        return {
            "kind": item_type,
            "label": _activity_label(item_type),
            "detail": str(detail),
        }
    if event.get("type") == "turn.started":
        return {
            "kind": "status",
            "label": "Préparation de la réponse",
            "detail": "",
        }
    return None


def _activity_claude(event: dict) -> dict[str, str] | None:
    message = event.get("message") or {}
    for block in message.get("content", []):
        if block.get("type") == "tool_use":
            return {
                "kind": "tool",
                "label": str(block.get("name") or "Outil"),
                "detail": _tool_detail(block.get("input")),
            }
    if event.get("type") == "system":
        model = _event_model(event)
        if model:
            return {"kind": "model", "label": model, "detail": ""}
    return None


def _activity_gemini(event: dict) -> dict[str, str] | None:
    event_type = event.get("type")
    if event_type in {"tool_use", "tool_call"}:
        return {
            "kind": "tool",
            "label": str(event.get("tool_name") or event.get("name") or "Outil"),
            "detail": _tool_detail(event.get("parameters") or event.get("args")),
        }
    if event_type == "init":
        model = _event_model(event)
        if model:
            return {"kind": "model", "label": model, "detail": ""}
    return None


def _event_model(event: dict[str, object]) -> str | None:
    """Best-effort extraction of the precise model id from a CLI init event."""
    candidates = (
        event.get("model"),
        (event.get("data") if isinstance(event.get("data"), dict) else {}).get("model"),
        (event.get("session") if isinstance(event.get("session"), dict) else {}).get(
            "model"
        ),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
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
    """Extract the provider's answer from its JSON event stream."""
    if not get_provider_spec(provider).streams_json:
        return stdout
    extractor = _FINAL_EXTRACTORS.get(provider)
    if extractor is None:
        return stdout
    text_parts: list[str] = []
    result_text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        result_text = extractor(event, text_parts) or result_text
    separator = _FINAL_SEPARATORS.get(provider, "\n")
    return _final_section(result_text or separator.join(text_parts))


def _final_codex(event: dict, text_parts: list[str]) -> str:
    item = event.get("item") or {}
    if item.get("type") == "agent_message" and item.get("text"):
        text_parts.append(str(item["text"]))
    return ""


def _final_claude(event: dict, text_parts: list[str]) -> str:
    if event.get("type") == "result" and event.get("result"):
        return str(event["result"])
    return ""


def _final_gemini(event: dict, text_parts: list[str]) -> str:
    if event.get("type") == "message" and event.get("role") == "assistant":
        content = event.get("content")
        if content:
            text_parts.append(str(content))
    elif event.get("type") == "result" and event.get("response"):
        return str(event["response"])
    return ""


def _final_section(text: str) -> str:
    matches = list(
        re.finditer(
            r"(?im)^(?:#{1,3}\s*)?Résultat\s*:\s*",
            text,
        )
    )
    if not matches:
        return text
    content = text[matches[-1].end():].strip()
    return f"## Résultat\n\n{content}" if content else "## Résultat"


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


# Un fournisseur qui déclare `streams_json` doit fournir un parseur d'activité
# et un extracteur de réponse. Sans cette validation, il se dégradait
# silencieusement : aucun événement affiché, réponse brute renvoyée.
_ACTIVITY_PARSERS = {
    "codex": _activity_codex,
    "claude": _activity_claude,
    "gemini": _activity_gemini,
}
_FINAL_EXTRACTORS = {
    "codex": _final_codex,
    "claude": _final_claude,
    "gemini": _final_gemini,
}
_FINAL_SEPARATORS = {"gemini": ""}

_STREAMING = {
    spec.name for spec in get_provider_specs() if spec.streams_json
}
for _table, _label in (
    (_ACTIVITY_PARSERS, "parseurs d'activité"),
    (_FINAL_EXTRACTORS, "extracteurs de réponse"),
):
    if set(_table) != _STREAMING:
        raise RuntimeError(
            f"Fournisseurs streams_json et {_label} désaccordés : "
            f"{sorted(set(_table) ^ _STREAMING)}"
        )
