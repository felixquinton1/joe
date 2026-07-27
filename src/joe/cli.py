from __future__ import annotations

import argparse
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
    args = web_parser.parse_args(argv)
    if not args.project.is_dir():
        print(f"joe: project directory does not exist: {args.project}", file=sys.stderr)
        return 2
    from .web import serve

    url = f"http://{args.host}:{args.port}"
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


if __name__ == "__main__":
    raise SystemExit(main())
