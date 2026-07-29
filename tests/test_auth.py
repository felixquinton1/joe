import http.client
import json
import stat
import threading

from joe.auth import LocalAuth, load_or_create_token, required_role
from joe.web import Handler, JoeServer, RunManager


def start_server(tmp_path, role):
    server = JoeServer(("127.0.0.1", 0), Handler)
    server.manager = RunManager(tmp_path)
    server.auth = LocalAuth("test-token", role, True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def request(server, method, path, payload=None, token=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5
    )
    body = None if payload is None else json.dumps(payload)
    headers = {}
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


def test_capability_matrix_is_centralized():
    assert required_role("GET", "/api/conversations") == "viewer"
    assert required_role("POST", "/api/runs") == "operator"
    assert required_role("POST", "/api/projects") == "maintainer"
    assert required_role("POST", "/api/runs/id/reject") == "maintainer"


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


def test_root_pairs_the_local_browser_with_an_http_only_cookie(tmp_path):
    server, thread = start_server(tmp_path, "maintainer")
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()

        cookie = response.getheader("Set-Cookie")
        assert "joe_token=test-token" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
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
