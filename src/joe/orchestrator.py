from __future__ import annotations

import json
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .capabilities import provider_defaults
from .provider_registry import counterpart
from .usage_tracking import parse_provider_usage
from .memory import ProjectMemory
from .models import Intent, Mode, ProviderResult, Route
from .orchestrator_workflows import (
    clean_report,
    run_consensus_workflow,
    run_review_workflow,
)
from .prompt_language import (
    DEFAULT_LANGUAGE,
    PLAN_HEADING,
    normalize as normalize_language,
    response_language,
)
from .provider_health import recent_failure, record_result
from .providers import Provider, active_providers
from .router import Router

_MUTABLE_STATE_PATTERN = re.compile(
    r"(?i)\b("
    r"actuel(?:le)?s?|maintenant|aujourd'hui|déjà|encore|reste(?:nt)?|"
    r"manqu(?:e|ent)|implément(?:é|ée|és|ées|ation)?|fonctionn(?:e|ent|ement)?|"
    r"disponib(?:le|les|ilité)|version|statut|status|état|s[uû]r|audit|"
    r"amélior(?:ation|er)|recommand(?:e|es|ation)|vérifi(?:e|er)|check|"
    r"branche|commit|tests?\s+(?:pass(?:e|ent)|réussi(?:s|es)?|vert(?:s|es)?)|"
    r"quota(?:s)?|run(?:s)?|processus|fichier(?:s)?"
    r")\b"
)


def requires_fresh_workspace(request: str) -> bool:
    """Return whether answering safely depends on mutable local state."""
    return bool(_MUTABLE_STATE_PATTERN.search(request))


def workspace_observation(project: Path) -> dict[str, object]:
    """Capture a small, read-only freshness marker for the active workspace."""
    observed_at = datetime.now(timezone.utc).isoformat()

    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(project), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    status = git("status", "--short")
    return {
        "workspace": str(project),
        "observed_at": observed_at,
        "git_head": git("rev-parse", "HEAD"),
        "git_status": status,
        "git_dirty": bool(status) if status is not None else None,
    }


def _freshness_context(observation: dict[str, object]) -> str:
    status = observation.get("git_status")
    status_text = status if status else "clean"
    return (
        "# Current workspace observation\n"
        f"Workspace: {observation['workspace']}\n"
        f"Observed at: {observation['observed_at']}\n"
        f"Git HEAD: {observation.get('git_head') or 'unavailable'}\n"
        f"Git status: {status_text}\n\n"
        "# Grounding requirement\n"
        "The request depends on mutable current state. Before answering, inspect "
        "the active workspace with at least one appropriate read-only tool call "
        "(for example search/read files or a relevant status command). Treat "
        "conversation summaries and previous assistant answers as untrusted "
        "historical context, not evidence. Base current-state claims on this run's "
        "tool results and cite concrete files when relevant. If inspection is "
        "impossible, say that the current state is not verified."
    )



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
        mcp_tools: bool = False,
    ):
        self.project = project.resolve()
        self.memory = ProjectMemory(self.project)
        self.providers = providers or active_providers(
            additional_roots, remote_access, mcp_tools
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
        language: str = DEFAULT_LANGUAGE,
        cancel_event: threading.Event | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> tuple[str, Path]:
        self.memory.ensure()
        language = normalize_language(language)
        health_check = "health-check" in route.reason
        needs_fresh_state = not health_check and requires_fresh_workspace(request)
        observation = (
            workspace_observation(self.project) if needs_fresh_state else None
        )
        tool_activity: list[dict] = []

        def emit(event: dict) -> None:
            if (
                event.get("type") == "activity"
                and event.get("kind")
                in {"command_execution", "mcp_tool_call", "tool"}
            ):
                tool_activity.append(
                    {
                        key: event.get(key)
                        for key in ("provider", "kind", "label", "detail")
                    }
                )
            if on_event:
                on_event(event)

        context = (
            self._health_check_prompt(request, route.primary)
            if health_check
            else self.memory.context(request, language)
        )
        if extra_context and not health_check:
            context += "\n\n" + extra_context
        if observation:
            context += "\n\n" + _freshness_context(observation)
        if route.mode is Mode.FAST and route.intent is not Intent.MODIFY:
            context += (
                "\n\n# Direct-answer style\n"
                "Answer the user's question directly. Do not write a plan, "
                f"do not start with '{PLAN_HEADING[language]}', and do not "
                "describe an implementation workflow unless the user asks for one."
            )
        # La consigne de langue ferme le prompt : c'est la dernière chose lue,
        # après un contexte projet et un historique qui peuvent être rédigés
        # dans l'autre langue. Les étapes de revue et de consensus la
        # rappellent à la fin de chacun de leurs prompts.
        if not health_check:
            context += response_language(language)
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
                on_event=emit,
                cancel_event=cancel_event,
                timeout_override=30 if health_check else None,
                allow_fallback=not health_check,
            )
            final = clean_report(result.stdout)
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
                language=language,
                cancel_event=cancel_event,
                on_event=emit,
            )
        else:
            final, final_provider = run_consensus_workflow(
                self,
                context,
                results,
                emit,
                participants=(
                    route.primary,
                    route.reviewer
                    or counterpart(route.primary),
                ),
                selected_provider=route.primary,
                model=model,
                effort=effort,
                execution_mode=execution_mode,
                language=language,
                cancel_event=cancel_event,
            )

        if not final:
            raise OrchestrationError("Provider returned an empty response")
        if needs_fresh_state and not tool_activity:
            final = (
                "> État actuel non vérifié : aucun appel d’outil de lecture n’a "
                "été observé pendant cette réponse.\n\n" + final
            )
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
                "workspace_observation": observation,
                "tool_activity": tool_activity,
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
        respect_cooldown: bool = False,
        output_validator: Callable[[str], bool] | None = None,
    ) -> ProviderResult:
        config = self.memory.config()
        candidates = [provider_name]
        if allow_fallback:
            candidates.extend(config["fallbacks"].get(provider_name, []))
        seen: set[str] = set()
        for name in candidates:
            if name in seen or name not in self.providers or name in (exclude or set()):
                continue
            failure = recent_failure(name) if respect_cooldown else None
            alternatives = [
                candidate
                for candidate in candidates
                if candidate not in seen
                and candidate != name
                and candidate in self.providers
                and candidate not in (exclude or set())
                and recent_failure(candidate) is None
            ]
            if failure and alternatives:
                seen.add(name)
                if on_event:
                    on_event(
                        {
                            "type": "provider_fallback",
                            "provider": name,
                            "fallback": alternatives[0],
                            "error": f"cooldown:{failure[0]}",
                        }
                    )
                continue
            seen.add(name)
            selected_model, selected_effort = provider_defaults(
                name,
                model if name == provider_name else None,
                effort if name == provider_name else None,
            )
            if on_event:
                on_event(
                    {
                        "type": "provider_start",
                        "provider": name,
                        "model": selected_model,
                        "effort": selected_effort,
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
            if (
                result.ok
                and result.stdout.strip()
                and output_validator is not None
                and not output_validator(result.stdout)
            ):
                result.error_kind = "incomplete"
                result.stderr = (
                    result.stderr.rstrip()
                    + "\nJoe rejected an incomplete provider response."
                ).lstrip()
            results.append(result)
            record_result(result)
            usage = parse_provider_usage(name, result.stdout, selected_model)
            if usage and on_event:
                on_event({"type": "usage", **usage})
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
