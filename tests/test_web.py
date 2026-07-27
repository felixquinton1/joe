import http.client
import json
import threading

from joe.web import Handler, JoeServer, RunManager


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
        assert "codex" in payload["providers"]

        connection.request("GET", "/api/capabilities")
        response = connection.getresponse()
        capabilities = json.loads(response.read())
        assert response.status == 200
        assert "models" in capabilities["codex"]
        assert "execution_modes" in capabilities["claude"]

        connection.request("GET", "/api/usage")
        response = connection.getresponse()
        usage = json.loads(response.read())
        assert response.status == 200
        assert usage[0]["provider"] == "codex"

        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        assert b"AI control room" in response.read()

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
