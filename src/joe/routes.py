"""Single source of truth for HTTP routes and the role each one requires.

Le dispatch et la matrice d'autorisation lisaient auparavant deux jeux de
motifs de chemin indépendants, qui ont dérivé : créer un skill *global* — la
portée la plus large — n'exigeait qu'`operator`, par simple omission du défaut
permissif. Ici, le rôle est une colonne de la table de routes : une route sans
rôle explicite n'existe pas.

Les motifs sont ancrés et leurs segments dynamiques excluent `/`, donc
`/api/projects/{id}` et `/api/projects/{id}/skills` sont mutuellement exclusifs
par construction. L'ordre de déclaration ne porte plus aucun sens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Route:
    method: str
    template: str
    handler: str
    role: str
    """Rôle minimal exigé. Vide = route publique (diagnostic, appairage)."""
    query_role: tuple[str, str, str] | None = None
    """(paramètre, valeur, rôle) : élévation portée par la requête elle-même."""
    escalated_role: str = ""
    """Rôle exigé quand le corps dépasse ce que le rôle de base autorise."""
    pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        expression = re.sub(
            r"\{(\w+)\}",
            lambda match: f"(?P<{match.group(1)}>[^/]+)",
            self.template,
        )
        object.__setattr__(self, "pattern", re.compile(f"^{expression}$"))


def _route(
    method: str,
    template: str,
    handler: str,
    role: str,
    *,
    query_role: tuple[str, str, str] | None = None,
    escalated_role: str = "",
) -> Route:
    return Route(
        method,
        template,
        handler,
        role,
        query_role=query_role,
        escalated_role=escalated_role,
    )


ROUTES: tuple[Route, ...] = (
    # --- Lecture ---------------------------------------------------------
    _route("GET", "/api/status", "_get_status", ""),
    _route("GET", "/api/capabilities", "_get_capabilities", "viewer"),
    _route("GET", "/api/doctor", "_get_doctor", "viewer"),
    # Choisir les CLI que Joe a le droit de lancer engage la machine : c'est
    # une décision d'administration, pas de lecture.
    _route("PATCH", "/api/providers", "_patch_providers", "maintainer"),
    _route("GET", "/api/files", "_get_files", "viewer"),
    _route("GET", "/api/files/{item_id}/download", "_get_file_download", "viewer"),
    # Forcer l'actualisation lance un sous-processus fournisseur : l'élévation
    # est portée par la requête, pas par une exception dans le handler.
    _route(
        "GET",
        "/api/usage",
        "_get_usage",
        "viewer",
        query_role=("force", "1", "maintainer"),
    ),
    _route("GET", "/api/conversations", "_get_conversations", "viewer"),
    _route("GET", "/api/conversations/{conversation_id}", "_get_conversation", "viewer"),
    _route("GET", "/api/search", "_get_search", "viewer"),
    _route("GET", "/api/analytics", "_get_analytics", "viewer"),
    _route("GET", "/api/preferences", "_get_preferences", "viewer"),
    _route("GET", "/api/runs/active", "_get_active_runs", "viewer"),
    _route("GET", "/api/tasks", "_get_tasks", "viewer"),
    _route("GET", "/api/tasks/{task_id}/diff", "_get_task_diff", "viewer"),
    _route("GET", "/api/automations", "_get_automations", "viewer"),
    _route("GET", "/api/autonomous", "_get_autonomous", "viewer"),
    _route("GET", "/api/approvals", "_get_approvals", "viewer"),
    # Lire les projets reste accessible : Joe Web en a besoin pour se rendre.
    _route("GET", "/api/projects", "_get_projects", "viewer"),
    _route("GET", "/api/project-trash", "_get_project_trash", "viewer"),
    _route("GET", "/api/projects/{project_id}", "_get_project", "viewer"),
    _route("GET", "/api/projects/{project_id}/skills", "_get_project_skills", "viewer"),
    _route("GET", "/api/skills/global", "_get_global_skills", "viewer"),
    # Lire le contenu, pas seulement le nom et la taille : tout skill listé
    # entre dans le contexte partagé des fournisseurs.
    _route(
        "GET",
        "/api/projects/{project_id}/skills/{name}",
        "_get_project_skill",
        "viewer",
    ),
    _route("GET", "/api/skills/global/{name}", "_get_global_skill", "viewer"),
    _route("GET", "/api/history", "_get_history", "viewer"),
    _route("GET", "/api/history/{run_id}", "_get_history_item", "viewer"),
    _route("GET", "/api/events/{run_id}", "_get_events", "viewer"),
    # --- Écriture --------------------------------------------------------
    _route("POST", "/api/pair", "_post_pair", ""),
    _route("POST", "/api/auth/rotate", "_post_auth_rotate", "maintainer"),
    _route("POST", "/api/runs", "_post_runs", "operator"),
    _route("POST", "/api/runs/{run_id}/cancel", "_post_run_cancel", "operator"),
    _route("POST", "/api/runs/{run_id}/reject", "_post_run_reject", "maintainer"),
    _route("POST", "/api/tasks/{task_id}/integrate", "_post_task_integrate", "maintainer"),
    _route("POST", "/api/automations", "_post_automations", "maintainer"),
    _route("POST", "/api/autonomous", "_post_autonomous", "maintainer"),
    _route(
        "POST", "/api/autonomous/{campaign_id}/cancel",
        "_post_autonomous_cancel", "maintainer",
    ),
    _route(
        "POST", "/api/autonomous/{campaign_id}/resume",
        "_post_autonomous_resume", "maintainer",
    ),
    _route(
        "POST", "/api/autonomous/{campaign_id}/handoff",
        "_post_autonomous_handoff", "maintainer",
    ),
    _route(
        "POST",
        "/api/automations/{plan_id}/cancel",
        "_post_automation_cancel",
        "maintainer",
    ),
    _route("POST", "/api/conversations", "_post_conversations", "operator"),
    _route("POST", "/api/files", "_post_files", "operator"),
    _route("POST", "/api/projects", "_post_projects", "maintainer"),
    _route("POST", "/api/projects/{project_id}/trash", "_post_project_trash", "maintainer"),
    _route("POST", "/api/projects/{project_id}/restore", "_post_project_restore", "maintainer"),
    # Un skill global est visible par tous les projets : sa création exige au
    # moins autant qu'un skill de projet.
    _route("POST", "/api/skills/global/create", "_post_global_skill_create", "maintainer"),
    _route("POST", "/api/skills/global/import", "_post_global_skill_import", "maintainer"),
    _route(
        "POST",
        "/api/projects/{project_id}/skills/create",
        "_post_project_skill_create",
        "maintainer",
    ),
    _route(
        "POST",
        "/api/projects/{project_id}/skills/import",
        "_post_project_skill_import",
        "maintainer",
    ),
    _route(
        "POST",
        "/api/projects/{project_id}/skills/promote",
        "_post_project_skill_promote",
        "maintainer",
    ),
    # --- Mise à jour -----------------------------------------------------
    _route("PATCH", "/api/preferences", "_patch_preferences", "operator"),
    _route("PATCH", "/api/approvals/{approval_id}", "_patch_approval", "maintainer"),
    # Acquitter un état lu n'est pas une mutation de contenu : viewer suffit
    # pour les champs d'acquittement, operator est exigé au-delà.
    _route(
        "PATCH",
        "/api/conversations/{conversation_id}",
        "_patch_conversation",
        "viewer",
        escalated_role="operator",
    ),
    _route("PATCH", "/api/projects/{project_id}", "_patch_project", "maintainer"),
    # --- Suppression -----------------------------------------------------
    _route("DELETE", "/api/tasks/{task_id}", "_delete_task", "maintainer"),
    _route(
        "DELETE", "/api/autonomous/{campaign_id}",
        "_delete_autonomous", "maintainer",
    ),
    _route("DELETE", "/api/files/{item_id}", "_delete_file", "operator"),
    _route("DELETE", "/api/projects/{project_id}", "_delete_project", "maintainer"),
    # Supprimer exige autant que créer : un skill commun sert tous les projets.
    _route(
        "DELETE",
        "/api/projects/{project_id}/skills/{name}",
        "_delete_project_skill",
        "maintainer",
    ),
    _route("DELETE", "/api/skills/global/{name}", "_delete_global_skill", "maintainer"),
    _route(
        "DELETE",
        "/api/conversations/{conversation_id}",
        "_delete_conversation",
        "operator",
    ),
)


def resolve(method: str, path: str) -> tuple[Route, dict[str, str]] | None:
    """Return the route serving this request and its path parameters."""
    for route in ROUTES:
        if route.method != method:
            continue
        match = route.pattern.match(path)
        if match:
            return route, match.groupdict()
    return None


def required_role(method: str, path: str) -> str:
    """Minimal role for this endpoint, read from the route table itself.

    Une route inconnue échoue fermée : mieux vaut refuser une route non
    déclarée que lui accorder le défaut permissif d'autrefois.
    """
    match = resolve(method, path)
    return match[0].role if match else "maintainer"
