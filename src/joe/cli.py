from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .models import Mode
from .orchestrator import OrchestrationError, Orchestrator

PROVIDERS = ("codex", "claude", "gemini", "copilot")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="joe",
        description="Route natural-language project work across local AI CLIs.",
    )
    result.add_argument("request", nargs="*", help="natural-language request")
    result.add_argument("-C", "--project", type=Path, default=Path.cwd())
    result.add_argument("--agent", choices=PROVIDERS, help="force one provider")
    result.add_argument("--mode", choices=[mode.value for mode in Mode])
    result.add_argument("--dry-run", action="store_true", help="show routing only")
    result.add_argument("--version", action="version", version=__version__)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments and sys.stdin.isatty():
        return _web([])
    if arguments and arguments[0] == "web":
        return _web(arguments[1:])
    if arguments and arguments[0] == "sync":
        return _sync(arguments[1:])
    if arguments and arguments[0] == "kill":
        return _kill(arguments[1:])
    if arguments and arguments[0] == "chat":
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
    route = orchestrator.plan(request, forced_agent=agent, forced_mode=mode)
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
        response, log = orchestrator.execute(request, route)
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
    web_parser.add_argument("--no-browser", action="store_true")
    web_parser.add_argument(
        "--foreground",
        action="store_true",
        help="run the server in this terminal instead of tmux",
    )
    args = web_parser.parse_args(argv)
    if not args.project.is_dir():
        print(f"joe: project directory does not exist: {args.project}", file=sys.stderr)
        return 2
    from .web import serve

    url = f"http://{args.host}:{args.port}"
    if not args.foreground and shutil.which("tmux"):
        return _tmux_web(args, url)
    print(f"Joe Web — {args.project.resolve()}\n{url}\nCtrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        serve(args.project, args.host, args.port)
    except KeyboardInterrupt:
        print("\nJoe Web stopped.")
    except OSError as exc:
        print(f"joe: impossible de démarrer le serveur: {exc}", file=sys.stderr)
        return 1
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
        executable = shutil.which("joe")
        if not executable:
            print("joe: exécutable introuvable", file=sys.stderr)
            return 1
        command = [
            executable,
            "web",
            "-C",
            str(args.project.resolve()),
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--no-browser",
            "--foreground",
        ]
        created = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, *command],
            check=False,
        )
        if created.returncode:
            print("joe: impossible de créer la session tmux", file=sys.stderr)
            return created.returncode
    print(
        f"Joe tourne dans tmux · session {session}\n{url}\n"
        f"Console : tmux attach -t {session}"
    )
    if not args.no_browser:
        webbrowser.open(url)
    return 0


def _kill(argv: list[str]) -> int:
    kill_parser = argparse.ArgumentParser(
        prog="joe kill",
        description="Stop every tmux session started by Joe.",
    )
    kill_parser.parse_args(argv)
    if not shutil.which("tmux"):
        print("Joe : tmux n’est pas installé.")
        return 0
    listed = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
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
        print("Joe : aucune session tmux active.")
    else:
        suffix = "s" if stopped > 1 else ""
        print(f"Joe : {stopped} session{suffix} tmux arrêtée{suffix}.")
    return 0


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
        print("\nUtilise `joe sync --apply` pour intégrer les nouveautés détectées.")
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
