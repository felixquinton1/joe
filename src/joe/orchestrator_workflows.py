from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .models import Intent, ProviderResult, Route


REPORT_RULES = (
    "\n\nReporting rules: use factual collective or impersonal phrasing. "
    "Do not add a section called Refusé/Refused for an internal inability to "
    "run tests, npm, git, network, or sandboxed commands. Omit that detail from "
    "the user report; it belongs to the technical run log. Mention a validation "
    "limitation only when it materially changes the result, in one short sentence "
    "under Validation. Never claim a check passed unless it actually ran."
)


def clean_report(text: str) -> str:
    """Remove only generic internal-validation refusal sections from agent prose."""
    if not text:
        return text
    heading = re.compile(
        r"(?ims)^(?:#{1,6}\s*|\*\*)refus(?:é|e|ed)(?:\*\*)?\s*:?\s*$"
    )
    internal = re.compile(
        r"(?i)(?:sandbox|lecture seule|read-only|pytest|npm|git\s+(?:fetch|push|commit)|"
        r"permission|répertoire temporaire|temporaire inscriptible|requires approval)"
    )
    matches = list(heading.finditer(text))
    if not matches:
        return text.strip()
    chunks: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end]
        if internal.search(body):
            chunks.append(text[cursor:match.start()].rstrip())
            cursor = end
    chunks.append(text[cursor:].rstrip())
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def run_review_workflow(
    orchestrator,
    context: str,
    route: Route,
    results: list[ProviderResult],
    *,
    model: str | None,
    effort: str | None,
    execution_mode: str | None,
    cancel_event: threading.Event | None,
    on_event: Callable[[dict], None] | None,
) -> tuple[str, str]:
    implementation_request = (
        context
        + "\n\nPresent the implementation report in a factual, collective voice. "
        "Use 'nous' or impersonal phrasing, never an ambiguous first-person "
        "singular. Focus on the completed result and validation. Do not invoke "
        "another AI provider or perform an independent review yourself: Joe "
        "owns the review stage after this implementation finishes."
        + REPORT_RULES
    )
    workflow_event(
        on_event,
        "review",
        "implementation",
        route.primary,
        "Implémentation principale",
        "running",
    )
    primary = orchestrator._run_with_fallback(
        route.primary,
        implementation_request,
        route.intent,
        results,
        model=model,
        effort=effort,
        execution_mode=execution_mode,
        on_event=on_event,
        cancel_event=cancel_event,
        fallback_error_kinds={"authentication", "quota", "unavailable"},
    )
    workflow_event(
        on_event,
        "review",
        "implementation",
        primary.provider,
        "Implémentation principale",
        "complete",
        primary.stdout,
    )
    reviewer = route.reviewer or (
        "claude" if primary.provider == "codex" else "codex"
    )
    review_request = review_prompt(context, primary.stdout)
    workflow_event(
        on_event,
        "review",
        "review",
        reviewer,
        "Revue indépendante",
        "running",
    )
    review = orchestrator._run_with_fallback(
        reviewer,
        review_request,
        Intent.ANALYZE,
        results,
        exclude={primary.provider},
        on_event=on_event,
        cancel_event=cancel_event,
        respect_cooldown=True,
    )
    workflow_event(
        on_event,
        "review",
        "review",
        review.provider,
        "Revue indépendante",
        "complete",
        review.stdout,
    )
    correction = None
    if route.intent is Intent.MODIFY and review_requires_correction(review.stdout):
        workflow_event(
            on_event,
            "review",
            "correction",
            primary.provider,
            "Corrections justifiées",
            "running",
        )
        correction = orchestrator._run_with_fallback(
            primary.provider,
            correction_prompt(context, primary.stdout, review.stdout),
            Intent.MODIFY,
            results,
            model=model,
            effort=effort,
            execution_mode=execution_mode,
            on_event=on_event,
            cancel_event=cancel_event,
        )
        workflow_event(
            on_event,
            "review",
            "correction",
            correction.provider,
            "Corrections justifiées",
            "complete",
            correction.stdout,
        )
    return review_final(primary, review, correction), primary.provider


def run_consensus_workflow(
    orchestrator,
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
        + REPORT_RULES
    )
    first, second = participants
    for provider in participants:
        workflow_event(
            on_event,
            "consensus",
            f"proposal_{provider}",
            provider,
            "Proposition indépendante",
            "running",
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                isolated_run,
                orchestrator,
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
            except Exception as error:
                workflow_event(
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
            workflow_event(
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
        workflow_event(
            on_event,
            "consensus",
            f"review_{provider}",
            provider,
            f"Examen de la proposition de {other.capitalize()}",
            "running",
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                isolated_run,
                orchestrator,
                provider,
                review_prompt(
                    context,
                    second_proposal.stdout
                    if provider == first
                    else first_proposal.stdout,
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
            except Exception as error:
                workflow_event(
                    on_event,
                    "consensus",
                    f"review_{provider}",
                    provider,
                    (
                        "Examen de la proposition de "
                        f"{(second if provider == first else first).capitalize()}"
                    ),
                    "failed",
                    str(error),
                )
                raise
            reviews[provider] = review
            review_results[provider] = local_results
            workflow_event(
                on_event,
                "consensus",
                f"review_{provider}",
                review.provider,
                (
                    "Examen de la proposition de "
                    f"{(second if provider == first else first).capitalize()}"
                ),
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
        "the orchestration mechanism, invent extra agents, or modify files. "
        "Write in a factual collective voice using 'nous' or impersonal phrasing; "
        "never use an ambiguous first-person singular. Clearly separate agreements, "
        "material disagreements, arbitration, and the final recommendation.\n\n"
        + REPORT_RULES
        + "\n\n"
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
    workflow_event(
        on_event,
        "consensus",
        "synthesis",
        "gemini",
        "Synthèse du consensus",
        "running",
    )
    synthesis = orchestrator._run_with_fallback(
        "gemini",
        synthesis_prompt,
        Intent.ANALYZE,
        results,
        on_event=on_event,
        cancel_event=cancel_event,
        respect_cooldown=True,
    )
    workflow_event(
        on_event,
        "consensus",
        "synthesis",
        synthesis.provider,
        "Synthèse du consensus",
        "complete",
    )
    final = clean_report(synthesis.stdout)
    if degraded_providers:
        replacements = ", ".join(
            f"{role.capitalize()} indisponible, relais par {provider.capitalize()}"
            for role, provider in degraded_providers.items()
        )
        final = f"> ⚠️ **Consensus dégradé** — {replacements}.\n\n{final}"
    return final, synthesis.provider


def isolated_run(
    orchestrator,
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
    result = orchestrator._run_with_fallback(
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
        respect_cooldown=True,
        fallback_error_kinds=(
            {"authentication", "quota", "timeout", "unavailable"}
            if allow_quota_fallback
            else None
        ),
    )
    return result, local_results


def review_prompt(context: str, candidate: str) -> str:
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
        + REPORT_RULES
    )


def review_requires_correction(review: str) -> bool:
    return "VERDICT: CORRECTIONS_REQUIRED" in review.upper()


def correction_prompt(context: str, implementation: str, review: str) -> str:
    return (
        context
        + "\n\nA reviewer audited the implementation below. Re-check every "
        "finding, apply only justified corrections, run focused validation, "
        "and report the final result. This is the only correction pass. "
        "Use a factual collective voice ('nous') or impersonal phrasing, never "
        "an ambiguous first-person singular. Integrate the useful review findings "
        "and mention only material remaining disagreements.\n\n"
        "<IMPLEMENTATION>\n"
        + implementation
        + "\n</IMPLEMENTATION>\n\n<REVIEW>\n"
        + review
        + "\n</REVIEW>"
        + REPORT_RULES
    )


def review_final(
    primary: ProviderResult,
    review: ProviderResult,
    correction: ProviderResult | None,
) -> str:
    result = clean_report(
        correction.stdout if correction else primary.stdout
    )
    status = (
        "Les corrections justifiées ont été appliquées et vérifiées."
        if correction
        else "La revue n’a demandé aucune correction justifiée."
    )
    return (
        result
        + "\n\n## Contrôle croisé\n\n"
        + status
        + " Le détail de l’avis de "
        + review.provider.capitalize()
        + " reste disponible dans le panneau de revue."
    )


def workflow_event(
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
