from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .models import Intent, ProviderResult, Route
from .prompt_language import (
    DEFAULT_LANGUAGE,
    collective_voice,
    normalize as normalize_language,
    question_rules,
    response_language,
)
from .git_review import changed_paths, snapshot
from .provider_registry import arbitration_order, counterpart


REPORT_RULES = (
    "\n\nReporting rules: use factual collective or impersonal phrasing. "
    "Do not add a section called Refusé/Refused for an internal inability to "
    "run tests, npm, git, network, or sandboxed commands. Omit that detail from "
    "the user report; it belongs to the technical run log. Mention a validation "
    "limitation only when it materially changes the result, in one short sentence "
    "under Validation. Never claim a check passed unless it actually ran. "
    "Never name a provider, reviewer, or stage as having run unless it appears "
    "in the execution records supplied to you."
)


# Ce que chaque etape merite, et non ce que la demande globale merite. Une
# revue croisee lit une proposition et la critique : c'est plus leger que de
# la produire. Jusqu'ici une seule etape recevait un modele — la proposition
# du fournisseur choisi — et toutes les autres partaient sur le defaut de leur
# CLI, que Joe ne controle pas et qui est souvent le modele phare.
STAGE_TIERS = {
    "proposal": "strong",
    "cross_review": "standard",
    "synthesis": "strong",
    "implementation": None,   # suit le niveau de la demande
    # L'examen ne critique plus un texte, il rouvre le code et le corrige.
    # Le laisser a « standard » revenait a confier la seconde passe a un modele
    # plus leger que celui qui a ecrit la premiere. Le niveau se resout dans le
    # catalogue de l'examinateur, qui n'est pas celui de l'auteur : lui passer
    # le modele de l'auteur nommerait un identifiant que sa CLI ne connait pas.
    "review": "strong",
}


def stage_model(orchestrator, provider: str, stage: str, fallback: str | None) -> str | None:
    """Le modele d'une etape interne, ou `fallback` si l'etape suit la demande."""
    tier = STAGE_TIERS.get(stage)
    if not tier:
        return fallback
    from .capabilities import select_model_tier

    return select_model_tier(provider, tier) or fallback


def clean_report(text: str) -> str:
    """Remove only generic internal-validation refusal sections from agent prose."""
    if not text:
        return text
    had_tool_protocol = bool(
        re.search(r"(?is)<(?:tool_call|tool_response)>.*?</(?:tool_call|tool_response)>", text)
    )
    text = re.sub(
        r"(?is)<(?:tool_call|tool_response)>.*?</(?:tool_call|tool_response)>\s*",
        "",
        text,
    )
    if had_tool_protocol:
        # Provider tool transcripts are not user-facing content. Keep the first
        # actual Markdown section that follows the leaked protocol.
        heading = re.search(r"(?m)^#{1,6}\s+", text)
        if heading:
            text = text[heading.start():]
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


def complete_synthesis(text: str) -> bool:
    """Reject a successful CLI exit that only contains an unfinished preamble."""
    report = clean_report(text)
    if not report:
        return False
    lowered = report.casefold()
    trailing_paragraph = lowered.rsplit("\n\n", 1)[-1].strip()
    unfinished_endings = (
        "je commence",
        "nous commençons",
        "je vais maintenant",
        "nous allons maintenant",
        "let me ",
        "i will now ",
    )
    if any(marker in trailing_paragraph for marker in unfinished_endings):
        return False
    plan_only = lowered.startswith(("ce que je vais faire", "what i will do"))
    completion_markers = (
        "## résultat",
        "# conclusion",
        "## conclusion",
        "recommandation finale",
        "final recommendation",
        "## arbitrage",
    )
    return not (
        plan_only
        and len(report) < 1_000
        and not any(marker in lowered for marker in completion_markers)
    )


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
    language: str = DEFAULT_LANGUAGE,
) -> tuple[str, str]:
    language = normalize_language(language)
    # Pris avant l'implementation : c'est la comparaison avec l'etat d'apres
    # qui dira a l'examinateur ou regarder, sans confondre le travail du run
    # avec ce que le depot portait deja.
    before = snapshot(orchestrator.project)
    implementation_request = (
        context
        + "\n\nPresent the implementation report in a factual, collective voice. "
        + collective_voice(language)
        + "Focus on the completed result and validation. Do not invoke "
        "another AI provider or perform an independent review yourself: Joe "
        "owns the review stage after this implementation finishes. Do not "
        "mention a future, pending, or not-yet-run review in this report; Joe "
        "will add the actual review outcome after that stage completes."
        + REPORT_RULES
        + question_rules(language)
        + response_language(language)
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
        counterpart(primary.provider)
    )
    # Une demande de lecture seule n'a rien modifie : il n'y a pas de seconde
    # passe a faire, seulement un avis a rendre.
    examines = route.intent is Intent.MODIFY
    review_request = (
        examination_prompt(
            context,
            primary.stdout,
            changed_paths(orchestrator.project, before),
            language,
        )
        if examines
        else review_prompt(context, primary.stdout, language)
    )
    workflow_event(
        on_event,
        "review",
        "review",
        reviewer,
        "Revue indépendante",
        "running",
    )
    before_examination = snapshot(orchestrator.project)
    review = orchestrator._run_with_fallback(
        reviewer,
        review_request,
        Intent.MODIFY if examines else Intent.ANALYZE,
        results,
        exclude={primary.provider},
        model=stage_model(orchestrator, reviewer, "review", None),
        effort=effort if examines else None,
        # Les memes droits que l'auteur, jamais plus : si la demande etait en
        # lecture seule, l'examen l'est aussi.
        execution_mode=execution_mode if examines else None,
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
    # Le texte du fournisseur ne suffit pas pour affirmer qu'il a corrigé le
    # dépôt. Dans un dépôt Git, l'état matériel fait foi ; hors Git, le verdict
    # reste le seul signal disponible et préserve le fonctionnement historique.
    corrected = (
        bool(changed_paths(orchestrator.project, before_examination))
        if before_examination.available
        else corrections_were_applied(review.stdout)
    )
    return review_final(
        primary,
        review,
        corrected=examines and corrected,
        language=language,
    ), review.provider if examines and corrected else primary.provider


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
    language: str = DEFAULT_LANGUAGE,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str]:
    language = normalize_language(language)
    proposal_prompt = (
        context
        + "\n\nForm an independent proposal for the requested consensus. "
        "Use your normal tools and investigation process within the enforced "
        "read-only permissions. State assumptions, trade-offs, and validation."
        + REPORT_RULES
        + response_language(language)
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
                stage_model(
                    orchestrator,
                    provider,
                    "proposal",
                    model if selected_provider == provider else None,
                ),
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
                    language,
                ),
                Intent.ANALYZE,
                {other},
                stage_model(orchestrator, provider, "cross_review", None),
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
    # L'arbitre se déduit de ce qui tourne réellement ici : un tiers de
    # préférence, l'un des deux proposants s'il ne reste qu'eux.
    arbiter = arbitration_order(
        proposers=participants,
        eligible=tuple(orchestrator.providers),
    )[0]
    synthesis_prompt = (
        context
        + "\n\nSynthesize the following independent proposals and cross-reviews. "
        "Resolve disagreements explicitly and produce one actionable recommendation. "
        "Return clean Markdown with complete lines and valid tables. Do not describe "
        "the orchestration mechanism, invent extra agents, or modify files. "
        + collective_voice(language)
        + "Clearly separate agreements, "
        "material disagreements, arbitration, and the final recommendation. The "
        "proposals and reviews below may be written in another language: they "
        "are material to synthesize, not a model for the language of your "
        "answer.\n\n"
        + REPORT_RULES
        + question_rules(language)
        + "\n\nActual execution ledger (authoritative; do not invent providers): "
        + json.dumps(
            {
                "proposal_roles": {role: proposal.provider for role, proposal in proposals.items()},
                "review_roles": {role: review.provider for role, review in reviews.items()},
                "synthesis_requested": arbiter,
            },
            ensure_ascii=False,
        )
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
        # Le matériau transmis peut peser plusieurs milliers de mots dans une
        # autre langue : la consigne se relit après lui, jamais avant.
        + response_language(language)
    )
    workflow_event(
        on_event,
        "consensus",
        "synthesis",
        arbiter,
        "Synthèse du consensus",
        "running",
    )
    synthesis = orchestrator._run_with_fallback(
        arbiter,
        synthesis_prompt,
        Intent.ANALYZE,
        results,
        model=stage_model(orchestrator, arbiter, "synthesis", None),
        on_event=on_event,
        cancel_event=cancel_event,
        respect_cooldown=True,
        output_validator=complete_synthesis,
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
    if synthesis.provider != arbiter:
        degraded_providers[f"synthèse {arbiter.capitalize()}"] = synthesis.provider
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


def review_prompt(
    context: str,
    candidate: str,
    language: str = DEFAULT_LANGUAGE,
) -> str:
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
        + response_language(language)
    )


def corrections_were_applied(examination: str) -> bool:
    return "VERDICT: CORRECTIONS_APPLIED" in examination.upper()


def examination_prompt(
    context: str,
    implementation: str,
    changed: list[str],
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """La seconde passe : un examinateur rouvre le code et le corrige.

    Une fonctionnalite qui marche a moitie est le cas courant, et elle ne se
    voit pas dans le compte rendu de celui qui l'a ecrite — il rapporte ce
    qu'il a voulu faire. D'ou deux exigences : juger le code plutot que le
    rapport, et corriger soi-meme plutot que decrire a l'auteur ce qu'il
    devrait refaire.

    Les chemins touches sont donnes parce qu'une CLI agentique sait ouvrir un
    fichier mais pas deviner lesquels ont bouge.
    """
    files = (
        "\n\n<CHANGED_FILES>\n" + "\n".join(changed) + "\n</CHANGED_FILES>"
        if changed
        else "\n\nNo changed file could be listed: locate the work yourself "
        "from the report and the repository."
    )
    return (
        context
        + "\n\nAnother agent has just done the work described below. Take a "
        "second pass over it, directly in the repository.\n"
        "1. Read the code that changed. Judge the code, not the report: the "
        "report says what was intended, the files say what happened, and "
        "where they disagree the files win.\n"
        "2. Check every condition of the original request is actually met, "
        "including those the report does not mention. A feature that works "
        "halfway, or that misses a case, is the usual outcome of a first "
        "pass — that is what this pass exists to catch.\n"
        "3. Fix what you find: bugs, omissions, conditions left unmet. Edit "
        "the files yourself rather than describing what should be changed. "
        "Leave alone what already works, and do not widen the scope beyond "
        "the original request.\n"
        "4. Start your answer with exactly `VERDICT: APPROVED` when you "
        "changed nothing, or `VERDICT: CORRECTIONS_APPLIED` when you did, "
        "then report the final state of the work.\n"
        + collective_voice(language)
        + files
        + "\n\n<IMPLEMENTATION_REPORT>\n"
        + implementation
        + "\n</IMPLEMENTATION_REPORT>"
        + REPORT_RULES
        + question_rules(language)
        + response_language(language)
    )


def review_final(
    primary: ProviderResult,
    review: ProviderResult,
    *,
    corrected: bool,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """Le rapport qui fait foi est celui du dernier a avoir touche au code.

    Quand l'examinateur a corrige, c'est son compte rendu qui decrit l'etat
    final ; celui de l'auteur decrit un etat qui n'existe plus.
    """
    result = clean_report(review.stdout if corrected else primary.stdout)
    if corrected:
        result = _completed_review_report(result)
    else:
        result = _remove_stale_review_forecast(result)
    if normalize_language(language) == "en":
        status = (
            f"{review.provider.capitalize()} took a second pass and corrected "
            "the work."
            if corrected
            else f"{review.provider.capitalize()} examined the work and found "
            "nothing to correct."
        )
        footer = (
            "## Cross-check\n\n"
            f"{status} The detail remains available in the review panel."
        )
    else:
        status = (
            f"{review.provider.capitalize()} est repassé sur le travail et l’a "
            "corrigé."
            if corrected
            else f"{review.provider.capitalize()} a examiné le travail sans "
            "relever de correction à apporter."
        )
        footer = (
            "## Contrôle croisé\n\n"
            f"{status} Le détail reste disponible dans le panneau de revue."
        )
    return result + "\n\n" + footer


def _completed_review_report(report: str) -> str:
    """Keep the completed finding, not the examiner's live progress preamble."""
    verdict = re.search(
        r"(?im)^VERDICT:\s*(?:APPROVED|CORRECTIONS_APPLIED|CORRECTIONS_REQUIRED)\s*$",
        report,
    )
    if not verdict:
        return report.strip()
    completed = report[verdict.end():].strip()
    return completed or report[:verdict.start()].strip()


def _remove_stale_review_forecast(report: str) -> str:
    """Remove implementation-time claims invalidated by a completed review.

    The author's report is still the richest description when the examiner
    changes nothing, but it was written before that examination. Providers
    sometimes append a sentence saying the review has not run yet; retaining
    it beside the completed cross-check makes the durable answer contradict
    itself.
    """
    patterns = (
        r"\s*The separate review by another provider has not run yet;\s*"
        r"Joe does that step next\.",
        r"\s*The (?:independent )?review has not (?:yet )?run(?: yet)?[.;]?\s*"
        r"(?:Joe (?:will|does) (?:run|do) (?:it|that) next\.)?",
        r"\s*La revue (?:indépendante )?(?:par un autre fournisseur )?"
        r"n['’]a pas encore (?:eu lieu|été exécutée|tourné)[.;]?\s*"
        r"(?:Joe (?:la lancera|s['’]en charge) ensuite\.)?",
    )
    for pattern in patterns:
        report = re.sub(pattern, "", report, flags=re.IGNORECASE)
    return report.strip()


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
