"""La table de routes est la source unique du dispatch et des rôles."""

import re

import pytest

from joe.auth import ROLES, required_role
from joe.routes import ROUTES, resolve
from joe.web import Handler


def test_every_declared_route_has_an_explicit_role_and_handler():
    """Aucune route ne doit hériter d'un défaut permissif implicite."""
    for route in ROUTES:
        assert route.method in {"GET", "POST", "PATCH", "DELETE"}, route.template
        assert route.role in ROLES or route.role == "", route.template
        assert hasattr(Handler, route.handler), (
            f"{route.method} {route.template} pointe vers un handler absent : "
            f"{route.handler}"
        )
        if route.escalated_role:
            assert route.escalated_role in ROLES, route.template
        if route.query_role:
            assert len(route.query_role) == 3, route.template
            assert route.query_role[2] in ROLES, route.template


def test_only_diagnostic_and_pairing_are_public():
    """Toute autre route publique serait une régression d'autorisation."""
    public = {(route.method, route.template) for route in ROUTES if not route.role}
    assert public == {("GET", "/api/status"), ("POST", "/api/pair")}


def test_no_two_routes_can_serve_the_same_path():
    """Les motifs sont exclusifs : l'ordre de déclaration ne porte aucun sens."""
    for method in {route.method for route in ROUTES}:
        declared = [route for route in ROUTES if route.method == method]
        for route in declared:
            # Un chemin concret construit depuis le gabarit ne doit être
            # accepté que par ce gabarit.
            concrete = re.sub(r"\{(\w+)\}", "sample", route.template)
            matching = [
                other for other in declared if other.pattern.match(concrete)
            ]
            assert matching == [route], (
                f"{method} {concrete} est accepté par "
                f"{[item.template for item in matching]}"
            )


def test_creating_a_global_skill_is_not_easier_than_a_project_skill():
    """Un skill global est visible par tous les projets : même exigence."""
    project = required_role("POST", "/api/projects/abc/skills/create")
    assert required_role("POST", "/api/skills/global/create") == project
    assert required_role("POST", "/api/skills/global/import") == "maintainer"


def test_an_undeclared_sub_resource_is_refused_not_diverted():
    """Avant la table, un préfixe fourre-tout détournait ces chemins."""
    assert resolve("GET", "/api/projects/abc/members") is None
    assert resolve("GET", "/api/conversations/abc/messages") is None
    assert resolve("DELETE", "/api/files/abc/extra") is None
    # Et une route inconnue échoue fermée côté rôle.
    assert required_role("GET", "/api/projects/abc/members") == "maintainer"


@pytest.mark.parametrize(
    ("method", "path", "template"),
    [
        ("GET", "/api/projects/abc", "/api/projects/{project_id}"),
        ("GET", "/api/projects/abc/skills", "/api/projects/{project_id}/skills"),
        ("GET", "/api/tasks/xyz/diff", "/api/tasks/{task_id}/diff"),
        ("POST", "/api/runs/xyz/cancel", "/api/runs/{run_id}/cancel"),
        ("POST", "/api/runs/xyz/reject", "/api/runs/{run_id}/reject"),
        ("POST", "/api/projects/abc/trash", "/api/projects/{project_id}/trash"),
        ("POST", "/api/projects/abc/restore", "/api/projects/{project_id}/restore"),
        ("DELETE", "/api/projects/abc", "/api/projects/{project_id}"),
    ],
)
def test_parameterised_paths_resolve_to_their_own_route(method, path, template):
    match = resolve(method, path)
    assert match is not None
    assert match[0].template == template
    assert list(match[1].values()) == [path.split("/")[3]]


def test_forcing_a_quota_refresh_is_an_elevation_carried_by_the_query():
    route = resolve("GET", "/api/usage")[0]
    assert route.role == "viewer"
    assert route.query_role == ("force", "1", "maintainer")


def test_autonomous_manual_handoff_route_is_declared():
    route, values = resolve("POST", "/api/autonomous/c1/handoff")
    assert route.handler == "_post_autonomous_handoff"
    assert values == {"campaign_id": "c1"}
