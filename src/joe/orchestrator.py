from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .memory import ProjectMemory
from .models import Intent, Mode, ProviderResult, Route
from .orchestrator_workflows import (
    run_consensus_workflow,
    run_review_workflow,
)
from .providers import Provider, default_providers
from .router import Router


class OrchestrationError(RuntimeError):
    pass


class Orchestrator:
    def __init__(
        self,
        project: Path,
        *,
        providers: dict[str, Provider] | None = None,
        router: Router | None = None,
        additional_roots: tuple[Path, ...] = (),
        remote_access: bool = False,
    ):
        self.project = project.resolve()
        self.memory = ProjectMemory(self.project)
        self.providers = providers or default_providers(
            additional_roots, remote_access
        )
        self.router = router or Router()

    def plan(
        self,
        request: str,
        *,
        forced_agent: str | None = None,
        forced_mode: Mode | None = None,
    ) -> Route:
        return self.router.route(
            request,
            forced_agent=forced_agent,
            forced_mode=forced_mode,
            previous_provider=self.memory.previous_provider(),
        )

    def execute(
        self,
        request: str,
        route: Route,
        *,
        model: str | None = None,
        effort: str | None = None,
        execution_mode: str | None = None,
        extra_context: str | None = None,
        cancel_event: threading.Event | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> tuple[str, Path]:
        self.memory.ensure()
        health_check = "health-check" in route.reason
        context = (
            self._health_check_prompt(request, route.primary)
            if health_check
            else self.memory.context(request)
        )
        if extra_context and not health_check:
            context += "\n\n" + extra_context
        results: list[ProviderResult] = []

        if route.mode is Mode.FAST:
            result = self._run_with_fallback(
                route.primary,
                context,
                route.intent,
                results,
                model=model,
                effort=effort,
                execution_mode=execution_mode,
                on_event=on_event,
                cancel_event=cancel_event,
                timeout_override=30 if health_check else None,
                allow_fallback=not health_check,
            )
            final = result.stdout.strip()
            final_provider = result.provider
        elif route.mode is Mode.REVIEW:
            final, final_provider = run_review_workflow(
                self,
                context,
                route,
                results,
                model=model,
                effort=effort,
                execution_mode=execution_mode,
                cancel_event=cancel_event,
                on_event=on_event,
            )
        else:
            final, final_provider = run_consensus_workflow(
                self,
                context,
                results,
                on_event,
                participants=(
                    route.primary,
                    route.reviewer
                    or ("claude" if route.primary == "codex" else "codex"),
                ),
                selected_provider=route.primary,
                model=model,
                effort=effort,
                execution_mode=execution_mode,
                cancel_event=cancel_event,
            )

        if not final:
            raise OrchestrationError("Provider returned an empty response")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        files = _extract_files(final)
        log = self.memory.save_run(
            run_id,
            {
                "request": request,
                "route": {
                    "intent": route.intent.value,
                    "mode": route.mode.value,
                    "primary": route.primary,
                    "reviewer": route.reviewer,
                    "reason": route.reason,
                },
                "results": [
                    {
                        **item.metadata(),
                        "stdout": item.stdout,
                        "stderr": item.stderr,
                    }
                    for item in results
                ],
                "final": final,
            },
        )
        self.memory.update_active(
            request=request,
            provider=final_provider,
            mode=route.mode.value,
            response=final,
            files=files,
        )
        return final, log

    def _run_with_fallback(
        self,
        provider_name: str,
        prompt: str,
        intent: Intent,
        results: list[ProviderResult],
        exclude: set[str] | None = None,
        model: str | None = None,
        effort: str | None = None,
        execution_mode: str | None = None,
        cancel_event: threading.Event | None = None,
        on_event: Callable[[dict], None] | None = None,
        timeout_override: int | None = None,
        allow_fallback: bool = True,
        fallback_error_kinds: set[str] | None = None,
    ) -> ProviderResult:
        config = self.memory.config()
        candidates = [provider_name]
        if allow_fallback:
            candidates.extend(config["fallbacks"].get(provider_name, []))
        seen: set[str] = set()
        for name in candidates:
            if name in seen or name not in self.providers or name in (exclude or set()):
                continue
            seen.add(name)
            selected_model = model if name == provider_name else None
            selected_effort = effort if name == provider_name else None
            if on_event:
                version_reader = getattr(
                    self.providers[name], "cli_version", None
                )
                on_event(
                    {
                        "type": "provider_start",
                        "provider": name,
                        "model": selected_model or "défaut fournisseur",
                        "effort": selected_effort or "défaut",
                        "cli_version": (
                            version_reader() if callable(version_reader) else None
                        ),
                    }
                )
            result = self.providers[name].run(
                prompt,
                self.project,
                intent,
                timeout_override or int(config["timeout_seconds"]),
                model=selected_model,
                effort=selected_effort,
                execution_mode=execution_mode,
                cancel_event=cancel_event,
                on_stream=(
                    lambda stream, text, provider=name: on_event(
                        (
                            {
                                "type": "activity",
                                "provider": provider,
                                **json.loads(text),
                            }
                            if stream == "activity"
                            else {
                                "type": "stream",
                                "provider": provider,
                                "stream": stream,
                                "text": text,
                            }
                        )
                    )
                    if on_event
                    else None
                ),
            )
            results.append(result)
            if result.error_kind == "cancelled":
                raise OrchestrationError("Exécution interrompue")
            if on_event:
                on_event(
                    {
                        "type": "provider_end",
                        "provider": name,
                        "ok": result.ok,
                        "error": result.error_kind,
                    }
                )
            if result.ok and result.stdout.strip():
                return result
            if (
                fallback_error_kinds is not None
                and result.error_kind not in fallback_error_kinds
            ):
                break
            if allow_fallback and on_event:
                next_provider = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate not in seen
                        and candidate in self.providers
                        and candidate not in (exclude or set())
                    ),
                    None,
                )
                if next_provider:
                    on_event(
                        {
                            "type": "provider_fallback",
                            "provider": name,
                            "fallback": next_provider,
                            "error": result.error_kind,
                        }
                    )
        errors = ", ".join(f"{r.provider}:{r.error_kind}" for r in results)
        raise OrchestrationError(f"All providers failed ({errors})")

    @staticmethod
    def _health_check_prompt(request: str, provider: str) -> str:
        return (
            f"Minimal availability test for {provider}. Do not inspect files, "
            "run tools, or load project context. Calculate 17 × 23 and answer "
            "in one short French sentence with the result. Original request: "
            + request
        )


def _extract_files(text: str) -> list[str]:
    candidates = re.findall(
        r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+|(?<![\w])[\w.-]+\.(?:py|md|toml|yaml|yml|json)",
        text,
    )
    return list(dict.fromkeys(candidates))[:30]
