from __future__ import annotations

import re

from .models import Intent, Mode, Route

MODIFY_WORDS = {
    "ajoute", "ajouter", "change", "changer", "corrige", "corriger", "crée",
    "créer", "implémente", "implémenter", "modifie", "modifier", "refactor",
    "implémentation", "implementation", "merge", "merger", "fusionne",
    "fusionner", "pull", "rebase", "commit", "push", "cherry-pick",
    "remove", "fix", "implement", "update", "write",
}
MODIFY_PHRASES = {
    "fais cela",
    "fais ça",
    "mets en œuvre",
    "mets en oeuvre",
    "applique ces changements",
}
FEATURE_REQUEST_PATTERNS = (
    re.compile(
        r"\b(?:c['’]est|est[- ]ce|est il)\s+possible\s+de\s+"
        r"(?:ajouter|afficher|préciser|integrer|intégrer|permettre|corriger)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:j['’]aimerais?|je\s+voudrais)\s+(?:seulement\s+)?"
        r"(?:avoir|afficher|ajouter|mettre|retirer|enlever)\b",
        re.IGNORECASE,
    ),
)
READ_ONLY_AMBIGUOUS_MODIFY_WORDS = {"implementation", "implémentation"}
REVIEW_WORDS = {
    "avis", "autre", "critique", "review", "relis", "relecture", "vérifie",
    "vérifier", "double-check", "audit", "audite", "auditer",
}
CONSENSUS_PHRASES = {
    "architecture structurante", "difficile à inverser", "irréversible",
    "plan expérimental", "plan scientifique", "plusieurs approches",
    "compromis importants", "consensus",
}
ARCHITECTURE_WORDS = {"architecture", "migration", "protocole", "roadmap"}
CODE_WORDS = {
    "bug", "debug", "python", "test", "tests", "loss", "training", "code",
    "exception", "traceback", "implémentation", "implementation",
}
DOC_WORDS = {"documentation", "readme", "rédige", "rédiger", "critique"}
LARGE_CONTEXT_WORDS = {
    "gros dépôt", "grand dépôt", "gros contexte", "long contexte",
    "explore tout", "synthétise le dépôt", "synthèse du dépôt",
}
LARGE_IMPLEMENTATION_PHRASES = {
    "gros travail", "implémentation complète", "implementation complete",
    "de bout en bout", "plusieurs fichiers", "refonte", "refactor complet",
}
CAPABILITY_QUESTION_PHRASES = {
    "est-ce que tu peux", "est ce que tu peux", "est-il possible",
    "est il possible", "puis-je", "puis je", "dois-je", "dois je",
    "est-ce que ça", "est ce que ça", "que se passe-t-il",
}
PROVIDER_NAMES = {"codex", "claude", "gemini", "copilot"}
HEALTH_CHECK_PHRASES = {
    "petit test", "teste ", "test de ", "test du ", "fonctionne",
    "est disponible", "marche",
}
READ_ONLY_DIRECTIVES = (
    r"\bne\s+(?:modifie|change|touche)\s+(?:rien|aucun(?:e)?\s+\w+)",
    r"\bn['’](?:écris|ecris)\s+(?:rien|dans\s+aucun(?:e)?\s+\w+)",
    r"\bsans\s+(?:modifier|changer|toucher|écrire|ecrire)\b",
    r"\b(?:do not|don['’]t)\s+(?:modify|change|edit|write)\b",
    r"\bwithout\s+(?:modifying|changing|editing|writing)\b",
    r"\b(?:no|aucune?s?)\s+(?:file\s+changes?|modifications?|changements?)\b",
)
NEGATED_ACTION_CLAUSES = (
    re.compile(
        r"\b(?:ne\s+|n['’])(?:pas\s+)?"
        r"(?:exécute|execute|lance|fais|fait|effectue)\s+"
        r"(?:pas\s+|jamais\s+)?[^,.;!?]*(?=$|[,.;!?])",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:do not|don['’]t)\s+(?:run|execute|perform)\s+"
        r"[^,.;!?]*(?=$|[,.;!?])",
        re.IGNORECASE,
    ),
)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\wÀ-ÿ-]+", text.lower()))


def _intent_text(text: str) -> tuple[str, bool]:
    actionable = text
    read_only = False
    for pattern in READ_ONLY_DIRECTIVES:
        actionable, count = re.subn(
            pattern,
            " ",
            actionable,
            flags=re.IGNORECASE,
        )
        read_only = read_only or bool(count)
    for pattern in NEGATED_ACTION_CLAUSES:
        actionable = pattern.sub(" ", actionable)
    return actionable, read_only


class Router:
    def route(
        self,
        request: str,
        *,
        forced_agent: str | None = None,
        forced_mode: Mode | None = None,
        previous_provider: str | None = None,
    ) -> Route:
        lower = request.lower()
        words = _tokens(request)
        intent_text, read_only_directive = _intent_text(request)
        intent_words = _tokens(intent_text)
        if read_only_directive:
            intent_words -= READ_ONLY_AMBIGUOUS_MODIFY_WORDS
        named_providers = words & PROVIDER_NAMES
        explicit_provider = (
            next(iter(named_providers)) if len(named_providers) == 1 else None
        )
        health_check = bool(explicit_provider) and any(
            phrase in lower for phrase in HEALTH_CHECK_PHRASES
        )
        capability_question = any(
            phrase in lower for phrase in CAPABILITY_QUESTION_PHRASES
        )
        feature_request = any(
            pattern.search(intent_text) for pattern in FEATURE_REQUEST_PATTERNS
        )
        intent = (
            Intent.MODIFY
            if (
                intent_words & MODIFY_WORDS
                or any(phrase in intent_text.lower() for phrase in MODIFY_PHRASES)
                or feature_request
            )
            and (not capability_question or feature_request)
            else Intent.ANALYZE
        )
        if (capability_question and not feature_request) or (
            request.rstrip().endswith("?") and intent is not Intent.MODIFY
        ):
            intent = Intent.ANSWER

        follow_up_review = bool(words & REVIEW_WORDS) and previous_provider
        important = any(phrase in lower for phrase in CONSENSUS_PHRASES)
        ambiguous_architecture = bool(words & ARCHITECTURE_WORDS) and any(
            marker in lower for marker in ("choisir", "quelle approche", "propose")
        )
        large_implementation = intent is Intent.MODIFY and (
            any(phrase in lower for phrase in LARGE_IMPLEMENTATION_PHRASES)
            or (
                len(request) >= 240
                and len(words & MODIFY_WORDS) >= 2
                and not request.rstrip().endswith("?")
            )
        )

        if forced_mode:
            mode = forced_mode
        elif follow_up_review:
            mode = Mode.FAST
        elif important or ambiguous_architecture:
            mode = Mode.CONSENSUS
        elif large_implementation:
            mode = Mode.REVIEW
        elif words & REVIEW_WORDS:
            mode = Mode.REVIEW
        else:
            mode = Mode.FAST

        if forced_agent:
            primary = forced_agent
        elif explicit_provider:
            primary = explicit_provider
        elif follow_up_review:
            primary = "claude" if previous_provider == "codex" else "codex"
        elif any(phrase in lower for phrase in LARGE_CONTEXT_WORDS):
            primary = "gemini"
        elif words & DOC_WORDS or words & ARCHITECTURE_WORDS:
            primary = "claude"
        elif words & CODE_WORDS or intent is Intent.MODIFY:
            primary = "codex"
        else:
            primary = "codex"

        reviewer = None
        if mode is Mode.REVIEW:
            reviewer = "claude" if primary == "codex" else "codex"
        reason = (
            f"{intent.value}; {mode.value}; preferred={primary}"
            + ("; explicit-provider" if explicit_provider and not forced_agent else "")
            + ("; health-check" if health_check else "")
            + (
                "; explicit-read-only"
                if read_only_directive and intent is not Intent.MODIFY
                else ""
            )
        )
        return Route(intent, mode, primary, reviewer, reason)
