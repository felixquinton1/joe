from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
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
                route.primary, context, route.intent, results, model=model,
                effort=effort, execution_mode=execution_mode, on_event=on_event,
                cancel_event=cancel_event,
                timeout_override=30 if health_check else None,
                allow_fallback=not health_check,
            )
            final = result.stdout.strip()
            final_provider = result.provider
        elif route.mode is Mode.REVIEW:
            self._workflow_event(
                on_event, "review", "implementation", route.primary,
                "Implémentation principale", "running",
            )
            primary = self._run_with_fallback(
                route.primary, context, route.intent, results, model=model,
                effort=effort, execution_mode=execution_mode, on_event=on_event,
                cancel_event=cancel_event,
            )
            self._workflow_event(
                on_event, "review", "implementation", primary.provider,
                "Implémentation principale", "complete", primary.stdout,
            )
            reviewer = route.reviewer or ("claude" if primary.provider == "codex" else "codex")
            review_prompt = self._review_prompt(context, primary.stdout)
            self._workflow_event(
                on_event, "review", "review", reviewer,
                "Revue indépendante", "running",
            )
            review = self._run_with_fallback(
                reviewer, review_prompt, Intent.ANALYZE, results,
                on_event=on_event,
                cancel_event=cancel_event,
            )
            self._workflow_event(
                on_event, "review", "review", review.provider,
                "Revue indépendante", "complete", review.stdout,
            )
            correction = None
            if (
                route.intent is Intent.MODIFY
                and self._review_requires_correction(review.stdout)
            ):
                self._workflow_event(
                    on_event, "review", "correction", primary.provider,
                    "Corrections justifiées", "running",
                )
                correction = self._run_with_fallback(
                    primary.provider,
                    self._correction_prompt(context, primary.stdout, review.stdout),
                    Intent.MODIFY,
                    results,
                    model=model,
                    effort=effort,
                    execution_mode=execution_mode,
                    on_event=on_event,
                    cancel_event=cancel_event,
                )
                self._workflow_event(
                    on_event, "review", "correction", correction.provider,
                    "Corrections justifiées", "complete", correction.stdout,
                )
            final = self._review_final(primary, review, correction)
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
            if on_event:
                on_event({"type": "provider_start", "provider": name})
            result = self.providers[name].run(
                prompt,
                self.project,
                intent,
                timeout_override or int(config["timeout_seconds"]),
                model=model if name == provider_name else None,
                effort=effort if name == provider_name else None,
                execution_mode=(
                    execution_mode if name == provider_name else None
                ),
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
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, str]:
        proposal_prompt = (
            context
            + "\n\nPropose independently a solution. Do not modify files. "
            "State assumptions, trade-offs, and validation."
        )
        self._workflow_event(
            on_event, "consensus", "proposal_codex", "codex",
            "Proposition indépendante", "running",
        )
        self._workflow_event(
            on_event, "consensus", "proposal_claude", "claude",
            "Proposition indépendante", "running",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            codex_future = executor.submit(
                self._isolated_run,
                "codex",
                proposal_prompt,
                Intent.ANALYZE,
                {"claude"},
                model if selected_provider == "codex" else None,
                effort if selected_provider == "codex" else None,
                execution_mode if selected_provider == "codex" else None,
                cancel_event,
                on_event,
            )
            claude_future = executor.submit(
                self._isolated_run,
                "claude",
                proposal_prompt,
                Intent.ANALYZE,
                {"codex"},
                model if selected_provider == "claude" else None,
                effort if selected_provider == "claude" else None,
                execution_mode if selected_provider == "claude" else None,
                cancel_event,
                on_event,
            )
            codex, codex_results = codex_future.result()
            claude, claude_results = claude_future.result()
        results.extend(codex_results)
        results.extend(claude_results)
        self._workflow_event(
            on_event, "consensus", "proposal_codex", codex.provider,
            "Proposition indépendante", "complete", codex.stdout,
        )
        self._workflow_event(
            on_event, "consensus", "proposal_claude", claude.provider,
            "Proposition indépendante", "complete", claude.stdout,
        )
        self._workflow_event(
            on_event, "consensus", "review_codex", "codex",
            "Examen de la proposition de Claude", "running",
        )
        self._workflow_event(
            on_event, "consensus", "review_claude", "claude",
            "Examen de la proposition de Codex", "running",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            codex_review_future = executor.submit(
                self._isolated_run,
                "codex",
                self._review_prompt(context, claude.stdout),
                Intent.ANALYZE,
                {"claude"},
                None,
                None,
                None,
                cancel_event,
                on_event,
            )
            claude_review_future = executor.submit(
                self._isolated_run,
                "claude",
                self._review_prompt(context, codex.stdout),
                Intent.ANALYZE,
                {"codex"},
                None,
                None,
                None,
                cancel_event,
                on_event,
            )
            codex_review, codex_review_results = codex_review_future.result()
            claude_review, claude_review_results = claude_review_future.result()
        results.extend(codex_review_results)
        results.extend(claude_review_results)
        self._workflow_event(
            on_event, "consensus", "review_codex", codex_review.provider,
            "Examen de la proposition de Claude", "complete",
            codex_review.stdout,
        )
        self._workflow_event(
            on_event, "consensus", "review_claude", claude_review.provider,
            "Examen de la proposition de Codex", "complete",
            claude_review.stdout,
        )
        synthesis_prompt = (
            context
            + "\n\nSynthesize the following independent proposals and cross-reviews. "
            "Resolve disagreements explicitly and produce one actionable recommendation. "
            "Return clean Markdown with complete lines and valid tables. Do not describe "
            "the orchestration mechanism, invent extra agents, or modify files.\n\n"
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
        self._workflow_event(
            on_event, "consensus", "synthesis", "gemini",
            "Synthèse du consensus", "running",
        )
        synthesis = self._run_with_fallback(
            "gemini", synthesis_prompt, Intent.ANALYZE, results,
            on_event=on_event,
            cancel_event=cancel_event,
        )
        self._workflow_event(
            on_event, "consensus", "synthesis", synthesis.provider,
            "Synthèse du consensus", "complete",
        )
        return synthesis.stdout.strip(), synthesis.provider

    def _isolated_run(
        self,
        provider: str,
        prompt: str,
        intent: Intent,
        exclude: set[str],
        model: str | None,
        effort: str | None,
        execution_mode: str | None,
        cancel_event: threading.Event | None,
        on_event: Callable[[dict], None] | None,
    ) -> tuple[ProviderResult, list[ProviderResult]]:
        local_results: list[ProviderResult] = []
        result = self._run_with_fallback(
            provider,
            prompt,
            intent,
            local_results,
            exclude,
            model=model,
            effort=effort,
            execution_mode=execution_mode,
            cancel_event=cancel_event,
            on_event=on_event,
        )
        return result, local_results

    @staticmethod
    def _review_prompt(context: str, candidate: str) -> str:
        return (
            context
            + "\n\nReview the candidate below and inspect the repository state. "
            "Do not modify files. Start with exactly `VERDICT: APPROVED` or "
            "`VERDICT: CORRECTIONS_REQUIRED`. Identify only "
            "material correctness, safety, maintainability, or validation issues. "
            "Say explicitly if no justified issue exists.\n\n<CANDIDATE>\n"
            + candidate
            + "\n</CANDIDATE>"
        )

    @staticmethod
    def _review_requires_correction(review: str) -> bool:
        return "VERDICT: CORRECTIONS_REQUIRED" in review.upper()

    @staticmethod
    def _correction_prompt(
        context: str,
        implementation: str,
        review: str,
    ) -> str:
        return (
            context
            + "\n\nA reviewer audited the implementation below. Re-check every "
            "finding, apply only justified corrections, run focused validation, "
            "and report the final result. This is the only correction pass.\n\n"
            "<IMPLEMENTATION>\n"
            + implementation
            + "\n</IMPLEMENTATION>\n\n<REVIEW>\n"
            + review
            + "\n</REVIEW>"
        )

    @staticmethod
    def _review_final(
        primary: ProviderResult,
        review: ProviderResult,
        correction: ProviderResult | None,
    ) -> str:
        result = correction.stdout.strip() if correction else primary.stdout.strip()
        status = (
            "Les corrections justifiées ont été appliquées et vérifiées."
            if correction
            else "La revue n’a demandé aucune correction justifiée."
        )
        return (
            result
            + "\n\n## Contrôle croisé\n\n"
            + status
            + "\n\n### Avis de "
            + review.provider.capitalize()
            + "\n\n"
            + review.stdout.strip()
        )

    @staticmethod
    def _workflow_event(
        callback: Callable[[dict], None] | None,
        mode: str,
        stage: str,
        provider: str,
        label: str,
        status: str,
        content: str | None = None,
    ) -> None:
        if not callback:
            return
        event = {
            "type": "workflow_update",
            "mode": mode,
            "stage": stage,
            "provider": provider,
            "label": label,
            "status": status,
        }
        if content is not None:
            event["content"] = content
        callback(event)


def _extract_files(text: str) -> list[str]:
    candidates = re.findall(
        r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+|(?<![\w])[\w.-]+\.(?:py|md|toml|yaml|yml|json)",
        text,
    )
    return list(dict.fromkeys(candidates))[:30]
