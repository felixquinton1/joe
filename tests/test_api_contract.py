import http.client
import json
import threading
import time

from joe import __version__
from joe.http_utils import MAX_JSON_BODY_BYTES
from joe.web import Handler, JoeServer, LiveRun, RunManager
from joe.web_server import API_VERSION


def start_server(tmp_path):
    server = JoeServer(("127.0.0.1", 0), Handler)
    server.manager = RunManager(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_contract_exposes_independent_api_version(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == 200
        assert payload["version"] == __version__
        assert payload["api_version"] == API_VERSION == "1.0"
        assert isinstance(payload["providers"], list)
        assert isinstance(payload["modes"], list)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_returns_bounded_json_errors(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        connection.request(
            "POST",
            "/api/runs",
            body="{",
            headers={"Content-Type": "application/json"},
        )
        invalid = connection.getresponse()
        assert invalid.status == 400
        assert "error" in json.loads(invalid.read())

        connection.request(
            "POST",
            "/api/projects",
            body=b"",
            headers={"Content-Length": str(MAX_JSON_BODY_BYTES + 1)},
        )
        oversized = connection.getresponse()
        assert oversized.status == 413
        assert "error" in json.loads(oversized.read())

        connection.putrequest("POST", "/api/runs")
        connection.putheader("Content-Type", "application/json")
        connection.endheaders()
        missing_length = connection.getresponse()
        assert missing_length.status == 411
        assert "error" in json.loads(missing_length.read())
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_reserves_one_run_per_conversation(tmp_path, monkeypatch):
    release = threading.Event()

    def hold_run(self, run, *args):
        release.wait(timeout=5)
        with run.condition:
            run.done = True
            run.finished_at = time.time()
            run.condition.notify_all()

    monkeypatch.setattr(RunManager, "_execute", hold_run)
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create()
    barrier = threading.Barrier(2)
    responses = []

    def launch(request):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        barrier.wait()
        connection.request(
            "POST",
            "/api/runs",
            body=json.dumps(
                {
                    "request": request,
                    "conversation_id": conversation["id"],
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        responses.append((response.status, json.loads(response.read())))
        connection.close()

    workers = [
        threading.Thread(target=launch, args=(request,))
        for request in ("premier", "second")
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        assert sorted(status for status, _ in responses) == [202, 409]
        conflict = next(payload for status, payload in responses if status == 409)
        assert "error" in conflict
        messages = server.manager.conversations.get(conversation["id"])["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
    finally:
        release.set()
        for worker in workers:
            worker.join(timeout=2)
        server.shutdown()
        thread.join(timeout=2)


def test_contract_sse_cursors_resume_strictly_after_event(tmp_path):
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create()
    run = LiveRun("contract-run", "continue", conversation["id"])
    run.emit({"type": "progress", "message": "étape 1"})
    run.emit({"type": "complete", "response": "terminé"})
    run.done = True
    run.finished_at = time.time()
    server.manager.live[run.run_id] = run
    connections = []

    try:
        bodies = []
        for path, headers in (
            (f"/api/events/{run.run_id}", {"Last-Event-ID": "1"}),
            (f"/api/events/{run.run_id}?after=1", {}),
        ):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            connections.append(connection)
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            assert response.status == 200
            bodies.append(response.read())

        for body in bodies:
            assert b"id: 1\n" not in body
            assert b"id: 2\n" in body
            assert b'"event_id": 2' in body
    finally:
        for connection in connections:
            connection.close()
        server.shutdown()
        thread.join(timeout=2)
