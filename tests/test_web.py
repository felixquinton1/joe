import http.client
import json
import threading

from joe.models import Intent, Mode, Route
from joe.web import (
    Handler,
    JoeServer,
    LiveRun,
    RunManager,
    _complex_request,
    build_quota_notice,
)


def start_server(tmp_path):
    server = JoeServer(("127.0.0.1", 0), Handler)
    server.manager = RunManager(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_web_status_and_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "joe.web.usage_status",
        lambda: [{"provider": "codex", "available": True, "windows": []}],
    )
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["project"] == str(tmp_path.resolve())
        assert payload["version"]
        assert payload["conversation_store"].endswith(
            ".agentflow/conversations.json"
        )
        assert "joe/backups" in payload["conversation_backup"]
        assert "codex" in payload["providers"]

        connection.request("GET", "/api/capabilities")
        response = connection.getresponse()
        capabilities = json.loads(response.read())
        assert response.status == 200
        assert "models" in capabilities["codex"]
        assert "execution_modes" in capabilities["claude"]

        connection.request("POST", "/api/conversations", body="{}")
        response = connection.getresponse()
        conversation = json.loads(response.read())
        assert response.status == 201

        connection.request("GET", f"/api/conversations/{conversation['id']}")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["settings"]["agent"] == ""

        body = json.dumps({"pinned": True, "settings": {"agent": "claude"}})
        connection.request(
            "PATCH",
            f"/api/conversations/{conversation['id']}",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert json.loads(response.read())["pinned"] is True

        body = json.dumps({"name": "Phase D"})
        connection.request(
            "POST",
            "/api/projects",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        project = json.loads(response.read())
        assert response.status == 201

        body = json.dumps({"context": "Contexte commun"})
        connection.request(
            "PATCH",
            f"/api/projects/{project['id']}",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert json.loads(response.read())["context"] == "Contexte commun"

        live = LiveRun("cancel-test", "long request", conversation["id"])
        server.manager.live[live.run_id] = live
        connection.request("POST", "/api/runs/cancel-test/cancel", body="{}")
        response = connection.getresponse()
        assert response.status == 202
        assert live.cancel_event.is_set()

        connection.request("GET", "/api/usage")
        response = connection.getresponse()
        usage = json.loads(response.read())
        assert response.status == 200
        assert usage[0]["provider"] == "codex"

        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        page = response.read()
        assert b"AI control room" in page
        assert "L’IA à la mode chez les jeunes".encode() in page
        assert b'id="cancel-project"' in page

        connection.request("GET", "/app.js")
        response = connection.getresponse()
        script = response.read()
        assert response.status == 200
        assert b"renderMarkdown" in script
        assert b"isTableSeparator" in script
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_web_rejects_empty_requests(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        body = json.dumps({"request": ""})
        connection.request(
            "POST",
            "/api/runs",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 400
        assert "required" in json.loads(response.read())["error"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_web_deletes_conversation(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request("POST", "/api/conversations", body="{}")
        response = connection.getresponse()
        conversation = json.loads(response.read())

        connection.request("DELETE", f"/api/conversations/{conversation['id']}")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["deleted"] is True

        connection.request("GET", f"/api/conversations/{conversation['id']}")
        response = connection.getresponse()
        assert response.status == 404
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_quota_notice_exposes_reset_and_available_fallback_models():
    notice = build_quota_notice(
        "claude",
        [
            {
                "provider": "claude",
                "windows": [{"name": "Opus · 7 jours", "resets_at": 1_800_000_000}],
                "message": None,
            }
        ],
        {
            "gemini": {
                "available": True,
                "models": [{"id": "auto", "label": "auto"}],
            },
            "codex": {"available": False, "models": []},
        },
        {"claude": ["gemini", "codex"]},
    )

    assert notice["type"] == "quota_notice"
    assert notice["windows"][0]["name"] == "Opus · 7 jours"
    assert notice["alternatives"] == [
        {"provider": "gemini", "models": ["auto"]}
    ]
    assert notice["automatic_fallback"] is True


def test_complex_requests_get_high_effort_defaults():
    route = Route(Intent.MODIFY, Mode.FAST, "codex")

    assert _complex_request("Implémente cette architecture", route) is True
    assert _complex_request("Quel est ce fichier ?", route) is False
