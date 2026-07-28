from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
            if on_event:
                on_event({"type": "provider_start", "provider": name})
            result = self.providers[name].run(
                prompt,
                self.project,
                intent,
                timeout_override or int(config["timeout_seconds"]),
                model=model if name == provider_name else None,
                effort=effort if name == provider_name else None,
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

    def _consensus(
        self,
        context: str,
        results: list[ProviderResult],
        on_event: Callable[[dict], None] | None = None,
        *,
        participants: tuple[str, str] = ("codex", "claude"),
        selected_provider: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        execution_mode: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, str]:
        proposal_prompt = (
            context
            + "\n\nPropose independently a solution. Do not modify files. "
            "State assumptions, trade-offs, and validation. Consensus stages are "
            "intentionally read-only: do not attempt test suites, git fetch, or "
            "authentication probes, and do not add a generic Refusé section merely "
            "because those operational checks belong to a REVIEW workflow."
        )
        first, second = participants
        for provider in participants:
            self._workflow_event(
                on_event, "consensus", f"proposal_{provider}", provider,
                "Proposition indépendante", "running",
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(
                    self._isolated_run,
                    provider,
                    proposal_prompt,
                    Intent.ANALYZE,
                    {other},
                    model if selected_provider == provider else None,
                    effort if selected_provider == provider else None,
                    None,
                    cancel_event,
                    on_event,
                    True,
                ): provider
                for provider, other in ((first, second), (second, first))
            }
            proposals = {}
            proposal_results = {}
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    proposal, local_results = future.result()
                except OrchestrationError as error:
                    self._workflow_event(
                        on_event,
                        "consensus",
                        f"proposal_{provider}",
                        provider,
                        "Proposition indépendante",
                        "failed",
                        str(error),
                    )
                    raise
                proposals[provider] = proposal
                proposal_results[provider] = local_results
                self._workflow_event(
                    on_event,
                    "consensus",
                    f"proposal_{provider}",
                    proposal.provider,
                    "Proposition indépendante",
                    "complete",
                    proposal.stdout,
                )
        first_proposal = proposals[first]
        second_proposal = proposals[second]
        degraded_providers = {
            role: proposal.provider
            for role, proposal in proposals.items()
            if proposal.provider != role
        }
        results.extend(proposal_results[first])
        results.extend(proposal_results[second])
        for provider, other in ((first, second), (second, first)):
            self._workflow_event(
                on_event, "consensus", f"review_{provider}", provider,
                f"Examen de la proposition de {other.capitalize()}", "running",
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(
                    self._isolated_run,
                    provider,
                    self._review_prompt(
                        context,
                        (
                            second_proposal.stdout
                            if provider == first
                            else first_proposal.stdout
                        ),
                    ),
                    Intent.ANALYZE,
                    {other},
                    None,
                    None,
                    None,
                    cancel_event,
                    on_event,
                    True,
                ): provider
                for provider, other in ((first, second), (second, first))
            }
            reviews = {}
            review_results = {}
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    review, local_results = future.result()
                except OrchestrationError as error:
                    self._workflow_event(
                        on_event,
                        "consensus",
                        f"review_{provider}",
                        provider,
                        f"Examen de la proposition de "
                        f"{(second if provider == first else first).capitalize()}",
                        "failed",
                        str(error),
                    )
                    raise
                reviews[provider] = review
                review_results[provider] = local_results
                self._workflow_event(
                    on_event,
                    "consensus",
                    f"review_{provider}",
                    review.provider,
                    f"Examen de la proposition de "
                    f"{(second if provider == first else first).capitalize()}",
                    "complete",
                    review.stdout,
                )
        first_review = reviews[first]
        second_review = reviews[second]
        degraded_providers.update(
            {
                f"revue {role}": review.provider
                for role, review in reviews.items()
                if review.provider != role
            }
        )
        results.extend(review_results[first])
        results.extend(review_results[second])
        synthesis_prompt = (
            context
            + "\n\nSynthesize the following independent proposals and cross-reviews. "
            "Resolve disagreements explicitly and produce one actionable recommendation. "
            "Return clean Markdown with complete lines and valid tables. Do not describe "
            "the orchestration mechanism, invent extra agents, or modify files.\n\n"
            + json.dumps(
                {
                    f"{first}_proposal": first_proposal.stdout,
                    f"{second}_proposal": second_proposal.stdout,
                    f"{first}_review": first_review.stdout,
                    f"{second}_review": second_review.stdout,
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
        final = synthesis.stdout.strip()
        if degraded_providers:
            replacements = ", ".join(
                f"{role.capitalize()} indisponible, relais par "
                f"{provider.capitalize()}"
                for role, provider in degraded_providers.items()
            )
            final = f"> ⚠️ **Consensus dégradé** — {replacements}.\n\n{final}"
        return final, synthesis.provider

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
        allow_quota_fallback: bool = False,
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
            allow_fallback=allow_quota_fallback,
            fallback_error_kinds={"quota"} if allow_quota_fallback else None,
        )
        return result, local_results

    @staticmethod
    def _review_prompt(context: str, candidate: str) -> str:
        return (
            context
            + "\n\nReview the candidate below and inspect the repository state. "
            "Do not modify files. Do not rerun commands that require write or "
            "network access; evaluate the candidate's recorded evidence instead. "
            "Start with exactly `VERDICT: APPROVED` or "
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
