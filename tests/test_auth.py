import http.client
import json
import stat

from conftest import build_test_server

from joe.auth import load_or_create_token, required_role, rotate_token


def start_server(tmp_path, role):
    # Ces tests exercent la porte d'approbation : accès manuel explicite.
    return build_test_server(tmp_path, role=role, ai_access="manual")


def request(server, method, path, payload=None, token=None, headers=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5
    )
    body = None if payload is None else json.dumps(payload)
    headers = dict(headers or {})
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    return response.status, json.loads(raw) if raw else None, response


def test_local_token_is_stable_and_private(tmp_path):
    path = tmp_path / "auth-token"
    first = load_or_create_token(path)
    second = load_or_create_token(path)

    assert first == second
    assert len(first) >= 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    rotated = rotate_token(path)
    assert rotated != first
    assert load_or_create_token(path) == rotated
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_capability_matrix_is_centralized():
    assert required_role("GET", "/api/conversations") == "viewer"
    assert required_role("POST", "/api/runs") == "operator"
    assert required_role("POST", "/api/projects") == "maintainer"
    assert required_role("POST", "/api/runs/id/reject") == "maintainer"
    assert required_role("POST", "/api/auth/rotate") == "maintainer"
    # Lire les projets reste accessible : Joe Web en a besoin pour se rendre.
    assert required_role("GET", "/api/projects") == "viewer"
    assert required_role("PATCH", "/api/approvals/id") == "maintainer"
    assert required_role("GET", "/api/approvals") == "viewer"


def test_status_is_public_but_api_requires_a_token(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        status, payload, _ = request(server, "GET", "/api/status")
        assert status == 200
        assert payload["auth_required"] is True
        # Assez pour que l'interface se rende et signale un serveur périmé,
        # mais rien qui décrive la machine : les chemins absolus portent le
        # nom du compte, et cette route est ouverte à qui atteint le port.
        assert payload["version"]
        assert payload["providers"]
        for secret in ("project", "conversation_store", "conversation_backup",
                       "profile"):
            assert secret not in payload, secret

        # Avec un jeton, la même route décrit l'installation.
        status, payload, _ = request(server, "GET", "/api/status", token="test-token")
        assert status == 200
        assert payload["profile"] == "maintainer"
        assert payload["project"]

        status, payload, _ = request(server, "GET", "/api/conversations")
        assert status == 401
        assert "Authentification" in payload["error"]

        status, _, _ = request(
            server, "GET", "/api/conversations", token="test-token"
        )
        assert status == 200
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_root_never_distributes_the_token(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()

        assert response.getheader("Set-Cookie") is None
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_pairing_requires_possession_and_returns_an_http_only_cookie(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        status, _, response = request(server, "POST", "/api/pair", {})
        assert status == 401
        assert response.getheader("Set-Cookie") is None

        status, payload, response = request(
            server,
            "POST",
            "/api/pair",
            {},
            token="test-token",
        )
        assert status == 200
        assert payload["paired"] is True
        cookie = response.getheader("Set-Cookie")
        assert "joe_token=test-token" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Max-Age=2592000" in cookie
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_cookie_auth_works_even_with_an_empty_bearer_header(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        status, _, _ = request(
            server,
            "GET",
            "/api/conversations",
            headers={
                "Authorization": "Bearer ",
                "Cookie": "joe_token=test-token",
            },
        )
        assert status == 200
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_non_ascii_bearer_is_rejected_without_crashing(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        status, payload, _ = request(
            server,
            "GET",
            "/api/conversations",
            headers={"Authorization": "Bearer é"},
        )
        assert status == 401
        assert "Authentification" in payload["error"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_viewer_cannot_force_a_quota_refresh(tmp_path):
    server, thread = start_server(tmp_path, "viewer")
    try:
        status, payload, _ = request(
            server,
            "GET",
            "/api/usage?force=1",
            token="test-token",
        )
        assert status == 403
        assert "maintainer" in payload["error"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_maintainer_can_rotate_and_revoke_the_previous_token(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        status, payload, _ = request(
            server,
            "POST",
            "/api/auth/rotate",
            {},
            token="test-token",
        )
        assert status == 200
        assert payload["rotated"] is True
        replacement = server.auth_path.read_text().strip()
        assert replacement != "test-token"

        status, _, _ = request(
            server, "GET", "/api/conversations", token="test-token"
        )
        assert status == 401
        status, _, _ = request(
            server, "GET", "/api/conversations", token=replacement
        )
        assert status == 200
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_viewer_cannot_mutate_and_operator_cannot_manage_projects(tmp_path):
    for role, path in (
        ("viewer", "/api/conversations"),
        ("operator", "/api/projects"),
    ):
        server, thread = start_server(tmp_path / role, role)
        try:
            status, _, _ = request(
                server, "POST", path, {}, token="test-token"
            )
            assert status == 403
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_operator_cannot_approve_a_project_default_full_access(tmp_path):
    server, thread = start_server(tmp_path, "operator")
    try:
        project = server.manager.conversations.create_project("Privé")
        server.manager.conversations.update_project(
            project["id"],
            {"default_execution_mode": "danger-full-access"},
        )
        conversation = server.manager.conversations.create(project["id"])

        status, payload, _ = request(
            server,
            "POST",
            "/api/runs",
            {
                "conversation_id": conversation["id"],
                "request": "go",
                "full_access_approved": True,
            },
            token="test-token",
        )

        assert status == 403
        assert "maintainer" in payload["error"]
        assert not server.manager.conversations.get(conversation["id"])["messages"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_full_access_flag_in_the_body_never_authorizes_a_run(tmp_path):
    """Seule une approbation durable et validée autorise un accès complet."""
    server, thread = start_server(tmp_path, "maintainer")
    try:
        project = server.manager.conversations.create_project("Pilote")
        server.manager.conversations.update_project(
            project["id"],
            {"default_execution_mode": "danger-full-access"},
        )
        conversation = server.manager.conversations.create(project["id"])
        body = {
            "conversation_id": conversation["id"],
            "request": "Corrige le bug",
            "full_access_approved": True,
        }

        status, payload, _ = request(
            server, "POST", "/api/runs", body, token="test-token"
        )

        assert status == 428
        assert payload["approval"] == "run"
        approval_id = payload["approval_id"]
        pending = server.manager.approvals.get(approval_id)
        assert pending["status"] == "pending"
        assert "full_access_approved" not in pending["payload"]

        # Une approbation encore pending ne vaut pas autorisation.
        status, _, _ = request(
            server,
            "POST",
            "/api/runs",
            {**body, "approval_id": approval_id},
            token="test-token",
        )
        assert status == 428

        server.manager.approvals.decide(approval_id, "approved")
        status, _, _ = request(
            server,
            "POST",
            "/api/runs",
            {**body, "approval_id": approval_id},
            token="test-token",
        )
        assert status == 202
        assert server.manager.approvals.get(approval_id)["status"] == "consumed"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_a_consumed_approval_cannot_be_replayed(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        project = server.manager.conversations.create_project("Pilote")
        server.manager.conversations.update_project(
            project["id"],
            {"default_execution_mode": "danger-full-access"},
        )
        conversation = server.manager.conversations.create(project["id"])
        approval = server.manager.approvals.create(
            "full-access",
            conversation["id"],
            project["id"],
            {"request": "Corrige le bug"},
            "test",
        )
        server.manager.approvals.decide(approval["id"], "approved")
        assert server.manager.approvals.consume(
            approval["id"], conversation["id"], "Corrige le bug"
        )

        status, payload, _ = request(
            server,
            "POST",
            "/api/runs",
            {
                "conversation_id": conversation["id"],
                "request": "Corrige le bug",
                "approval_id": approval["id"],
            },
            token="test-token",
        )

        assert status == 428
        assert payload["approval_id"] != approval["id"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_reading_projects_stays_available_to_a_viewer(tmp_path):
    """Joe Web a besoin de la liste des projets pour se rendre."""
    assert required_role("GET", "/api/projects") == "viewer"
    assert required_role("GET", "/api/projects/abc") == "viewer"
    assert required_role("POST", "/api/projects") == "maintainer"
    assert required_role("PATCH", "/api/projects/abc") == "maintainer"

    for role in ("viewer", "operator", "maintainer"):
        server, thread = start_server(tmp_path / role, role)
        try:
            status, payload, _ = request(
                server, "GET", "/api/projects", token="test-token"
            )
            assert status == 200
            assert isinstance(payload, list)
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_a_viewer_can_acknowledge_a_completion_but_not_edit(tmp_path):
    server, thread = start_server(tmp_path, "viewer")
    try:
        conversation = server.manager.conversations.create(None)
        server.manager.conversations.append_message(
            conversation["id"], "assistant", "fini"
        )
        assert server.manager.conversations.get(
            conversation["id"]
        )["unread_completion"] is True

        status, _, _ = request(
            server,
            "PATCH",
            f"/api/conversations/{conversation['id']}",
            {"unread_completion": False},
            token="test-token",
        )
        assert status == 200
        assert server.manager.conversations.get(
            conversation["id"]
        )["unread_completion"] is False

        status, payload, _ = request(
            server,
            "PATCH",
            f"/api/conversations/{conversation['id']}",
            {"title": "Renommée"},
            token="test-token",
        )
        assert status == 403
        assert "operator" in payload["error"]
        assert server.manager.conversations.get(
            conversation["id"]
        )["title"] != "Renommée"
    finally:
        server.shutdown()
        thread.join(timeout=2)
