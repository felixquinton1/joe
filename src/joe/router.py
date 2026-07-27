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
REVIEW_WORDS = {
    "avis", "autre", "critique", "review", "relis", "relecture", "vérifie",
    "vérifier", "double-check",
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


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\wÀ-ÿ-]+", text.lower()))


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
        intent = Intent.MODIFY if words & MODIFY_WORDS else Intent.ANALYZE
        if request.rstrip().endswith("?") and intent is not Intent.MODIFY:
            intent = Intent.ANSWER

        follow_up_review = bool(words & REVIEW_WORDS) and previous_provider
        important = any(phrase in lower for phrase in CONSENSUS_PHRASES)
        ambiguous_architecture = bool(words & ARCHITECTURE_WORDS) and any(
            marker in lower for marker in ("choisir", "quelle approche", "propose")
        )

        if forced_mode:
            mode = forced_mode
        elif follow_up_review:
            mode = Mode.FAST
        elif important or ambiguous_architecture:
            mode = Mode.CONSENSUS
        elif words & REVIEW_WORDS:
            mode = Mode.REVIEW
        else:
            mode = Mode.FAST

        if forced_agent:
            primary = forced_agent
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
        reason = f"{intent.value}; {mode.value}; preferred={primary}"
        return Route(intent, mode, primary, reviewer, reason)
