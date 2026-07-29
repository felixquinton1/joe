import http.client
import json
import stat
import threading

from joe.auth import LocalAuth, load_or_create_token, required_role, rotate_token
from joe.web import Handler, JoeServer, RunManager


def start_server(tmp_path, role):
    server = JoeServer(("127.0.0.1", 0), Handler)
    server.manager = RunManager(tmp_path)
    server.auth = LocalAuth("test-token", role, True)
    server.auth_path = tmp_path / "auth-token"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


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
    assert required_role("GET", "/api/projects") == "maintainer"


def test_status_is_public_but_api_requires_a_token(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        status, payload, _ = request(server, "GET", "/api/status")
        assert status == 200
        assert payload["auth_required"] is True
        assert payload["profile"] == "maintainer"

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
