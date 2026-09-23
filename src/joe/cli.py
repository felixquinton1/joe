from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__
from .models import Mode
from .orchestrator import OrchestrationError, Orchestrator
from .provider_registry import get_provider_names
from .prompt_language import request_language
from .routing import resolve_route
from .route_classifier import classify_request
from .capabilities import select_model_tier
from .skills import create_skill, import_skill, list_skills, parse_skill_request
from .usage import usage_status

def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="joe",
        description="Route natural-language project work across local AI CLIs.",
        epilog=(
            "Interfaces: `joe <request>` for a one-shot command, `joe cli` or "
            "`joe chat` for the interactive terminal, and `joe web` for the "
            "local interface. Useful commands: `url`, `auth rotate`, `doctor`, "
            "`sync`, `restart`, `stop`, `logs`, and `kill`."
        ),
    )
    result.add_argument("request", nargs="*", help="natural-language request")
    result.add_argument("-C", "--project", type=Path, default=Path.cwd())
    result.add_argument(
        "--agent",
        choices=get_provider_names(),
        help="force one provider",
    )
    result.add_argument("--mode", choices=[mode.value for mode in Mode])
    result.add_argument("--dry-run", action="store_true", help="show routing only")
    result.add_argument("--version", action="version", version=__version__)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments and sys.stdin.isatty():
        return _web([])
    commands = {
        "auth": _auth,
        "doctor": _doctor,
        "kill": _kill,
        "logs": _logs,
        "skills": _skills,
        "restart": _restart,
        "stop": _stop,
        "sync": _sync,
        "url": _url,
        "web": _web,
    }
    if arguments and arguments[0] in commands:
        return commands[arguments[0]](arguments[1:])
    if arguments and arguments[0] in {"chat", "cli"}:
        arguments = arguments[1:]
    args = parser().parse_args(arguments)
    project = args.project.resolve()
    if not project.is_dir():
        print(f"joe: project directory does not exist: {project}", file=sys.stderr)
        return 2
    orchestrator = Orchestrator(project)
    forced_mode = Mode(args.mode) if args.mode else None
    initial = " ".join(args.request).strip()
    if initial:
        return _handle(orchestrator, initial, args.agent, forced_mode, args.dry_run)
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if not piped:
            print("joe: empty request", file=sys.stderr)
            return 2
        return _handle(orchestrator, piped, args.agent, forced_mode, args.dry_run)
    print(f"Joe — {project}\nType /exit to quit.")
    while True:
        try:
            request = input("joe> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if request in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if request:
            _handle(orchestrator, request, args.agent, forced_mode, args.dry_run)


def _handle(
    orchestrator: Orchestrator,
    request: str,
    agent: str | None,
    mode: Mode | None,
    dry_run: bool,
) -> int:
    local_skill = parse_skill_request(request)
    statuses = usage_status()
    baseline = orchestrator.router.route(
        request,
        forced_agent=agent,
        forced_mode=mode,
        previous_provider=orchestrator.memory.previous_provider(),
    )
    classification = (
        None
        if local_skill is not None or dry_run
        else classify_request(
            request,
            baseline,
            orchestrator.providers,
            statuses,
            orchestrator.project,
            forced_agent=bool(agent),
            forced_mode=mode is not None,
        )
    )
    if (
        local_skill is None
        and classification
        and classification.action == "create_skill"
        and classification.confidence >= 0.85
    ):
        local_skill = {
            "name": classification.skill_name,
            "instructions": classification.skill_instructions,
            "scope": classification.skill_scope,
        }
    if local_skill is not None:
        if not local_skill["name"] or not local_skill["instructions"]:
            print(
                "joe: provide the skill name and instructions",
                file=sys.stderr,
            )
            return 2
        if dry_run:
            print(
                f"local-action=create_skill scope={local_skill['scope']} "
                f"name={local_skill['name']}"
            )
            return 0
        created = create_skill(
            None if local_skill["scope"] == "global" else orchestrator.project,
            local_skill["name"],
            local_skill["instructions"],
            global_scope=local_skill["scope"] == "global",
        )
        print(f"Skill {created['name']} created: {created['path']}")
        return 0
    decision = resolve_route(
        orchestrator.router,
        request,
        statuses,
        forced_agent=agent,
        forced_mode=mode,
        previous_provider=orchestrator.memory.previous_provider(),
        classification=classification,
    )
    route = decision.route
    quota_admission = decision.quota_admission
    if quota_admission:
        print(f"[joe] {quota_admission['message']}", file=sys.stderr)
        if quota_admission.get("blocked"):
            return 1
    print(
        f"[joe] {route.mode.value.upper()} · {route.intent.value} · {route.primary}",
        file=sys.stderr,
    )
    if dry_run:
        if route.reviewer:
            print(f"reviewer={route.reviewer}")
        print(route.reason)
        return 0
    try:
        response, log = orchestrator.execute(
            request,
            route,
            model=(
                select_model_tier(route.primary, classification.model_tier)
                if classification
                else None
            ),
            effort=classification.effort if classification else None,
            language=request_language(request),
        )
    except OrchestrationError as exc:
        print(f"joe: {exc}", file=sys.stderr)
        return 1
    print(response)
    print(f"[joe] log: {log}", file=sys.stderr)
    return 0


def _web(argv: list[str]) -> int:
    web_parser = argparse.ArgumentParser(
        prog="joe web", description="Start the local Joe web interface."
    )
    web_parser.add_argument("-C", "--project", type=Path, default=Path.cwd())
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)
    web_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow a non-local bind (authentication remains required)",
    )
    web_parser.add_argument(
        "--profile",
        choices=("viewer", "operator", "maintainer"),
        default="maintainer",
        help="maximum capabilities granted by this server",
    )
    web_parser.add_argument("--no-browser", action="store_true")
    web_parser.add_argument(
        "--foreground",
        action="store_true",
        help="run in this terminal instead of the platform background manager",
    )
    args = web_parser.parse_args(argv)
    if not args.project.is_dir():
        print(f"joe: project directory does not exist: {args.project}", file=sys.stderr)
        return 2
    from .web import serve
    from .http_utils import validate_bind

    try:
        validate_bind(args.host, allow_remote=args.allow_remote)
    except ValueError as exc:
        print(f"joe: {exc}", file=sys.stderr)
        return 2

    url = f"http://{args.host}:{args.port}"
    if not args.foreground:
        from .background import is_windows

        if is_windows():
            return _windows_web(args, url)
        if shutil.which("tmux"):
            return _tmux_web(args, url)
    print(f"Joe Web — {args.project.resolve()}\n{url}\nCtrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(_pairing_url(url),)).start()
    try:
        serve(
            args.project,
            args.host,
            args.port,
            allow_remote=args.allow_remote,
            profile=args.profile,
        )
    except KeyboardInterrupt:
        print("\nJoe Web stopped.")
    except OSError as exc:
        print(f"joe: could not start the server: {exc}", file=sys.stderr)
        return 1
    return 0


def _self_command() -> list[str]:
    """Command that re-runs this very Joe, whatever installation it comes from."""
    script = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if script and script.is_file() and script.name.startswith("joe"):
        return [str(script)]
    return [sys.executable, "-m", "joe.cli"]


def _server_command(args: argparse.Namespace) -> list[str]:
    command = [
        *_self_command(),
        "web",
        "-C",
        str(args.project.resolve()),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--no-browser",
        "--foreground",
        "--profile",
        args.profile,
    ]
    if args.allow_remote:
        command.append("--allow-remote")
    return command


def _wait_for_server(url: str, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _server_status(url) is not None:
            return True
        time.sleep(0.1)
    return False


def _windows_web(args: argparse.Namespace, url: str) -> int:
    from .background import (
        load_state,
        process_is_running,
        start_windows_background,
    )

    current = load_state(args.port)
    if current is not None and process_is_running(current.pid):
        log = current.log
        if not _wait_for_server(url):
            print(
                f"joe: the managed Windows server is not responding; see {log}",
                file=sys.stderr,
            )
            return 1
    elif _server_status(url) is not None:
        log = "unavailable (server was not started by this Joe installation)"
    else:
        current, process = start_windows_background(
            _server_command(args),
            port=args.port,
            project=args.project,
            host=args.host,
            profile=args.profile,
        )
        log = current.log
        if not _wait_for_server(url) and process.poll() is not None:
            print(
                f"joe: the Windows background server exited; see {log}",
                file=sys.stderr,
            )
            return process.returncode or 1
    print(f"Joe is running in the background\n{url}\nLog: {log}")
    if not args.no_browser:
        webbrowser.open(_pairing_url(url))
    return 0


def _tmux_web(args: argparse.Namespace, url: str) -> int:
    session = f"joe-{args.port}"
    exists = subprocess.run(
        ["tmux", "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    if not exists:
        # Se relancer soi-même, et non le premier `joe` du PATH : sur une
        # machine qui porte plusieurs installations, la session tmux exécutait
        # un autre Joe que celui invoqué, avec son propre code.
        command = _server_command(args)
        created = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, *command],
            check=False,
        )
        if created.returncode:
            print("joe: could not create the tmux session", file=sys.stderr)
            return created.returncode
    print(
        f"Joe is running in tmux · session {session}\n{url}\n"
        f"Console: tmux attach -t {session}"
    )
    if not args.no_browser:
        webbrowser.open(_pairing_url(url))
    return 0


def _kill(argv: list[str]) -> int:
    kill_parser = argparse.ArgumentParser(
        prog="joe kill",
        description="Stop every background server started by Joe.",
    )
    kill_parser.parse_args(argv)
    from .background import is_windows, managed_ports, stop_windows_background

    if is_windows():
        ports = managed_ports()
        stopped = sum(stop_windows_background(port) for port in ports)
        if stopped:
            suffix = "s" if stopped > 1 else ""
            print(f"Joe: stopped {stopped} background server{suffix}.")
        else:
            print("Joe: no active background server.")
        return 0
    if not shutil.which("tmux"):
        print("Joe: tmux is not installed.")
        return 0
    listed = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False, encoding="utf-8", errors="replace"
    )
    sessions = [
        name
        for name in listed.stdout.splitlines()
        if re.fullmatch(r"joe-\d+", name)
    ]
    stopped = 0
    for session in sessions:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session],
            check=False,
        )
        stopped += result.returncode == 0
    if not stopped:
        print("Joe: no active tmux session.")
    else:
        suffix = "s" if stopped > 1 else ""
        print(f"Joe: stopped {stopped} tmux session{suffix}.")
    return 0


def _stop(argv: list[str]) -> int:
    stop_parser = argparse.ArgumentParser(
        prog="joe stop",
        description="Stop the Joe background server on one port.",
    )
    stop_parser.add_argument("--port", type=int, default=8765)
    args = stop_parser.parse_args(argv)
    from .background import is_windows, stop_windows_background

    if is_windows():
        if not stop_windows_background(args.port):
            print(f"Joe: no managed background instance on port {args.port}.")
            return 1
        print(f"Joe: stopped the instance on port {args.port}.")
        return 0
    if not shutil.which("tmux"):
        print(
            "Joe: automatic stop is unavailable without tmux; interrupt the "
            "terminal running `joe web`."
        )
        return 1
    session = f"joe-{args.port}"
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        print(f"Joe: no active tmux instance on port {args.port}.")
        return 1
    print(f"Joe: stopped the instance on port {args.port}.")
    return 0


def _restart(argv: list[str]) -> int:
    restart_parser = argparse.ArgumentParser(
        prog="joe restart",
        description="Restart one Joe web server.",
    )
    restart_parser.add_argument("-C", "--project", type=Path)
    restart_parser.add_argument("--host", default="127.0.0.1")
    restart_parser.add_argument("--port", type=int, default=8765)
    restart_parser.add_argument(
        "--profile",
        choices=("viewer", "operator", "maintainer"),
        help="override the current server profile",
    )
    restart_parser.add_argument(
        "--force",
        action="store_true",
        help="restart even when the active-run check is unavailable",
    )
    args = restart_parser.parse_args(argv)
    url = f"http://{args.host}:{args.port}"
    status = _server_status(url)
    project = args.project
    if project is None and status and status.get("project"):
        project = Path(str(status["project"]))
    project = project or Path.cwd()
    if not project.is_dir():
        print(
            f"joe restart: project directory does not exist: {project}",
            file=sys.stderr,
        )
        return 2
    from .background import is_windows, stop_windows_background

    if not is_windows() and not shutil.which("tmux"):
        print(
            "joe restart: tmux is required for automatic restart.",
            file=sys.stderr,
        )
        return 2

    active = _active_runs(url)
    if active:
        print(
            "joe restart: refused because a task is still active.",
            file=sys.stderr,
        )
        return 3
    if active is None and not args.force:
        print(
            "joe restart: server status is unavailable; use --force to "
            "restart anyway.",
            file=sys.stderr,
        )
        return 3

    if is_windows():
        stop_windows_background(args.port)
    else:
        subprocess.run(
            ["tmux", "kill-session", "-t", f"joe-{args.port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    profile = args.profile or (
        str(status.get("profile")) if status and status.get("profile") else "maintainer"
    )
    return _web(
        [
            "-C",
            str(project.resolve()),
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--no-browser",
            "--profile",
            profile,
        ]
    )


def _logs(argv: list[str]) -> int:
    logs_parser = argparse.ArgumentParser(
        prog="joe logs",
        description="Print the log of a Joe background server.",
    )
    logs_parser.add_argument("--port", type=int, default=8765)
    logs_parser.add_argument("--tail", type=int, default=200)
    args = logs_parser.parse_args(argv)
    from .background import log_path

    target = log_path(args.port)
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        print(f"Joe: no background log on port {args.port}.", file=sys.stderr)
        return 1
    print("\n".join(lines[-max(1, args.tail) :]))
    return 0


def _active_runs(url: str) -> list[dict] | None:
    from .auth import auth_token_path

    try:
        token = auth_token_path().read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            f"{url}/api/runs/active",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(
            request,
            timeout=2,
        ) as response:
            payload = json.loads(response.read())
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ):
        return None
    return payload if isinstance(payload, list) else None


def _server_status(url: str) -> dict | None:
    from .auth import auth_token_path

    try:
        token = auth_token_path().read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            f"{url}/api/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(
            request,
            timeout=2,
        ) as response:
            payload = json.loads(response.read())
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ):
        return None
    return payload if isinstance(payload, dict) else None


def _pairing_url(url: str) -> str:
    from .auth import load_or_create_token

    return f"{url}#token={load_or_create_token()}"


def _url(argv: list[str]) -> int:
    url_parser = argparse.ArgumentParser(
        prog="joe url",
        description="Pair a browser with the local Joe server.",
    )
    url_parser.add_argument("--host", default="127.0.0.1")
    url_parser.add_argument("--port", type=int, default=8765)
    url_parser.add_argument(
        "--print",
        action="store_true",
        help="print the sensitive pairing URL instead of opening it",
    )
    args = url_parser.parse_args(argv)
    pairing_url = _pairing_url(f"http://{args.host}:{args.port}")
    if args.print:
        print("Attention : cette URL contient le secret local Joe.", file=sys.stderr)
        print(pairing_url)
    else:
        webbrowser.open(pairing_url)
        print("Joe : URL d’appairage ouverte dans le navigateur.")
    return 0


def _skills(argv: list[str]) -> int:
    skills_parser = argparse.ArgumentParser(
        prog="joe skills",
        description="List or import project skills shared by all providers.",
    )
    skills_parser.add_argument("action", choices=("list", "import"))
    skills_parser.add_argument("source", nargs="?", type=Path)
    skills_parser.add_argument("-C", "--project", type=Path, default=Path.cwd())
    skills_parser.add_argument("--name")
    skills_parser.add_argument("--provider", default="unknown")
    args = skills_parser.parse_args(argv)
    project = args.project.resolve()
    if args.action == "list":
        print(json.dumps(list_skills(project), ensure_ascii=False, indent=2))
        return 0
    if args.source is None:
        skills_parser.error("skills import requires a source path")
    result = import_skill(
        project,
        args.source,
        name=args.name,
        source_provider=args.provider,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _auth(argv: list[str]) -> int:
    auth_parser = argparse.ArgumentParser(
        prog="joe auth",
        description="Manage the local Joe authentication secret.",
    )
    auth_parser.add_argument("action", choices=("rotate",))
    auth_parser.add_argument("--host", default="127.0.0.1")
    auth_parser.add_argument("--port", type=int, default=8765)
    auth_parser.add_argument("--no-browser", action="store_true")
    args = auth_parser.parse_args(argv)
    url = f"http://{args.host}:{args.port}"
    from .auth import auth_token_path

    try:
        current = auth_token_path().read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            f"{url}/api/auth/rotate",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {current}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(f"joe auth: rotation failed: {exc}", file=sys.stderr)
        return 1
    if not payload.get("rotated"):
        print("joe auth: rotation refused.", file=sys.stderr)
        return 1
    print("Joe: local secret rotated; previous sessions were revoked.")
    if not args.no_browser:
        webbrowser.open(_pairing_url(url))
    return 0


def _doctor(argv: list[str]) -> int:
    doctor_parser = argparse.ArgumentParser(
        prog="joe doctor",
        description="Check Joe storage and local AI provider health.",
    )
    doctor_parser.add_argument("-C", "--project", type=Path, default=Path.cwd())
    doctor_parser.add_argument(
        "--live",
        action="store_true",
        help="send one very short prompt to every installed provider",
    )
    doctor_parser.add_argument("--json", action="store_true")
    args = doctor_parser.parse_args(argv)
    if not args.project.is_dir():
        print(
            f"joe doctor: project directory does not exist: {args.project}",
            file=sys.stderr,
        )
        return 2
    from .doctor import doctor_report, format_doctor

    report = doctor_report(args.project, live=args.live)
    print(
        json.dumps(report, indent=2, ensure_ascii=False)
        if args.json
        else format_doctor(report)
    )
    storage_ok = bool(report["storage"]["writable"])
    live_ok = all(
        item.get("live", {}).get("ok", True) for item in report["providers"]
    )
    return 0 if storage_ok and live_ok else 1


def _sync(argv: list[str]) -> int:
    sync_parser = argparse.ArgumentParser(
        prog="joe sync",
        description="Detect provider CLI updates and optionally adapt Joe.",
    )
    sync_parser.add_argument(
        "--apply",
        action="store_true",
        help="let Codex implement relevant updates, then ask Claude to review",
    )
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="run the implementation audit even when versions are unchanged",
    )
    args = sync_parser.parse_args(argv)

    from .maintenance import format_audit, joe_project, provider_audit, update_request

    results = provider_audit()
    print(format_audit(results))
    changed = any(item["changed"] for item in results)
    if not args.apply:
        print("\nUse `joe sync --apply` to integrate the detected updates.")
        return 1 if changed else 0
    if not changed and not args.force:
        print("\nAucune nouvelle version. Utilise --force pour un audit complet.")
        return 0

    orchestrator = Orchestrator(joe_project())
    request = update_request(results, args.force)
    route = orchestrator.plan(
        request,
        forced_agent="codex",
        forced_mode=Mode.REVIEW,
    )
    try:
        response, log = orchestrator.execute(
            request,
            route,
            execution_mode="workspace-write",
        )
    except OrchestrationError as exc:
        print(f"joe sync: {exc}", file=sys.stderr)
        return 1
    print(f"\n{response}\n\n[joe] log: {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
