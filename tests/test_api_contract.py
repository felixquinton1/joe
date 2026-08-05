import http.client
import base64
import json
import threading
import time
from types import SimpleNamespace

from joe import __version__
from joe.auth import LocalAuth
from joe.http_utils import MAX_JSON_BODY_BYTES
from joe.web import Handler, JoeServer, LiveRun, RunManager
from joe.web_server import API_VERSION


def start_server(tmp_path):
    server = JoeServer(("127.0.0.1", 0), Handler)
    server.auth = LocalAuth()
    server.manager = RunManager(tmp_path)
    # Les tests qui n'exercent pas la porte d'approbation travaillent en accès
    # automatique ; ceux qui la testent règlent ai_access explicitement.
    for project in server.manager.conversations.list_projects():
        server.manager.conversations.update_project(project["id"], {"ai_access": "auto"})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def json_request(connection, method, path, payload=None):
    body = None if payload is None else json.dumps(payload)
    headers = {} if body is None else {"Content-Type": "application/json"}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    return response.status, json.loads(raw) if raw else None


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
        assert payload["api_version"] == API_VERSION == "1.3"
        assert isinstance(payload["providers"], list)
        assert isinstance(payload["modes"], list)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_read_only_discovery_endpoints(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        for path in (
            "/api/capabilities",
            "/api/usage",
            "/api/projects",
            "/api/conversations",
            "/api/runs/active",
            "/api/history",
            "/api/files?project=free",
        ):
            status, payload = json_request(connection, "GET", path)
            assert status == 200
            assert payload is not None
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_uploads_lists_downloads_and_deletes_project_file(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, item = json_request(
            connection,
            "POST",
            "/api/files",
            {
                "project_id": "free",
                "name": "notes.md",
                "content_type": "text/markdown",
                "data": base64.b64encode(b"# Notes").decode(),
            },
        )
        assert status == 201
        assert item["name"] == "notes.md"
        assert "path" not in item

        status, items = json_request(
            connection,
            "GET",
            "/api/files?project=free",
        )
        assert status == 200
        assert items == [item]

        status, results = json_request(
            connection,
            "GET",
            "/api/search?q=notes",
        )
        assert status == 200
        assert any(
            result["type"] == "file" and result["file_id"] == item["id"]
            for result in results
        )

        connection.request(
            "GET",
            f"/api/files/{item['id']}/download",
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b"# Notes"

        status, payload = json_request(
            connection,
            "DELETE",
            f"/api/files/{item['id']}",
        )
        assert status == 200
        assert payload == {"deleted": True}
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_requires_confirmation_for_full_access(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        server.manager.conversations.update_project(
            "main",
            {"ai_access": "manual"},
        )
        conversation = server.manager.conversations.create("main")
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )

        status, payload = json_request(
            connection,
            "POST",
            "/api/runs",
            {
                "conversation_id": conversation["id"],
                "request": "Implémente et teste cette fonctionnalité",
                "agent": "claude",
                "mode": "fast",
            },
        )

        assert status == 428
        assert payload["approval"] == "run"
        approval_id = payload["approval_id"]
        assert approval_id
        assert not server.manager.conversations.get(
            conversation["id"]
        )["messages"]

        # Un drapeau posé par le client ne vaut jamais autorisation : seule une
        # approbation durable et validée débloque le run.
        status, _ = json_request(
            connection,
            "POST",
            "/api/runs",
            {
                "conversation_id": conversation["id"],
                "request": "Implémente et teste cette fonctionnalité",
                "agent": "claude",
                "mode": "fast",
                "full_access_approved": True,
            },
        )
        assert status == 428

        server.manager.approvals.decide(approval_id, "approved")
        server.manager.start = lambda *args, **kwargs: SimpleNamespace(
            run_id="approved"
        )
        status, payload = json_request(
            connection,
            "POST",
            "/api/runs",
            {
                "conversation_id": conversation["id"],
                "request": "Implémente et teste cette fonctionnalité",
                "agent": "claude",
                "mode": "fast",
                "approval_id": approval_id,
            },
        )
        assert status == 202
        assert payload["run_id"] == "approved"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_persists_and_consumes_full_access_approval(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        server.manager.conversations.update_project(
            "main",
            {"ai_access": "manual"},
        )
        conversation = server.manager.conversations.create("main")
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        request = {
            "conversation_id": conversation["id"],
            "request": "Implémente la correction",
            "agent": "codex",
            "mode": "fast",
        }
        status, pending = json_request(
            connection,
            "POST",
            "/api/runs",
            request,
        )
        assert status == 428

        status, approvals = json_request(
            connection,
            "GET",
            "/api/approvals",
        )
        assert status == 200
        assert approvals[0]["id"] == pending["approval_id"]

        status, approval = json_request(
            connection,
            "PATCH",
            f"/api/approvals/{pending['approval_id']}",
            {"decision": "approved"},
        )
        assert status == 200
        assert approval["status"] == "approved"

        server.manager.start = lambda *args, **kwargs: SimpleNamespace(
            run_id="approved-persistently"
        )
        status, payload = json_request(
            connection,
            "POST",
            "/api/runs",
            {**request, "approval_id": pending["approval_id"]},
        )
        assert status == 202
        assert payload["run_id"] == "approved-persistently"
        assert server.manager.approvals.get(
            pending["approval_id"]
        )["status"] == "consumed"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_project_and_conversation_lifecycle(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, project = json_request(
            connection, "POST", "/api/projects", {"name": "Extension"}
        )
        assert status == 201

        status, project_detail = json_request(
            connection, "GET", f"/api/projects/{project['id']}"
        )
        assert status == 200
        assert project_detail["name"] == "Extension"

        status, renamed_project = json_request(
            connection,
            "PATCH",
            f"/api/projects/{project['id']}",
            {"name": "Extension VS Code"},
        )
        assert status == 200
        assert renamed_project["name"] == "Extension VS Code"

        status, conversation = json_request(
            connection,
            "POST",
            "/api/conversations",
            {"project_id": project["id"]},
        )
        assert status == 201

        status, renamed = json_request(
            connection,
            "PATCH",
            f"/api/conversations/{conversation['id']}",
            {"title": "Lecture seule"},
        )
        assert status == 200
        assert renamed["title"] == "Lecture seule"

        status, detail = json_request(
            connection, "GET", f"/api/conversations/{conversation['id']}"
        )
        assert status == 200
        assert detail["title"] == "Lecture seule"

        status, deleted = json_request(
            connection, "DELETE", f"/api/conversations/{conversation['id']}"
        )
        assert status == 200
        assert deleted["deleted"] is True

        status, missing = json_request(
            connection, "GET", f"/api/conversations/{conversation['id']}"
        )
        assert status == 404
        assert missing is None
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_missing_run_resources_return_404(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, history = json_request(
            connection, "GET", "/api/history/missing-run"
        )
        assert status == 404
        assert history is None

        status, cancelled = json_request(
            connection, "POST", "/api/runs/missing-run/cancel", {}
        )
        assert status == 404
        assert cancelled == {"cancelled": False}

        connection.request("GET", "/api/events/missing-run")
        events = connection.getresponse()
        assert events.status == 404
        events.read()
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_cancels_an_active_run(tmp_path):
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create()
    run = LiveRun("active-run", "continue", conversation["id"])
    server.manager.live[run.run_id] = run
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, cancelled = json_request(
            connection, "POST", f"/api/runs/{run.run_id}/cancel", {}
        )

        assert status == 202
        assert cancelled == {"cancelled": True}
        assert run.cancel_event.is_set()
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_creates_lists_and_cancels_scheduled_automation(tmp_path):
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, plan = json_request(
            connection,
            "POST",
            "/api/automations",
            {
                "title": "Cette nuit",
                "conversation_id": conversation["id"],
                "steps": ["Coder", "Tester"],
                "scheduled_for": time.time() + 3600,
                "execution_mode": "workspace-write",
            },
        )
        assert status == 201
        status, plans = json_request(connection, "GET", "/api/automations")
        assert status == 200
        assert plans[0]["id"] == plan["id"]

        status, cancelled = json_request(
            connection, "POST", f"/api/automations/{plan['id']}/cancel", {}
        )
        assert status == 202
        assert cancelled["status"] == "cancelled"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_exposes_and_deletes_durable_tasks(tmp_path):
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create()
    server.manager.tasks.create(
        "task1",
        "Analyse le projet",
        conversation["id"],
        conversation["project_id"],
        workspace=tmp_path,
        base_workspace=tmp_path,
        isolated=False,
    )
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, tasks = json_request(connection, "GET", "/api/tasks")
        assert status == 200
        assert tasks[0]["id"] == "task1"

        status, report = json_request(
            connection, "GET", "/api/tasks/task1/diff"
        )
        assert status == 200
        assert report["message"].startswith("Cette tâche")

        server.manager.tasks.update("task1", status="completed")
        status, deleted = json_request(
            connection, "DELETE", "/api/tasks/task1"
        )
        assert status == 200
        assert deleted == {"deleted": True}
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_exposes_persisted_run_history(tmp_path):
    server, thread = start_server(tmp_path)
    run_payload = {
        "request": "Stabilise le contrat",
        "route": {"provider": "codex", "mode": "fast"},
        "final": "Contrat stabilisé",
        "workflow": [{"provider": "codex", "status": "complete"}],
    }
    history_path = server.manager.orchestrator.memory.runs / "contractrun.json"
    history_path.write_text(json.dumps(run_payload))
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, history = json_request(connection, "GET", "/api/history")
        assert status == 200
        assert history == [
            {
                "id": "contractrun",
                "request": run_payload["request"],
                "route": run_payload["route"],
                "final": run_payload["final"],
            }
        ]

        status, detail = json_request(
            connection, "GET", "/api/history/contractrun"
        )
        assert status == 200
        assert detail == run_payload
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_contract_rejects_deleting_an_active_conversation(tmp_path):
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create()
    run = LiveRun("active-run", "continue", conversation["id"])
    server.manager.live[run.run_id] = run
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, conflict = json_request(
            connection,
            "DELETE",
            f"/api/conversations/{conversation['id']}",
        )

        assert status == 409
        assert conflict == {
            "error": "Interromps la tâche avant de supprimer la conversation."
        }
        assert server.manager.conversations.get(conversation["id"]) is not None
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


def test_the_authorized_route_is_the_one_that_runs(tmp_path):
    """Le garde-fou d'accès complet et l'exécution partagent une décision.

    Avant, `requires_full_access_approval` appelait `resolve_route` sans
    `wait_for_provider` ni fournisseur réservé, alors que `_execute` les
    appliquait : la route autorisée n'était pas structurellement celle
    exécutée.
    """
    server, thread = start_server(tmp_path)
    try:
        manager = server.manager
        conversation = manager.conversations.create("main")
        decisions = []
        original = manager.decide

        def counting_decide(*args, **kwargs):
            decision = original(*args, **kwargs)
            decisions.append(decision)
            return decision

        manager.decide = counting_decide
        manager.start = lambda *args, **kwargs: SimpleNamespace(
            run_id="once", decision=kwargs.get("decision")
        )

        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        status, payload = json_request(
            connection,
            "POST",
            "/api/runs",
            {
                "conversation_id": conversation["id"],
                "request": "Analyse ce dépôt sans rien modifier",
            },
        )

        assert status == 202
        # Une seule décision pour toute la requête : plus de recalcul entre
        # l'autorisation et le démarrage.
        assert len(decisions) == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)
