from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .memory import ProjectMemory
from .models import Intent, Mode, ProviderResult, Route
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
    ):
        self.project = project.resolve()
        self.memory = ProjectMemory(self.project)
        self.providers = providers or default_providers()
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
        on_event: Callable[[dict], None] | None = None,
    ) -> tuple[str, Path]:
        self.memory.ensure()
        context = self.memory.context(request)
        if extra_context:
            context += "\n\n" + extra_context
        results: list[ProviderResult] = []

        if route.mode is Mode.FAST:
            result = self._run_with_fallback(
                route.primary, context, route.intent, results, model=model,
                effort=effort, execution_mode=execution_mode, on_event=on_event,
            )
            final = result.stdout.strip()
            final_provider = result.provider
        elif route.mode is Mode.REVIEW:
            primary = self._run_with_fallback(
                route.primary, context, route.intent, results, model=model,
                effort=effort, execution_mode=execution_mode, on_event=on_event,
            )
            reviewer = route.reviewer or ("claude" if primary.provider == "codex" else "codex")
            review_prompt = self._review_prompt(context, primary.stdout)
            review = self._run_with_fallback(
                reviewer, review_prompt, Intent.ANALYZE, results,
                on_event=on_event,
            )
            final = primary.stdout.strip() + "\n\n---\nReview by " + review.provider + ":\n" + review.stdout.strip()
            final_provider = primary.provider
        else:
            final, final_provider = self._consensus(
                context,
                results,
                on_event,
                selected_provider=route.primary,
                model=model,
                effort=effort,
                execution_mode=execution_mode,
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
        on_event: Callable[[dict], None] | None = None,
    ) -> ProviderResult:
        config = self.memory.config()
        candidates = [provider_name, *config["fallbacks"].get(provider_name, [])]
        seen: set[str] = set()
        for name in candidates:
            if name in seen or name not in self.providers or name in (exclude or set()):
                continue
            seen.add(name)
            if on_event:
                on_event({"type": "provider_start", "provider": name})
            result = self.providers[name].run(
                prompt,
                self.project,
                intent,
                int(config["timeout_seconds"]),
                model=model if name == provider_name else None,
                effort=effort if name == provider_name else None,
                execution_mode=(
                    execution_mode if name == provider_name else None
                ),
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
        errors = ", ".join(f"{r.provider}:{r.error_kind}" for r in results)
        raise OrchestrationError(f"All providers failed ({errors})")

    def _consensus(
        self,
        context: str,
        results: list[ProviderResult],
        on_event: Callable[[dict], None] | None = None,
        *,
        selected_provider: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        execution_mode: str | None = None,
    ) -> tuple[str, str]:
        proposal_prompt = (
            context
            + "\n\nPropose independently a solution. Do not modify files. "
            "State assumptions, trade-offs, and validation."
        )
        codex = self._run_with_fallback(
            "codex", proposal_prompt, Intent.ANALYZE, results,
            model=model if selected_provider == "codex" else None,
            effort=effort if selected_provider == "codex" else None,
            execution_mode=execution_mode if selected_provider == "codex" else None,
            on_event=on_event,
        )
        claude = self._run_with_fallback(
            "claude", proposal_prompt, Intent.ANALYZE, results, {codex.provider},
            model=model if selected_provider == "claude" else None,
            effort=effort if selected_provider == "claude" else None,
            execution_mode=execution_mode if selected_provider == "claude" else None,
            on_event=on_event,
        )
        codex_review = self._run_with_fallback(
            "codex",
            self._review_prompt(context, claude.stdout),
            Intent.ANALYZE,
            results,
            {claude.provider},
            on_event=on_event,
        )
        claude_review = self._run_with_fallback(
            "claude",
            self._review_prompt(context, codex.stdout),
            Intent.ANALYZE,
            results,
            {codex.provider},
            on_event=on_event,
        )
        synthesis_prompt = (
            context
            + "\n\nSynthesize the following independent proposals and cross-reviews. "
            "Resolve disagreements explicitly and produce one actionable recommendation. "
            "Do not modify files.\n\n"
            + json.dumps(
                {
                    "codex_proposal": codex.stdout,
                    "claude_proposal": claude.stdout,
                    "codex_review": codex_review.stdout,
                    "claude_review": claude_review.stdout,
                },
                ensure_ascii=False,
            )
        )
        synthesis = self._run_with_fallback(
            "gemini", synthesis_prompt, Intent.ANALYZE, results,
            on_event=on_event,
        )
        return synthesis.stdout.strip(), synthesis.provider

    @staticmethod
    def _review_prompt(context: str, candidate: str) -> str:
        return (
            context
            + "\n\nReview the candidate below. Do not modify files. Identify only "
            "material correctness, safety, maintainability, or validation issues. "
            "Say explicitly if no justified issue exists.\n\n<CANDIDATE>\n"
            + candidate
            + "\n</CANDIDATE>"
        )


def _extract_files(text: str) -> list[str]:
    candidates = re.findall(
        r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+|(?<![\w])[\w.-]+\.(?:py|md|toml|yaml|yml|json)",
        text,
    )
    return list(dict.fromkeys(candidates))[:30]
