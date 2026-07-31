import http.client
import json
import threading
import time
from types import SimpleNamespace

from joe import __version__, provider_registry
from joe.auth import LocalAuth
from joe.models import Intent, Mode, Route
from joe.provider_registry import ProviderSpec
from joe.web import (
    Handler,
    JoeServer,
    LiveRun,
    RunManager,
    _complex_request,
    _operational_validation,
    build_quota_notice,
)
from joe.http_utils import MAX_JSON_BODY_BYTES, validate_bind
from joe.web_runs import _resolve_execution_mode, _run_summary
from joe.web_server import API_VERSION


def start_server(tmp_path):
    server = JoeServer(("127.0.0.1", 0), Handler)
    server.auth = LocalAuth()
    server.manager = RunManager(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_web_status_and_assets(tmp_path, monkeypatch):
    usage_calls = []
    monkeypatch.setattr(
        "joe.web.usage_status",
        lambda force=False: usage_calls.append(force)
        or [{"provider": "codex", "available": True, "windows": []}],
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
        assert payload["api_version"] == API_VERSION
        assert payload["conversation_store"].endswith(
            ".agentflow/conversations.json"
        )
        assert "joe/backups" in payload["conversation_backup"]
        assert "codex" in payload["providers"]
        assert {"id": "codex", "label": "Codex"} in payload["provider_catalog"]

        connection.request("GET", "/app.js")
        response = connection.getresponse()
        app = response.read().decode()
        assert response.status == 200
        assert "reconcileRun(conversationId, runId)" in app
        assert "message.run_id === previousRunId" in app
        assert "renderHistoricalRunSummary(completed" in app
        assert "if (!options.suppressScroll)" in app
        assert "if (options.forceScroll)" in app
        assert "{ forceScroll: true }" in app
        assert "renderApproval(approval)" in app
        assert "task.pipeline" in app
        assert "dragging-files" in app
        assert f'const APP_VERSION = "{__version__}";' in app
        assert "window.location.reload()" not in app

        connection.request("GET", "/style.css")
        response = connection.getresponse()
        stylesheet = response.read().decode()
        assert response.status == 200
        assert ".composer {" in stylesheet
        assert "z-index: 60;" in stylesheet
        assert ".tool-panel {" in stylesheet
        assert "z-index: 1000;" in stylesheet

        connection.request("GET", "/markdown.js")
        response = connection.getresponse()
        markdown = response.read().decode()
        assert response.status == 200
        assert "renderMarkdown" in markdown
        assert "isTableSeparator" in markdown

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
        connection.request("GET", "/api/runs/active")
        response = connection.getresponse()
        active = json.loads(response.read())
        assert active[0]["run_id"] == "cancel-test"
        assert active[0]["conversation_id"] == conversation["id"]

        connection.request("POST", "/api/runs/cancel-test/cancel", body="{}")
        response = connection.getresponse()
        assert response.status == 202
        assert live.cancel_event.is_set()

        connection.request("GET", "/api/usage")
        response = connection.getresponse()
        usage = json.loads(response.read())
        assert response.status == 200
        assert usage[0]["provider"] == "codex"

        connection.request("GET", "/api/usage?force=1")
        response = connection.getresponse()
        response.read()
        assert response.status == 200
        assert usage_calls[-1] is True

        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        page = response.read()
        assert b"AI control room" in page
        assert "L’IA à la mode chez les jeunes".encode() not in page
        assert b'id="cancel-project"' in page
        assert b'id="prompt-queue"' in page
        assert b'data-resizer="left"' in page
        assert b'data-resizer="right"' in page
        assert b'id="toggle-history"' in page
        assert b'id="toggle-activity"' in page
        assert b'src="/markdown.js"' in page
        assert b'src="/i18n.js"' in page
        assert b'id="language"' in page
        assert page.index(b'id="language-en"') < page.index(b'id="language-fr"')
        assert b"<select id=\"language\"" not in page
        assert b'data-i18n="slogan"' not in page
        assert b'class="topbar"' not in page
        assert b'class="raw-panel"' not in page
        assert b'class="activity-tools"' in page
        assert b'id="preferences-dialog"' in page
        assert b'id="open-preferences"' in page
        assert b'id="global-skills"' in page
        assert b'class="dialog-advanced"' in page
        assert b'class="dialog-section"' in page
        assert "Skills communs".encode() in page
        assert "Créer un skill".encode() in page
        assert b'name="skill-create-scope"' in page
        assert b'name="skill-import-scope"' in page
        assert "SSH/Jean Zay".encode() not in page
        assert b'id="project"' not in page
        assert b'<span class="brand-mark">J</span>' in page
        assert b"<option>codex</option>" not in page

        connection.request("GET", "/i18n.js")
        response = connection.getresponse()
        translations = response.read()
        assert response.status == 200
        assert b"initialLanguage" in translations
        assert b"cool kids" not in translations

        connection.request("GET", "/app.js")
        response = connection.getresponse()
        script = response.read()
        assert response.status == 200
        assert b"renderMarkdown" in script
        assert b"launchNextQueued" in script
        assert b"copyButton" in script
        assert "⧉ Copier".encode() in script
        assert "✓ Copié".encode() in script
        assert b"moveConversation" in script
        assert b"setupPanelResizers" in script
        assert b"resizeComposer" in script
        assert b"toggleMobilePanel" in script
        assert b"loadActiveRuns" in script
        assert b"setSummaryPending" in script
        assert b"event.cli_version" not in script
        assert b"updateWorkflowFallback" in script
        assert b"fallback_from" in script
        assert b"updateProviderMenu" in script
        assert b"lastEventId" in script
        assert b"setupSelectMenu" in script
        assert b"setAgentMeta" in script

        connection.request("GET", "/app_conversations.js")
        response = connection.getresponse()
        conversations_script = response.read()
        assert response.status == 200
        assert b"loadGlobalSkills" in conversations_script
        assert b"promoteSkill" in conversations_script
        assert b"{ suppressScroll: true }" in conversations_script
        assert b"requestedMessage.scrollIntoView" not in conversations_script
        assert b"conversationViewport.scrollTop = Math.max" in conversations_script

        connection.request("GET", "/style.css")
        response = connection.getresponse()
        style = response.read()
        assert response.status == 200
        assert b"max-height: min(42vh, 360px)" in style
        assert b".composer textarea" in style
        assert b".activity-panel.mobile-open" in style
        assert b"prefers-reduced-motion" in style
        assert b".conversation-item:hover .pin-button" in style
        assert b".message.workflow-summary-pending" in style
        assert b".copy-button.copied" in style
        assert b".language-picker" in style
        assert b".language-picker button.active" in style
        assert b"height: 100dvh" in style
        assert b"grid-template-columns: var(--left-panel)" in style
        assert b"overflow: visible" in style
        assert b"overscroll-behavior: contain" in style
        assert b"top: calc(100% + 8px)" in style
        assert b"@keyframes dropdown-in" in style
        assert b"grid-template-rows: 0fr" in style
        assert b".select-menu-options" in style
        assert b".project-dialog .select-menu-trigger" in style
        assert b"animation: dialog-in" in style
        assert b".agent-meta" in style
        assert b".skill-promote" in style
        assert b".dialog-advanced" in style
    finally:
        server.shutdown()
        thread.join(timeout=2)
        thread.join(timeout=2)


def test_skills_import_promote_and_global_listing(tmp_path, monkeypatch):
    from joe import skills

    global_root = tmp_path / "global-skills"
    monkeypatch.setattr(skills, "global_skills_root", lambda: global_root)
    source = tmp_path / "conventions"
    source.mkdir()
    (source / "SKILL.md").write_text("Toujours écrire des tests ciblés.")

    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)

        connection.request(
            "POST",
            "/api/projects",
            body=json.dumps({"name": "Phase D"}),
            headers={"Content-Type": "application/json"},
        )
        project = json.loads(connection.getresponse().read())

        connection.request(
            "POST",
            f"/api/projects/{project['id']}/skills/import",
            body=json.dumps({"source": str(source), "name": "conventions"}),
            headers={"Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 201

        connection.request("GET", f"/api/projects/{project['id']}/skills")
        listed = json.loads(connection.getresponse().read())
        assert [item["name"] for item in listed] == ["conventions"]
        assert listed[0]["active"] is True

        connection.request("GET", "/api/skills/global")
        assert json.loads(connection.getresponse().read()) == []

        connection.request(
            "POST",
            f"/api/projects/{project['id']}/skills/promote",
            body=json.dumps({"name": "conventions"}),
            headers={"Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 201

        connection.request("GET", "/api/skills/global")
        common = json.loads(connection.getresponse().read())
        assert [item["name"] for item in common] == ["conventions"]
        assert common[0]["scope"] == "global"

        connection.request(
            "POST",
            f"/api/projects/{project['id']}/skills/promote",
            body=json.dumps({"name": "absent"}),
            headers={"Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 400
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_skills_can_be_created_or_imported_at_both_scopes(tmp_path, monkeypatch):
    from joe import skills

    global_root = tmp_path / "global-skills"
    monkeypatch.setattr(skills, "global_skills_root", lambda: global_root)
    source = tmp_path / "shared-source"
    source.mkdir()
    (source / "SKILL.md").write_text("Règle commune importée.")
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/projects",
            body=json.dumps({"name": "Projet"}),
            headers={"Content-Type": "application/json"},
        )
        project = json.loads(connection.getresponse().read())

        connection.request(
            "POST",
            f"/api/projects/{project['id']}/skills/create",
            body=json.dumps({
                "name": "Python simple",
                "instructions": "Garder le code lisible.",
            }),
            headers={"Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 201

        connection.request(
            "POST",
            "/api/skills/global/create",
            body=json.dumps({
                "name": "Revue commune",
                "instructions": "Relire avant livraison.",
            }),
            headers={"Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 201

        connection.request(
            "POST",
            "/api/skills/global/import",
            body=json.dumps({"source": str(source)}),
            headers={"Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 201

        connection.request("GET", f"/api/projects/{project['id']}/skills")
        project_skills = json.loads(connection.getresponse().read())
        assert [item["name"] for item in project_skills] == ["python-simple"]
        connection.request("GET", "/api/skills/global")
        global_skills = json.loads(connection.getresponse().read())
        assert [item["name"] for item in global_skills] == [
            "revue-commune",
            "shared-source",
        ]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_explicit_skill_prompt_is_handled_locally_without_provider(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        conversation = server.manager.conversations.create("main")
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/runs",
            body=json.dumps({
                "request": "créé un skill test pour ce projet pour voir si ca marche",
                "conversation_id": conversation["id"],
            }),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 202
        run = server.manager.live.get(payload["run_id"])
        assert run is not None
        assert run.done is True
        assert run.events[0]["primary"] == "joe"
        skill = tmp_path / ".agentflow/skills/test/SKILL.md"
        assert skill.exists()
        assert "SKILL_TEST_ACTIF" in skill.read_text()
        messages = server.manager.conversations.get(conversation["id"])["messages"]
        assert messages[-1]["provider"] == "joe"
        assert "créé comme skill du projet" in messages[-1]["content"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_underspecified_skill_prompt_returns_actionable_error(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        conversation = server.manager.conversations.create("main")
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/runs",
            body=json.dumps({
                "request": "crée un skill qualité",
                "conversation_id": conversation["id"],
            }),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert "nom et les instructions" in payload["error"]
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


def test_web_api_uses_the_provider_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_registry,
        "PROVIDERS",
        provider_registry.PROVIDERS + (ProviderSpec("fixture", "Fixture"),),
    )
    monkeypatch.setattr(RunManager, "_execute", lambda self, *args: None)
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
        )
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        status = json.loads(response.read())

        assert "fixture" in status["providers"]
        assert {"id": "fixture", "label": "Fixture"} in status["provider_catalog"]

        connection.request(
            "POST",
            "/api/runs",
            body=json.dumps(
                {
                    "request": "test",
                    "conversation_id": conversation["id"],
                    "agent": "fixture",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 202
        response.read()
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_web_rejects_invalid_or_oversized_json_uniformly(tmp_path):
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/conversations",
            body="{",
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 400
        assert json.loads(response.read())["error"] == "JSON invalide."

        connection.request(
            "POST",
            "/api/projects",
            body=b"",
            headers={"Content-Length": str(MAX_JSON_BODY_BYTES + 1)},
        )
        response = connection.getresponse()
        assert response.status == 413
        assert "maximum" in json.loads(response.read())["error"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_two_concurrent_http_runs_reserve_one_conversation_atomically(
    tmp_path,
    monkeypatch,
):
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
            "127.0.0.1",
            server.server_port,
            timeout=5,
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
        assert "déjà active" in conflict["error"]
        messages = server.manager.conversations.get(conversation["id"])["messages"]
        assert [message["role"] for message in messages] == ["user"]
        assert messages[0]["content"] in {"premier", "second"}
    finally:
        release.set()
        for worker in workers:
            worker.join(timeout=2)
        server.shutdown()
        thread.join(timeout=2)


def test_run_reservation_rolls_back_if_pending_persistence_fails(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    monkeypatch.setattr(
        manager,
        "_write_pending",
        lambda *args: (_ for _ in ()).throw(OSError("disk full")),
    )

    try:
        manager.start(
            "test",
            conversation["id"],
            None,
            None,
            None,
            None,
            None,
        )
    except OSError as error:
        assert str(error) == "disk full"
    else:
        raise AssertionError("The persistence failure must abort the run")

    assert manager.active_runs() == []
    assert manager.conversations.get(conversation["id"])["messages"] == []


def test_sse_reconnect_resumes_after_last_received_event(tmp_path):
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create()
    run = LiveRun("reconnect-run", "continue", conversation["id"])
    run.emit({"type": "progress", "message": "étape 1"})
    server.manager.live[run.run_id] = run

    first = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_port,
        timeout=5,
    )
    reconnects = []
    try:
        first.request("GET", f"/api/events/{run.run_id}")
        response = first.getresponse()
        assert response.status == 200
        assert response.readline() == b"id: 1\n"
        first_payload = json.loads(
            response.readline().removeprefix(b"data: ").decode()
        )
        assert first_payload["message"] == "étape 1"
        assert response.readline() == b"\n"
        first.close()

        run.emit({"type": "complete", "response": "terminé"})
        with run.condition:
            run.done = True
            run.finished_at = time.time()
            run.condition.notify_all()

        second = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=5,
        )
        reconnects.append(second)
        second.request(
            "GET",
            f"/api/events/{run.run_id}",
            headers={"Last-Event-ID": "1"},
        )
        resumed = second.getresponse()
        body = resumed.read()

        assert resumed.status == 200
        assert b"id: 1\n" not in body
        assert b"id: 2\n" in body
        assert "terminé".encode() in body

        query_reconnect = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=5,
        )
        reconnects.append(query_reconnect)
        query_reconnect.request(
            "GET",
            f"/api/events/{run.run_id}?after=1",
        )
        query_body = query_reconnect.getresponse().read()
        assert b"id: 1\n" not in query_body
        assert b"id: 2\n" in query_body
    finally:
        first.close()
        for connection in reconnects:
            connection.close()
        server.shutdown()
        thread.join(timeout=2)


def test_non_local_bind_requires_explicit_opt_in():
    validate_bind("127.0.0.1")
    validate_bind("::1")

    try:
        validate_bind("0.0.0.0")
    except ValueError as error:
        assert "--allow-remote" in str(error)
    else:
        raise AssertionError("A remote bind must require explicit opt-in")

    validate_bind("0.0.0.0", allow_remote=True)


def test_project_scope_uses_only_explicit_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    manager = RunManager(tmp_path)
    workspace = tmp_path / "workspace"
    extra = tmp_path / "vision"
    workspace.mkdir()
    extra.mkdir()
    project = manager.conversations.create_project("Vision")
    manager.conversations.update_project(
        project["id"],
        {
            "workspace_root": str(workspace),
            "additional_roots": [str(extra)],
            "default_execution_mode": "workspace-write",
        },
    )
    conversation = manager.conversations.create(project["id"])

    root, additional, remote, execution_mode = manager._project_scope(
        conversation["id"]
    )

    assert root == workspace.resolve()
    assert additional == (extra.resolve(),)
    assert remote is True
    assert execution_mode == "workspace-write"

    manager.conversations.update(
        conversation["id"],
        {"settings": {"web_access": "off"}},
    )
    assert manager._project_scope(conversation["id"])[2] is False


def test_only_explicit_operational_checks_use_project_validation_permission():
    assert _operational_validation(
        "Fais un audit global et exécute la suite de tests"
    )
    assert _operational_validation("git fetch puis vérifie origin/main")
    assert not _operational_validation("Explique-moi l’architecture de Joe")


def test_project_write_default_applies_to_modify_routes():
    route = Route(Intent.MODIFY, Mode.FAST, "codex")

    assert _resolve_execution_mode(
        None,
        "danger-full-access",
        route,
        "Ajoute le logo",
    ) == "danger-full-access"
    assert _resolve_execution_mode(
        "read-only",
        "danger-full-access",
        route,
        "Ajoute le logo",
    ) == "read-only"


def test_full_access_approval_is_required_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    manager = RunManager(tmp_path)
    manager.conversations.update_project(
        "main",
        {"default_execution_mode": "danger-full-access"},
    )
    conversation = manager.conversations.create("main")

    assert manager.requires_full_access_approval(
        "Implémente et teste cette fonctionnalité",
        conversation["id"],
        "claude",
        "fast",
        None,
    )
    assert not manager.requires_full_access_approval(
        "Explique cette fonctionnalité",
        conversation["id"],
        "claude",
        "fast",
        None,
    )


def test_consensus_remains_read_only_despite_project_default():
    route = Route(Intent.MODIFY, Mode.CONSENSUS, "codex")

    assert _resolve_execution_mode(
        None,
        "danger-full-access",
        route,
        "Décide de l’architecture",
    ) is None


def test_run_summary_preserves_completed_workflow_and_fallback():
    run = LiveRun("run-1", "request", "conversation")
    run.emit(
        {
            "type": "route",
            "mode": "consensus",
            "primary": "codex",
            "reviewer": "gemini",
            "profile": "operator",
        }
    )
    run.emit(
        {
            "type": "workflow_update",
            "mode": "consensus",
            "stage": "proposal_gemini",
            "provider": "gemini",
            "label": "Proposition",
            "status": "running",
        }
    )
    run.emit(
        {
            "type": "provider_fallback",
            "provider": "gemini",
            "fallback": "claude",
            "error": "timeout",
        }
    )
    run.emit(
        {
            "type": "provider_start",
            "provider": "claude",
            "model": "sonnet",
            "effort": "high",
        }
    )
    run.emit(
        {
            "type": "workflow_update",
            "mode": "consensus",
            "stage": "proposal_gemini",
            "provider": "claude",
            "label": "Proposition",
            "status": "complete",
        }
    )

    summary = _run_summary(run)

    assert summary["route"]["mode"] == "consensus"
    assert summary["route"]["profile"] == "operator"
    assert summary["workflow"][0]["provider"] == "claude"
    assert summary["workflow"][0]["fallback_from"] == "gemini"
    assert summary["workflow"][0]["model"] == "sonnet"


def test_pending_run_state_is_persisted_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    run = LiveRun("persistent", "continue", conversation["id"])

    manager._write_pending(run, "codex", "review", None, "high", None)

    assert manager._read_pending()["persistent"]["request"] == "continue"
    manager._remove_pending("persistent")
    assert manager._read_pending() == {}


def test_completed_live_runs_expire_from_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    manager = RunManager(tmp_path)
    expired = LiveRun("expired", "done", "conversation")
    expired.done = True
    expired.finished_at = 10
    active = LiveRun("active", "work", "conversation")
    manager.live = {"expired": expired, "active": active}

    with manager.lock:
        manager._prune_live_locked(now=10 + manager.LIVE_RUN_TTL_SECONDS)

    assert "expired" not in manager.live
    assert manager.get_run("active") is active


def test_completed_live_run_timer_removes_only_matching_generation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    manager = RunManager(tmp_path)
    completed = LiveRun("same-id", "done", "conversation")
    completed.done = True
    completed.finished_at = time.time()
    manager.live[completed.run_id] = completed

    manager._expire_live_run("same-id", 10)
    assert manager.get_run("same-id") is completed

    manager._expire_live_run("same-id", completed.finished_at)
    assert manager.get_run("same-id") is None


def test_resumed_run_is_announced_without_duplicating_user_message(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    monkeypatch.setattr(RunManager, "_execute", lambda self, *args: None)
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    manager.conversations.append_message(
        conversation["id"], "user", "continue", "persistent"
    )

    run = manager.start(
        "continue",
        conversation["id"],
        "codex",
        "fast",
        None,
        None,
        None,
        run_id="persistent",
        resumed=True,
    )

    messages = manager.conversations.get(conversation["id"])["messages"]
    assert len(messages) == 1
    assert run.events[0]["type"] == "recovered"


def test_background_compaction_saves_successful_gemini_summary(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    manager.conversations.append_message(
        conversation["id"], "assistant", "ancien contexte"
    )

    class Gemini:
        def run(self, *args, **kwargs):
            return SimpleNamespace(ok=True, stdout="Résumé compact")

    orchestrator = SimpleNamespace(
        project=tmp_path,
        providers={"gemini": Gemini()},
        memory=SimpleNamespace(
            config=lambda: {
                "semantic_compaction": {
                    "provider": "gemini",
                    "model": "gemini-3-flash-preview",
                }
            }
        ),
    )
    candidate = {
        "previous_summary": "",
        "transcript": "ASSISTANT: ancien contexte",
        "message_count": 1,
    }

    manager.compacting.add(conversation["id"])
    manager._compact_conversation(
        conversation["id"],
        orchestrator,
        candidate,
    )

    loaded = manager.conversations.get(conversation["id"])
    assert loaded["context_summary"] == "Résumé compact"
    assert conversation["id"] not in manager.compacting


def test_read_only_run_ignores_unrelated_repository_changes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    monkeypatch.setattr(
        "joe.web.build_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Git diff must not be inspected")
        ),
    )
    manager = RunManager(tmp_path)
    run = LiveRun("answer", "simple question", "conversation")

    report = manager._capture_git_report(run)

    assert report["available"] is False
    assert "lecture seule" in report["reason"]


def test_long_fast_answer_does_not_enable_high_effort():
    route = Route(Intent.ANSWER, Mode.FAST, "codex")
    request = "Est-ce que je peux modifier Joe depuis ici ? " * 20

    assert _complex_request(request, route) is False


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


def test_pasted_diagnostic_does_not_raise_effort():
    route = Route(Intent.ANSWER, Mode.FAST, "codex")
    request = (
        "Comment je règle cette erreur ?\n"
        "npm error please double-check permissions\n"
        "npm error at async debug migration"
    )

    assert _complex_request(request, route) is False
