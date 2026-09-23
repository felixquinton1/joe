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
from .mcp import allowed_tool_patterns
from .provider_registry import (
    get_provider_names,
    get_provider_spec,
    get_provider_specs,
    provider_executables,
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


def provider_runtime_issue(name: str, executable: str) -> str | None:
    """Return an actionable error for an incomplete managed provider install.

    Native Codex releases use sibling helper binaries for code-mode tools.  The
    npm package supplies its own layout, so this check intentionally applies
    only to Joe's managed native installation.
    """
    if name != "codex":
        return None
    path = Path(executable)
    if path.name.lower() not in {"codex", "codex.exe"}:
        return None
    parts = tuple(part.lower() for part in path.parts)
    if not ("programs" in parts and "joe" in parts and "bin" in parts):
        return None
    suffix = ".exe" if path.suffix.lower() == ".exe" else ""
    required = (
        path.with_name(f"codex-code-mode-host{suffix}"),
        path.with_name(f"codex-command-runner{suffix}"),
        *(
            (path.with_name("codex-windows-sandbox-setup.exe"),)
            if suffix == ".exe"
            else ()
        ),
    )
    missing = [item.name for item in required if not item.is_file()]
    if not missing:
        return None
    return (
        "installation Codex native incomplète : composant(s) manquant(s) "
        + ", ".join(missing)
        + ". Réinstalle le paquet Codex complet avant de lancer Autonomous."
    )


def windows_aware_executable(
    name: str, *, os_module=os, shutil_module=shutil
) -> str | None:
    """Resolve an executable Windows shim without selecting an ACL-blocked alias."""
    if os_module.name == "nt":
        command = shutil_module.which(f"{name}.cmd")
        if command:
            return command
        environment = getattr(os_module, "environ", {})
        override = environment.get(f"JOE_{name.upper()}_EXECUTABLE")
        if override and Path(override).is_file():
            return override
        local_app_data = environment.get("LOCALAPPDATA")
        if local_app_data:
            managed = Path(local_app_data) / "Programs" / "Joe" / "bin" / f"{name}.exe"
            if managed.is_file():
                return str(managed)
        command = shutil_module.which(f"{name}.exe")
        if command:
            return command
    return shutil_module.which(name)


# Une identité se prouve en lançant le binaire : on ne le refait pas à chaque
# run. La clé est le chemin résolu, donc une réinstallation ailleurs rouvre la
# question.
_IDENTITY_CACHE: dict[str, bool] = {}


def _proves_identity(path: str, marker: str) -> bool:
    """Le binaire trouvé sous un nom générique se réclame-t-il du fournisseur ?"""
    cached = _IDENTITY_CACHE.get(path)
    if cached is not None:
        return cached
    proven = False
    for flag in ("--version", "--help"):
        try:
            result = subprocess.run(
                [path, flag],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if marker in f"{result.stdout}{result.stderr}".lower():
            proven = True
            break
    _IDENTITY_CACHE[path] = proven
    return proven


def resolve_executable(name: str, *, unconfirmed: bool = False, **kwargs) -> str | None:
    """Premier exécutable trouvé parmi les noms que le registre déclare.

    Une CLI peut être renommée en amont sans que le fournisseur change de nom
    chez Joe : ce nom-là est écrit dans les conversations. Chercher le seul nom
    du fournisseur revenait à la déclarer absente sur toute installation à jour.

    Un nom générique n'appartient à personne, alors il doit faire ses preuves.
    `unconfirmed=True` renvoie quand même le candidat qui a échoué, pour que le
    diagnostic puisse le nommer au lieu de dire « absent ».
    """
    marker = get_provider_spec(name).identity_marker
    doubtful = None
    for candidate in provider_executables(name):
        found = windows_aware_executable(candidate, **kwargs)
        if not found:
            continue
        if candidate == name or not marker or _proves_identity(found, marker):
            return found
        doubtful = doubtful or found
    return doubtful if unconfirmed else None


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
    mcp_tools: bool = False
    """Le projet autorise les outils MCP deja configures dans la CLI.

    Un outil MCP demande toujours une autorisation explicite, meme pour lire, et
    un appel non interactif n'offre aucun canal pour la donner : il est donc
    refuse. Pre-autoriser leve le refus, mais un outil MCP sort du projet par
    nature — cela ne se fait que sur demande du projet.
    """

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
        if self.remote_access:
            # `--search` is a global Codex option: it must precede `exec`.
            command.append("--search")
        # Joe already injects the shared AGENTS.md contract. Prevent Codex from
        # discovering and loading the same file a second time.
        command.extend(["--config", "project_doc_max_bytes=0"])
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
        # A dash reads the prompt from stdin, avoiding Windows argv limits.
        # The same transport is supported on macOS and Linux.
        return [*command, "-C", str(cwd), "-"]

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
        allowed = ["Bash"] if access == "write" else []
        if self.mcp_tools:
            allowed.extend(allowed_tool_patterns(self.name, self.executable))
        if allowed:
            command.extend(["--allowedTools", *allowed])
        command.extend(self._roots("--add-dir"))
        if effort:
            command.extend(["--effort", effort])
        if model:
            command.extend(["--model", model])
        # `--allowedTools` accepte plusieurs valeurs : sans ce séparateur, la
        # CLI avale le prompt comme un nom d'outil de plus et le run échoue sur
        # « Input must be provided ». Seuls les drapeaux qui suivaient le
        # masquaient jusqu'ici.
        return [*command, "--", prompt]

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

    def _argv_antigravity(
        self,
        access: str,
        prompt: str,
        cwd: Path,
        model: str | None,
        effort: str | None,
    ) -> list[str]:
        """Piloter `agy` en un seul tour non interactif.

        La CLI n'expose aucun drapeau d'autorisation fine : seulement un mode
        d'execution et un « tout approuver ». Les ecritures dans l'espace de
        travail sont deja auto-approuvees, mais une commande shell reste
        refusee en mode non interactif — la CLI le signale alors dans
        `denied_actions` plutot que d'echouer.

        Joe ne franchit donc pas ce palier a sa place : accorder « tout
        approuver » pour pouvoir lancer des tests reviendrait a autoriser aussi
        ce qui sort du projet, alors que l'utilisateur n'a consenti qu'a
        modifier celui-ci.
        """
        command = [self.executable, "--output-format", "stream-json"]
        if access == "project-full":
            command.append("--dangerously-skip-permissions")
        elif access == "restricted":
            command.extend(["--sandbox", "--mode", "plan"])
        else:
            command.extend(
                ["--mode", "accept-edits" if access == "write" else "plan"]
            )
        command.extend(self._roots("--add-dir"))
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["--effort", effort])
        # `--print` accepte une valeur facultative : laisse seul, il avale
        # l'argument suivant comme prompt. La CLI le dit elle-meme quand cela
        # arrive. On attache donc le prompt au drapeau.
        return [*command, f"--print={prompt}"]

    def _argv_cursor(
        self,
        access: str,
        prompt: str,
        cwd: Path,
        model: str | None,
        effort: str | None,
    ) -> list[str]:
        """Build the Cursor CLI invocation.

        Écrit sur documentation, sans exécution réelle : aucun compte Cursor
        n'était disponible. On s'en tient donc aux options confirmées par
        plusieurs sources — `--print`, `--output-format`, `--model`, `--force`
        — et on écarte les autres. Un drapeau inconnu ne dégrade pas, il fait
        échouer tout le run sur « unexpected argument », comme `--search` l'a
        montré sur Codex.

        Le répertoire de travail passe par `cwd` du processus, pas par un
        drapeau. Sans `--force`, Cursor propose les modifications sans les
        appliquer : c'est notre lecture seule, sans drapeau supplémentaire.
        L'effort n'a pas d'équivalent connu et n'est pas transmis.
        """
        args = [self.executable, "--print", "--output-format", "text"]
        if model:
            args.extend(["--model", model])
        if access in {"write", "project-full"}:
            args.append("--force")
        return [*args, prompt]

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
        # `executable` reste un point d'entrée explicite — les doublures de
        # test s'en servent, et il permet d'épingler un binaire précis. Sans
        # override, le registre décide quels noms chercher.
        executable = (
            resolve_executable(self.name)
            if self.executable == self.name
            else windows_aware_executable(self.executable)
        )
        if not executable:
            return ProviderResult(
                self.name, [self.executable], "", "executable not found", 127, 0,
                error_kind="unavailable",
            )
        runtime_issue = provider_runtime_issue(self.name, executable)
        if runtime_issue:
            return ProviderResult(
                self.name, [executable], "", runtime_issue, 127, 0,
                error_kind="unavailable",
            )
        command = self.command(
            prompt, cwd, intent, model, effort, execution_mode
        )
        command[0] = executable
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
                encoding="utf-8",
                errors="replace",
                stdin=(subprocess.PIPE if self.name == "codex" else None),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                **group_options,
            )
            if self.name == "codex" and process.stdin is not None:
                try:
                    process.stdin.write(prompt)
                    process.stdin.close()
                except BrokenPipeError:
                    pass
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
    "antigravity": Provider._argv_antigravity,
    "cursor-agent": Provider._argv_cursor,
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
    mcp_tools: bool = False,
) -> dict[str, Provider]:
    """Tous les fournisseurs déclarés, installés ou non — vue du diagnostic."""
    return {
        name: Provider(name, name, additional_roots, remote_access, mcp_tools)
        for name in get_provider_names()
    }


def active_providers(
    additional_roots: tuple[Path, ...] = (),
    remote_access: bool = False,
    mcp_tools: bool = False,
) -> dict[str, Provider]:
    """Ceux que Joe peut réellement lancer : détectés et non écartés.

    Le routage recevait jusqu'ici les cinq fournisseurs quoi qu'il arrive. Sur
    une machine où une seule CLI est installée, la première demande partait
    donc vers une CLI absente, échouait, puis se rabattait — l'utilisateur
    voyait une erreur pour une situation parfaitement normale.

    Si rien n'est détecté, on rend la liste complète plutôt qu'une liste vide :
    l'erreur au run reste « exécutable introuvable », qui dit la vérité, au
    lieu d'un plantage de routage sans destinataire.
    """
    from .provider_choice import disabled_providers

    declared = default_providers(additional_roots, remote_access, mcp_tools)
    refused = disabled_providers()
    usable = {
        name: provider
        for name, provider in declared.items()
        if name not in refused and resolve_executable(name)
    }
    return usable or declared


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
    process_exited_at: float | None = None
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
            if process.poll() is not None:
                process_exited_at = process_exited_at or now
                if now - process_exited_at >= 1:
                    # Some CLIs leave a helper process holding stdout/stderr
                    # open after their main process exits. Waiting for both EOF
                    # markers would then consume the whole provider timeout even
                    # though the requested command has already finished.
                    _stop_orphaned_process_group(process)
                    break
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
                            "label": "Still running",
                            "label_key": "still_running",
                            "detail": duration,
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


def _stop_orphaned_process_group(process: subprocess.Popen[str]) -> None:
    """Best-effort cleanup when the group leader exited before its helpers."""
    if os.name == "nt":
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except (AttributeError, OSError):
                pass
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


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
        # Codex emits the same item when it starts and when it completes. Keep a
        # single activity row instead of making one command look like two.
        event_type = event.get("type")
        expected = "item.completed" if item_type == "file_change" else "item.started"
        if event_type != expected:
            return None
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
            "label": "Preparing the response",
            "label_key": "preparing_response",
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


def _activity_antigravity(event: dict) -> dict[str, str] | None:
    """Lire le flux `stream-json` d'`agy`.

    L'enveloppe porte la cle `event`, pas `type`, et chaque etape d'outil
    arrive deux fois — ACTIVE puis DONE. On n'annonce que la premiere, sinon
    chaque outil apparaitrait en double dans le panneau d'activite.
    """
    if event.get("event") == "init":
        model = _event_model(event.get("init") or {})
        if model:
            return {"kind": "model", "label": model, "detail": ""}
        return None
    step = event.get("step_update") or {}
    if step.get("step_type") != "tool" or step.get("state") != "ACTIVE":
        return None
    name = str(step.get("tool_name") or "Outil")
    parameters = (step.get("tool_info") or {}).get("parameters")
    return {
        "kind": (
            "command_execution"
            if name == "run_command"
            else "mcp_tool_call"
            if name.startswith("mcp")
            else "tool"
        ),
        "label": name,
        "detail": _tool_detail(parameters),
    }


def _final_antigravity(event: dict, text_parts: list[str]) -> str:
    """La reponse finale, ou l'explication de son absence.

    Une permission qu'un appel non interactif ne peut pas demander est
    « refusee en douceur » : la CLI sort en succes avec une reponse vide et
    liste ce qu'elle n'a pas eu le droit de faire. Rendre ce vide tel quel
    donnerait une bulle muette ; on nomme donc l'autorisation manquante.
    """
    if event.get("event") != "result":
        return ""
    result = event.get("result") or {}
    response = str(result.get("response") or "").strip()
    if response:
        return response
    refuses = [
        str(item.get("display_name") or item.get("action"))
        for item in result.get("denied_actions") or []
        if isinstance(item, dict)
    ]
    if refuses:
        return (
            "Aucune réponse : l'agent a demandé une autorisation que Joe ne "
            "peut pas accorder dans un appel non interactif — "
            + ", ".join(refuses)
            + ". Autorise-la dans les réglages d'Antigravity, ou relance en "
            "accès complet."
        )
    return ""


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
    "antigravity": _activity_antigravity,
}
_FINAL_EXTRACTORS = {
    "codex": _final_codex,
    "claude": _final_claude,
    "gemini": _final_gemini,
    "antigravity": _final_antigravity,
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
