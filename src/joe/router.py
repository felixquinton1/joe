from __future__ import annotations

import re

from .models import Intent, Mode, Route
from .provider_registry import counterpart, get_provider_names

MODIFY_WORDS = {
    "ajoute", "ajouter", "change", "changer", "corrige", "corriger", "crée",
    "créer", "implémente", "implémenter", "modifie", "modifier", "refactor",
    "implémentation", "implementation", "merge", "merger", "fusionne",
    "fusionner", "pull", "rebase", "commit", "push", "cherry-pick",
    "remove", "fix", "implement", "update", "write", "améliore", "améliorer",
}
MODIFY_PHRASES = {
    "vas-y",
    "vas y",
    "fais le nécessaire",
    "fais le necessaire",
    "fais cela",
    "fais ça",
    "mets en œuvre",
    "mets en oeuvre",
    "applique ces changements",
    "passe à la suite",
    "passe a la suite",
    "poursuis l'implémentation",
    "poursuis l’implémentation",
    "continue l'implémentation",
    "continue l’implémentation",
    "reprends l'implémentation",
    "reprends l’implémentation",
}
FEATURE_REQUEST_PATTERNS = (
    re.compile(
        r"\b(?:c['’]est|est[- ]ce|est il)\s+possible\s+de\s+"
        r"(?:ajouter|afficher|avoir|faire|préciser|integrer|intégrer|"
        r"permettre|corriger)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:j['’]aimerais?|je\s+voudrais)\s+(?:seulement\s+)?"
        r"(?:avoir|afficher|ajouter|mettre|retirer|enlever)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:peux[- ]tu|pourrais[- ]tu|tu\s+peux)\s+faire\s+en\s+sorte\s+"
        r"(?:de|qu['’]on\s+puisse)\s+"
        r"(?:ajouter|améliorer|changer|choisir|corriger|créer|implémenter|"
        r"modifier|refaire|rendre|traduire)\b",
        re.IGNORECASE,
    ),
)
DEFECT_REPORT_PATTERNS = (
    re.compile(
        r"\b(?:est|sont|reste|restent|semble|semblent)\s+"
        r"(?:bugu[ée]s?|cass[ée]s?|bloqu[ée]s?|illisible|inutilisable)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ça|cela|ceci|il|elle)\s+ne\s+"
        r"(?:fonctionne|marche|s['’]affiche)\s+pas\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:ça|cela|le panneau|la fenêtre)\s+d[ée]borde\b", re.IGNORECASE),
)
ANALYSIS_QUESTION_PREFIXES = (
    "analyse", "diagnostique", "explique", "pourquoi", "comment",
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
STRATEGIC_CHOICE_WORDS = {
    "commercial", "distribution", "github", "license", "licence",
    "marketplace", "monétisation", "portfolio", "public", "pypi",
    "stratégie", "vitrine",
}
EXPLICIT_REVIEW_PHRASES = {
    "auditer par", "faire auditer", "fais-le auditer", "fais le auditer",
    "fait le auditer", "puis audite", "puis fais auditer",
}
SUBSTANTIAL_REVIEW_PHRASES = {
    "audit global",
    "audit complet",
    "revue indépendante",
    "relecture indépendante",
    "contrôle croisé",
    "verification croisée",
    "vérification croisée",
}
ARCHITECTURE_WORDS = {"architecture", "migration", "protocole", "roadmap"}
DOC_WORDS = {"documentation", "readme", "rédige", "rédiger", "critique"}
LARGE_CONTEXT_WORDS = {
    "gros dépôt", "grand dépôt", "gros contexte", "long contexte",
    "explore tout", "synthétise le dépôt", "synthèse du dépôt",
}
LARGE_IMPLEMENTATION_PHRASES = {
    "gros travail", "implémentation complète", "implementation complete",
    "de bout en bout", "plusieurs fichiers", "refonte", "refactor complet",
}
# « go » ne peut pas être cherché en sous-chaîne : il apparaît dans
# « algorithme », « catégorie », « ergonomie », « négociation », « Django »…
# On ne le reconnaît que comme demande entière, ponctuation et politesses
# usuelles admises.
GO_CONTINUATION_FILLERS = {
    "ok", "okay", "oui", "allez", "allez-y", "stp", "svp", "please",
    "merci", "go",
}
EXTERNAL_RESEARCH_PHRASES = {
    "compare", "comparaison", "comparer", "outils similaires", "produits similaires",
    "sites existants", "solutions existantes", "benchmark", "état des sources",
}
CAPABILITY_QUESTION_PHRASES = {
    "est-ce que tu peux", "est ce que tu peux", "est-il possible",
    "est il possible", "puis-je", "puis je", "dois-je", "dois je",
    "est-ce que ça", "est ce que ça", "que se passe-t-il",
}
# Sonde de disponibilité. Le health-check REMPLACE la demande de l'utilisateur
# par « calcule 17 × 23 » : le déclencher à tort détruit la demande. Deux
# garde-fous, parce que la recherche en sous-chaîne a déjà transformé une
# spécification de 700 caractères contenant « il fonctionne par fenêtre de 5h »
# en test de disponibilité.
HEALTH_CHECK_MAX_CHARS = 120
HEALTH_CHECK_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bpetit\s+test\b",
        r"\btest(?:e|es)?\s+(?:de|du|d['’])\b",
        r"\bfonctionnes?\b",
        r"\best\s+disponible\b",
        r"\b(?:ça|ca|il|elle)\s+marche\b",
    )
)

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


def _routing_text(text: str) -> str:
    """Keep user prose for routing while ignoring pasted diagnostics."""
    kept: list[str] = []
    in_fence = False
    diagnostic_prefixes = (
        "npm error", "npm warn", "traceback", "warning:", "error:",
        "fatal:", "caused by:", "at ", "file ",
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if stripped.lower().startswith(diagnostic_prefixes):
            continue
        kept.append(stripped)
    return " ".join(kept) or text


def _is_go_continuation(text: str) -> bool:
    """Recognize a bare « go » request, punctuation and fillers allowed."""
    words = re.findall(r"[\w'’-]+", text.casefold())
    return bool(words) and "go" in words and set(words) <= GO_CONTINUATION_FILLERS


def _is_health_check(text: str, explicit_provider: bool) -> bool:
    """An availability probe is short, names one provider, and asks nothing else."""
    if not explicit_provider:
        return False
    if len(text.strip()) > HEALTH_CHECK_MAX_CHARS:
        return False
    return any(pattern.search(text) for pattern in HEALTH_CHECK_PATTERNS)


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
        routing_text = _routing_text(request)
        lower = routing_text.lower()
        words = _tokens(routing_text)
        intent_text, read_only_directive = _intent_text(routing_text)
        intent_words = _tokens(intent_text)
        if read_only_directive:
            intent_words -= READ_ONLY_AMBIGUOUS_MODIFY_WORDS
        named_providers = words & set(get_provider_names())
        explicit_provider = (
            next(iter(named_providers)) if len(named_providers) == 1 else None
        )
        health_check = _is_health_check(routing_text, bool(explicit_provider))
        capability_question = any(
            phrase in lower for phrase in CAPABILITY_QUESTION_PHRASES
        )
        feature_request = any(
            pattern.search(intent_text) for pattern in FEATURE_REQUEST_PATTERNS
        )
        defect_report = (
            any(pattern.search(intent_text) for pattern in DEFECT_REPORT_PATTERNS)
            and not intent_text.strip().lower().startswith(ANALYSIS_QUESTION_PREFIXES)
        )
        intent = (
            Intent.MODIFY
            if (
                intent_words & MODIFY_WORDS
                or any(phrase in intent_text.lower() for phrase in MODIFY_PHRASES)
                or _is_go_continuation(intent_text)
                or feature_request
                or defect_report
            )
            and (not capability_question or feature_request)
            else Intent.ANALYZE
        )
        if (capability_question and not feature_request) or (
            routing_text.rstrip().endswith("?") and intent is not Intent.MODIFY
        ):
            intent = Intent.ANSWER

        explicit_workflow_review = any(
            phrase in lower for phrase in EXPLICIT_REVIEW_PHRASES
        )
        substantial_review = any(
            phrase in lower for phrase in SUBSTANTIAL_REVIEW_PHRASES
        )
        follow_up_review = (
            bool(words & REVIEW_WORDS)
            and previous_provider
            and not explicit_workflow_review
        )
        strategic_choice = (
            len(routing_text) >= 140
            and " ou " in lower
            and len(words & STRATEGIC_CHOICE_WORDS) >= 2
        )
        external_research = (
            any(phrase in lower for phrase in EXTERNAL_RESEARCH_PHRASES)
            or "http://" in lower
            or "https://" in lower
        )
        important = (
            any(phrase in lower for phrase in CONSENSUS_PHRASES)
            or strategic_choice
            or external_research
        )
        ambiguous_architecture = bool(words & ARCHITECTURE_WORDS) and any(
            marker in lower for marker in ("choisir", "quelle approche", "propose")
        )
        large_implementation = intent is Intent.MODIFY and (
            any(phrase in lower for phrase in LARGE_IMPLEMENTATION_PHRASES)
            or (
                len(routing_text) >= 240
                and len(words & MODIFY_WORDS) >= 2
                and not routing_text.rstrip().endswith("?")
            )
        )

        if forced_mode:
            mode = forced_mode
        elif explicit_workflow_review:
            mode = Mode.REVIEW
        elif follow_up_review:
            mode = Mode.FAST
        elif important or ambiguous_architecture:
            mode = Mode.CONSENSUS
        elif large_implementation or substantial_review:
            mode = Mode.REVIEW
        else:
            mode = Mode.FAST

        if forced_agent:
            primary = forced_agent
        elif explicit_provider and not explicit_workflow_review:
            primary = explicit_provider
        elif follow_up_review:
            primary = counterpart(previous_provider or "")
        elif any(phrase in lower for phrase in LARGE_CONTEXT_WORDS):
            primary = "gemini"
        elif words & DOC_WORDS or words & ARCHITECTURE_WORDS:
            primary = "claude"
        else:
            primary = "codex"

        reviewer = None
        if mode is Mode.REVIEW:
            reviewer = (
                explicit_provider
                if explicit_workflow_review and explicit_provider
                else counterpart(primary)
            )
        reason = (
            f"{intent.value}; {mode.value}; preferred={primary}"
            + ("; explicit-provider" if explicit_provider and not forced_agent else "")
            + ("; explicit-review-workflow" if explicit_workflow_review else "")
            + ("; health-check" if health_check else "")
            + ("; external-research" if external_research else "")
            + (
                "; explicit-read-only"
                if read_only_directive and intent is not Intent.MODIFY
                else ""
            )
        )
        return Route(intent, mode, primary, reviewer, reason)
