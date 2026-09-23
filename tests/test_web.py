import http.client
import json
import pytest
import threading
from pathlib import Path

from conftest import build_test_server
import time
from types import SimpleNamespace

from joe import __version__, provider_registry
from joe.models import Intent, Mode, Route
from joe.provider_registry import ProviderSpec
from joe.web import (
    Handler,
    JoeServer,
    LiveRun,
    RunManager,
    _complex_request,
    build_quota_notice,
)
from joe.http_utils import MAX_JSON_BODY_BYTES, validate_bind
from joe.prompt_language import request_language, response_language
from joe.web_runs import RunDecision, _resolve_execution_mode, _run_summary
from joe.web_server import API_VERSION


def start_server(tmp_path):
    return build_test_server(tmp_path)


def test_the_server_listens_on_the_ipv6_loopback_too():
    """`::1` était accepté à la validation puis refusé au bind.

    Un transfert de port qui résout `localhost` en IPv6 — le cas courant sous
    VS Code Remote — ne trouvait alors personne à l'écoute.
    """
    import socket

    from joe.http_utils import validate_bind

    for host, family in (("::1", socket.AF_INET6), ("127.0.0.1", socket.AF_INET)):
        validate_bind(host)
        server = JoeServer((host, 0), Handler)
        try:
            assert server.address_family == family
        finally:
            server.server_close()


def test_the_interface_does_not_expose_the_server_workspace_in_the_brand():
    assets = Path(__file__).resolve().parent.parent / "src" / "joe" / "web_assets"
    page = (assets / "index.html").read_text(encoding="utf-8")
    app = (assets / "app.js").read_text(encoding="utf-8")

    assert 'id="workspace"' not in page
    assert 'workspace.title = t("workspace_root"' not in app


def test_the_page_notices_when_the_server_serves_another_history():
    """Joe peut être relancé depuis un autre dossier, page ouverte.

    L'historique affiché change alors sans prévenir, et toute action sur une
    conversation de l'ancien échoue en « introuvable » : c'est ce qui ressemble
    à une suppression bloquée puis à un projet renommé.
    """
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    assert "servedStore" in app
    assert 'status.conversation_store !== servedStore' in app
    assert 't("store_changed"' in app
    # La comparaison n'a de valeur que si le statut est relu régulièrement.
    assert "loadStatus()" in app.split("setInterval")[-1]


def test_first_run_cli_onboarding_does_not_depend_on_other_panels():
    """A broken conversation/task load must not hide first-run CLI setup."""
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    startup = app.split("window.JoeAuth.pairBrowser()", 1)[1]
    assert 'const ONBOARDING_STORAGE_KEY = "joe-onboarded-v2"' in app
    assert "loadDoctor().catch(reportStartupFailure);" in startup
    assert ".then(loadDoctor)" not in startup


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
        assert payload["conversation_store"].replace("\\", "/").endswith(
            ".agentflow/conversations.json"
        )
        assert "joe/backups" in payload["conversation_backup"].replace("\\", "/")
        assert "codex" in payload["providers"]
        codex = next(
            item for item in payload["provider_catalog"] if item["id"] == "codex"
        )
        assert codex["label"] == "Codex"
        # Le menu Agent lit ce catalogue : sans cette marque, il proposait un
        # agent que le panneau des CLI déclarait absent au même moment.
        assert "available" in codex

        connection.request("GET", "/app.js")
        response = connection.getresponse()
        app = response.read().decode()
        assert response.status == 200
        assert "reconcileRun(conversationId, runId)" in app
        assert "message.run_id === previousRunId" in app
        assert "renderHistoricalRunSummary(completed" in app
        assert "if (!options.suppressScroll)" in app
        assert "if (options.forceScroll)" in app
        assert "{ forceScroll: true, labelKey: \"joe_summary\" }" in app
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

        connection.request("GET", "/katex.min.js")
        response = connection.getresponse()
        katex = response.read()
        assert response.status == 200
        assert b"renderToString" in katex

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

        body = json.dumps({
            "name": "Phase D",
            "workspace_root": str(tmp_path),
            "auto_commit_push": True,
            "quota_provider": "codex",
            "ai_access": "auto",
        })
        connection.request(
            "POST",
            "/api/projects",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        project = json.loads(response.read())
        assert response.status == 201
        assert project["workspace_root"] == str(tmp_path)
        assert project["auto_commit_push"] is True
        assert project["quota_provider"] == "codex"
        assert project["ai_access"] == "auto"

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
        assert b'id="autonomous-mode"' in page
        assert b'id="automation-tab-plan"' in page
        assert b'id="automation-tab-autonomous"' in page
        assert b'id="automation-pane-plan"' in page
        assert b'id="automation-pane-autonomous"' in page
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
        assert b't("copy")' in script
        assert b't("copied")' in script
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
        assert b"suppressScroll: true" in conversations_script
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
    (source / "SKILL.md").write_text("Toujours écrire des tests ciblés.", encoding="utf-8")

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


def test_a_skill_can_be_read_then_removed_at_both_scopes(tmp_path, monkeypatch):
    """Un skill listé est toujours actif : il faut pouvoir le lire et le sortir.

    La liste n'exposait que nom et taille, et aucune route ne supprimait : le
    contexte partagé ne pouvait donc que grossir, sans qu'on puisse vérifier
    ce qu'il contenait.
    """
    from joe import skills

    global_root = tmp_path / "global-skills"
    monkeypatch.setattr(skills, "global_skills_root", lambda: global_root)
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)

        def call(method, path, payload=None):
            connection.request(
                method,
                path,
                body=json.dumps(payload) if payload is not None else None,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"{}")

        _, project = call("POST", "/api/projects", {"name": "Projet"})
        base = f"/api/projects/{project['id']}/skills"
        call("POST", f"{base}/create", {
            "name": "Revue stricte",
            "instructions": "Toujours relire les migrations.",
        })
        call("POST", "/api/skills/global/create", {
            "name": "Commun",
            "instructions": "Règle partagée par tous les projets.",
        })

        # Le contenu est lisible, pas seulement la taille.
        status, skill = call("GET", f"{base}/revue-stricte")
        assert status == 200
        assert "Toujours relire les migrations." in skill["content"]
        status, shared = call("GET", "/api/skills/global/commun")
        assert status == 200
        assert "Règle partagée" in shared["content"]

        # La suppression sort réellement le skill de la liste.
        assert call("DELETE", f"{base}/revue-stricte")[0] == 200
        assert call("DELETE", "/api/skills/global/commun")[0] == 200
        assert call("GET", base)[1] == []
        assert call("GET", "/api/skills/global")[1] == []

        # Un skill absent se distingue d'une demande invalide.
        assert call("GET", f"{base}/revue-stricte")[0] == 404
        assert call("DELETE", "/api/skills/global/commun")[0] == 404
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_skills_can_be_created_or_imported_at_both_scopes(tmp_path, monkeypatch):
    from joe import skills

    global_root = tmp_path / "global-skills"
    monkeypatch.setattr(skills, "global_skills_root", lambda: global_root)
    source = tmp_path / "shared-source"
    source.mkdir()
    (source / "SKILL.md").write_text("Règle commune importée.", encoding="utf-8")
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
        # L'action locale suit le cycle de vie asynchrone commun à tout run.
        deadline = time.monotonic() + 10
        with run.condition:
            while not run.done and time.monotonic() < deadline:
                run.condition.wait(timeout=0.1)
        assert run.done is True
        assert run.events[0]["primary"] == "joe"
        skill = tmp_path / ".agentflow/skills/test/SKILL.md"
        assert skill.exists()
        assert "SKILL_TEST_ACTIF" in skill.read_text(encoding="utf-8")
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
        assert any(
            item["id"] == "fixture" and item["label"] == "Fixture"
            for item in status["provider_catalog"]
        )

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
            "ai_access": "auto",
        },
    )
    conversation = manager.conversations.create(project["id"])

    root, additional, remote, ai_access = manager._project_scope(
        conversation["id"]
    )

    assert root == workspace.resolve()
    assert additional == (extra.resolve(),)
    assert remote is True
    assert ai_access == "auto"

    manager.conversations.update(
        conversation["id"],
        {"settings": {"web_access": "off"}},
    )
    assert manager._project_scope(conversation["id"])[2] is False


def test_project_access_level_drives_the_execution_mode():
    """Le niveau du projet décide seul : l'intention ne s'en mêle plus."""
    for intent in (Intent.MODIFY, Intent.ANALYZE, Intent.ANSWER):
        route = Route(intent, Mode.FAST, "codex")
        assert _resolve_execution_mode(None, "read_only", route) == "read-only"
        assert _resolve_execution_mode(None, "manual", route) == "workspace-write"
        assert _resolve_execution_mode(None, "auto", route) == "workspace-write"
        # Un choix explicite de conversation prime toujours.
        assert _resolve_execution_mode("read-only", "auto", route) == "read-only"


def test_full_access_approval_is_required_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    manager = RunManager(tmp_path)
    manager.conversations.update_project(
        "main",
        {"ai_access": "manual"},
    )
    conversation = manager.conversations.create("main")

    # En accès manuel, l'approbation ne dépend plus de l'intention devinée :
    # toute demande qui pourra exécuter quelque chose passe par la carte.
    for request in (
        "Implémente et teste cette fonctionnalité",
        "Explique cette fonctionnalité",
    ):
        decision = manager.decide(request, conversation["id"], "claude", "fast")
        assert decision.needs_approval, request


def test_consensus_remains_read_only_despite_project_default():
    route = Route(Intent.MODIFY, Mode.CONSENSUS, "codex")

    assert _resolve_execution_mode(None, "auto", route) is None


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


def test_read_only_runs_are_told_no_approval_channel_exists():
    """Joe ne peut pas transmettre d'approbation en cours de run : le dire."""
    from joe.web_runs import _permission_context

    read_only = _permission_context(None)
    assert "cannot relay an interactive permission request" in read_only
    assert "Never ask the user to approve a command" in read_only
    assert "operational validation permission" in read_only

    writable = _permission_context("workspace-write")
    assert "Execute the necessary commands directly" in writable
    assert "Ne demande jamais" not in writable


def _grants_commands(name: str, argv: list[str]) -> bool:
    """Le fournisseur peut-il exécuter une commande avec cet argv ?"""
    if name == "codex":
        return argv[argv.index("--sandbox") + 1] != "read-only"
    if name == "claude":
        mode = argv[argv.index("--permission-mode") + 1]
        return mode == "bypassPermissions" or "Bash" in argv
    if name == "gemini":
        return argv[argv.index("--approval-mode") + 1] == "yolo"
    return "--allow-tool=shell" in argv


def test_three_access_levels_are_provider_independent():
    """Le niveau du projet décide, quel que soit le fournisseur."""
    from pathlib import Path

    from joe.providers import Provider
    from joe.web_runs import RunDecision, _resolve_execution_mode

    attendu = {
        "read_only": {"codex": "read-only", "claude": "plan", "gemini": "plan"},
        "manual": {
            "codex": "workspace-write", "claude": "acceptEdits", "gemini": "yolo"
        },
        "auto": {
            "codex": "workspace-write", "claude": "acceptEdits", "gemini": "yolo"
        },
    }
    for level, par_fournisseur in attendu.items():
        for intent in (Intent.ANALYZE, Intent.MODIFY, Intent.ANSWER):
            route = Route(intent, Mode.FAST, "claude")
            mode = _resolve_execution_mode(None, level, route)
            for name, expected in par_fournisseur.items():
                argv = Provider(name, name).command(
                    "p", Path("/tmp"), intent, execution_mode=mode
                )
                assert expected in argv, (level, name, intent)
                # Un accès en écriture doit pouvoir lancer une commande, sinon
                # « lance les tests » revient « Refusé par le sandbox ».
                if level != "read_only":
                    assert _grants_commands(name, argv), (level, name, intent)
            decision = RunDecision(route=route, execution_mode=mode, ai_access=level)
            assert decision.needs_approval is (level == "manual")


def test_run_decision_defaults_to_unattended_project_access():
    route = Route(Intent.MODIFY, Mode.FAST, "claude")
    decision = RunDecision(route=route, execution_mode="workspace-write")

    assert decision.ai_access == "auto"
    assert decision.needs_approval is False


def test_a_local_action_never_asks_for_approval():
    from joe.web_runs import RunDecision

    route = Route(Intent.MODIFY, Mode.FAST, "joe")
    decision = RunDecision(
        route=route,
        execution_mode="workspace-write",
        ai_access="manual",
        local_action="create_skill",
    )
    assert decision.needs_approval is False


def test_plan_run_stays_read_only_whatever_the_project_level():
    """Rédiger un plan n'exige aucun droit : le run n'exécute rien."""
    from joe.web_runs import RunDecision

    route = Route(Intent.MODIFY, Mode.FAST, "claude")
    for level in ("read_only", "manual", "auto"):
        decision = RunDecision(
            route=route,
            execution_mode="read-only",
            ai_access=level,
            plan_stage="propose",
        )
        assert decision.will_execute is False, level
        # Rien ne s'exécute : aucune approbation en amont, même en manuel.
        assert decision.needs_approval is False, level


def test_a_finished_plan_run_produces_a_durable_approval(tmp_path):
    manager = RunManager(tmp_path)
    manager.conversations.update_project("main", {"ai_access": "manual"})
    conversation = manager.conversations.create("main")
    run = LiveRun("plan-run", "Ajoute une option de purge", conversation["id"])

    manager._propose_plan(run, "1. Lire le loader\n2. Ajouter le drapeau")

    pending = manager.approvals.list(status="pending")
    assert len(pending) == 1
    approval = pending[0]
    assert approval["kind"] == "plan"
    assert approval["payload"]["plan"].startswith("1. Lire le loader")
    assert approval["payload"]["request"] == "Ajoute une option de purge"
    # Il survit à un rechargement du store.
    reloaded = RunManager(tmp_path).approvals.list(status="pending")
    assert reloaded[0]["id"] == approval["id"]


def test_prompt_label_shortens_the_stored_user_message(tmp_path, monkeypatch):
    """Un plan validé ne doit pas réapparaître en entier comme mon message.

    Le modèle reçoit tout le `request` ; l'historique n'affiche que l'intitulé
    court fourni via `prompt_label`.
    """
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    monkeypatch.setattr(RunManager, "_execute", lambda self, *args: None)
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()
    full_request = "Demande initiale\n\n# Plan validé\n1. étape\n2. étape"

    run = manager.start(
        full_request,
        conversation["id"],
        "codex",
        "fast",
        None,
        None,
        None,
        prompt_label="Implémenter le plan validé ci-dessus.",
    )

    messages = manager.conversations.get(conversation["id"])["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Implémenter le plan validé ci-dessus."
    # Le run — donc le modèle — garde bien le contenu complet.
    assert run.request == full_request


def test_a_run_without_prompt_label_stores_the_full_request(tmp_path, monkeypatch):
    monkeypatch.setattr(RunManager, "_recover_pending", lambda self: None)
    monkeypatch.setattr(RunManager, "_execute", lambda self, *args: None)
    manager = RunManager(tmp_path)
    conversation = manager.conversations.create()

    manager.start(
        "Corrige le bug de tri",
        conversation["id"],
        "codex",
        "fast",
        None,
        None,
        None,
    )

    messages = manager.conversations.get(conversation["id"])["messages"]
    assert messages[0]["content"] == "Corrige le bug de tri"


def test_run_language_instruction_covers_all_workflow_stages():
    english = response_language("en")
    french = response_language("fr")

    assert "Answer the user in English" in english
    assert "plans, reviews, consensus stages" in english
    assert "Réponds à l'utilisateur en français" in french
    assert "étapes de consensus" in french


@pytest.mark.parametrize(
    ("prompt_text", "interface", "expected"),
    [
        ("Parle-moi de LimiX 2, le nouveau modèle tabulaire", "en", "fr"),
        ("Tell me about LimiX 2, the new tabular model", "fr", "en"),
        ("pytest -q", "en", "en"),
        ("Réponds en anglais : parle-moi de ce modèle", "fr", "en"),
        ("Answer in French: explain this model", "en", "fr"),
    ],
)
def test_the_request_language_is_independent_from_the_interface(
    prompt_text, interface, expected
):
    assert request_language(prompt_text, interface) == expected


def test_web_sends_the_request_language_to_the_run(tmp_path, monkeypatch):
    server, thread = start_server(tmp_path)
    conversation = server.manager.conversations.create("main")
    captured = {}
    decision = RunDecision(
        route=Route(Intent.ANSWER, Mode.FAST, "codex"),
        execution_mode="read-only",
    )
    monkeypatch.setattr(server.manager, "decide", lambda *args, **kwargs: decision)
    monkeypatch.setattr(
        server.manager,
        "start",
        lambda *args, **kwargs: captured.update(kwargs)
        or SimpleNamespace(run_id="language-test"),
    )
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/api/runs",
            body=json.dumps(
                {
                    "request": "Parle-moi du nouveau modèle tabulaire",
                    "conversation_id": conversation["id"],
                    "language": "en",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 202
        assert captured["language"] == "fr"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_the_server_answers_on_both_loopback_families(tmp_path):
    """`localhost` se résout tantôt en IPv4, tantôt en IPv6.

    Une socket ne sert qu'une famille : la moitié des accès restait sans
    réponse, et le navigateur tournait indéfiniment au lieu d'échouer.
    """
    import socket

    from joe.web_server import _loopback_companion

    server = JoeServer(("127.0.0.1", 0), Handler)
    # `serve()` installe authentification et gestionnaire avant de dupliquer
    # l'écoute : la doublure doit les recevoir tels quels.
    server.auth = object()
    server.manager = object()
    try:
        companion = _loopback_companion(server, "127.0.0.1", 0)
        if companion is None:
            pytest.skip("Pas de pile IPv6 sur cette machine")
        try:
            assert companion.address_family == socket.AF_INET6
            # Les deux écoutes partagent le même état : même authentification,
            # même historique, quel que soit le chemin emprunté.
            assert companion.auth is server.auth
            assert companion.manager is server.manager
        finally:
            companion.server_close()
    finally:
        server.server_close()


def test_every_agent_answer_is_rendered_through_the_question_aware_path():
    """Les boutons s'affichaient, puis le JSON brut prenait leur place.

    Un seul chemin de rendu connaissait le bloc `joe:question`. Celui de
    l'historique, qui repasse sur la même bulle une seconde plus tard, en
    refaisait un bloc de code. Le contenu d'un message d'agent se rend donc
    toujours par `renderAnswer`.
    """
    import re

    assets = Path(__file__).resolve().parent.parent / "src" / "joe" / "web_assets"
    renders = []
    for path in sorted(assets.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"(render\w+)\(\s*[\w.?]+\s*,\s*(?:message|completed)\.content\s*\)",
            source,
        ):
            renders.append((path.name, match.group(1)))

    assert renders, "le rendu des messages d'agent doit être visible dans les assets"
    assert all(name == "renderAnswer" for _, name in renders), renders


def test_the_providers_panel_is_reachable_and_writes_the_choice(tmp_path, monkeypatch):
    """Le choix des CLI doit rester accessible, pas mourir avec l'accueil.

    L'écran de bienvenue se marquait vu dans le navigateur et ne revenait
    jamais : on ne pouvait plus ni voir ce qui manquait, ni en ajouter.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    server, thread = start_server(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)

        body = json.dumps({"disabled": ["copilot"]})
        connection.request(
            "PATCH",
            "/api/providers",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["disabled"] == ["copilot"]

        connection.request("GET", "/api/doctor")
        response = connection.getresponse()
        report = json.loads(response.read())
        copilot = next(
            item for item in report["providers"] if item["provider"] == "copilot"
        )
        assert copilot["enabled"] is False
        assert copilot["install"]["command"]
        assert copilot["label"] == "Copilot"

        # Un nom inconnu n'entre pas dans le fichier relu à chaque démarrage.
        connection.request(
            "PATCH",
            "/api/providers",
            body=json.dumps({"disabled": ["chatgpt"]}),
            headers={"Content-Type": "application/json"},
        )
        assert connection.getresponse().status == 400
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_the_interface_offers_one_permanent_place_to_manage_cli():
    """Le panneau et l'accueil doivent rendre la même liste.

    Deux rendus séparés divergent, et c'est le permanent qui compte : on
    installe une CLI des semaines après avoir découvert Joe.
    """
    assets = Path(__file__).resolve().parent.parent / "src" / "joe" / "web_assets"
    page = (assets / "index.html").read_text(encoding="utf-8")
    app = (assets / "app.js").read_text(encoding="utf-8")

    # Les CLI sont un onglet des paramètres, pas une fenêtre de plus : deux
    # boutons ouvraient chacun leur dialogue, sans retour possible.
    assert 'data-pane="providers"' in page
    assert 'data-i18n="tab_providers"' in page
    assert 'id="providers-dialog"' not in page
    # Un seul rendu, appelé par les deux écrans.
    assert app.count("function renderProviders(") == 1
    assert 'fetchDoctor($("providers-list")' in app
    assert 'fetchDoctor($("doctor-status")' in app


def test_a_settings_tab_that_opens_a_window_offers_a_way_back():
    """Ouvrir une seconde fenêtre sans retour laisse dans une impasse."""
    assets = Path(__file__).resolve().parent.parent / "src" / "joe" / "web_assets"
    page = (assets / "index.html").read_text(encoding="utf-8")
    app = (assets / "app.js").read_text(encoding="utf-8")

    assert 'id="back-to-settings"' in page
    assert 'openSettings("project")' in app


def test_the_cli_list_refreshes_when_the_window_regains_focus():
    """On installe une CLI dans un terminal, la fenêtre ouverte.

    Demander en plus de cliquer « actualiser » fait reposer sur l'utilisateur
    une étape que la fenêtre peut faire seule.
    """
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    focus = app.split('window.addEventListener("focus"')[1][:400]
    assert 'fetchDoctor($("providers-list")' in focus
    assert '$("preferences-dialog").open' in focus


def test_the_agent_menu_never_offers_a_cli_joe_cannot_run():
    """Le panneau disait « non détectée » pendant que le menu la proposait.

    Deux affirmations contraires sur le même écran, et un choix qui ne pouvait
    pas aboutir. Le menu lit maintenant la même détection que le panneau.
    """
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'item.available === false' in app
    assert 'item.disabled = item.id !== selected' in app
    # Le menu déroulant maison doit honorer l'état, sinon le clic passe quand même.
    assert "item.disabled = option.disabled;" in app
    assert "option.disabled = Boolean(item.disabled);" in app


def test_an_empty_conversation_keeps_explaining_what_to_do():
    """Deux écrans vides coexistaient, et le second écrasait le premier.

    La seule phrase qui explique quoi faire disparaissait une fraction de
    seconde après l'ouverture — au moment précis où elle sert.
    """
    assets = Path(__file__).resolve().parent.parent / "src" / "joe" / "web_assets"
    page = (assets / "index.html").read_text(encoding="utf-8")
    conversations = (assets / "app_conversations.js").read_text(encoding="utf-8")

    assert 'data-i18n="empty_title"' in page and 'data-i18n="empty_text"' in page
    assert 'tr("empty_title")' in conversations
    assert 'tr("empty_text")' in conversations


def test_the_panel_explains_that_a_detected_cli_still_needs_an_account():
    """Cursor apparaissait utilisable alors qu'aucun compte n'y etait connecte."""
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    assert "provider.installed && install.sign_in" in app
    assert 'provider.auth_failed ? "provider_sign_in_failed" : "provider_sign_in_once"' in app


def test_a_review_no_longer_forces_the_flagship_model_on_every_stage():
    """Tout ce qui n'était pas FAST héritait du modèle phare et d'un effort max.

    Une relecture ou un consensus prenait donc le plus gros modèle pour chacune
    de ses étapes, y compris les revues croisées qui n'en ont pas besoin.
    """
    from joe.web_runs import _route_tier

    simple_review = Route(Intent.MODIFY, Mode.REVIEW, "codex")
    assert _route_tier("Rename this variable", simple_review) == ("standard", "medium")

    heavy_review = Route(Intent.MODIFY, Mode.REVIEW, "codex")
    assert _route_tier("Refactor the migration", heavy_review) == ("strong", "high")

    # Un consensus est réservé aux décisions conséquentes : il garde le haut.
    consensus = Route(Intent.ANALYZE, Mode.CONSENSUS, "codex")
    assert _route_tier("Choose between these two designs", consensus) == (
        "strong",
        "high",
    )

    question = Route(Intent.ANSWER, Mode.FAST, "codex")
    assert _route_tier("What does this function return?", question) == ("light", "low")


def test_the_heavy_markers_work_in_both_languages():
    """Ils n'existaient qu'en français.

    « implement the migration » ne déclenchait rien, « implémente la migration »
    oui : deux utilisateurs, le même besoin, deux routages.
    """
    from joe.web_runs import _heavy_words

    for request in ("implémente la migration", "implement the migration"):
        assert _heavy_words(request) is True
    for request in ("audit de sécurité", "security audit"):
        assert _heavy_words(request) is True
    assert _heavy_words("comment ça marche ?") is False
    assert _heavy_words("how does this work?") is False


def test_a_periodic_refresh_never_resets_the_chosen_agent():
    """Le statut est relu toutes les cinq secondes.

    Reconstruire le menu a chaque fois remettait la selection a zero puis la
    restaurait — mais le menu visible, lui, restait sur « Automatique ».
    """
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    menu = app.split("function updateProviderMenu(")[1].split("\n}")[0]
    # On ne reconstruit que si le catalogue ou la langue ont change.
    assert "state.providerCatalogSignature" in menu
    # Et la valeur restauree doit atteindre le menu visible.
    assert menu.index('$("agent").value = selected') < menu.index(
        'refreshSelectMenu($("agent"))'
    )


def test_loading_capabilities_late_does_not_erase_the_chosen_model():
    """Les capacites sont chargees en parallele de la conversation.

    Elles arrivaient souvent apres elle et remettaient le modele sur
    « defaut du fournisseur », quel que soit le choix enregistre.
    """
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    menus = app.split("function updateCapabilityMenus(")[1].split("\n}")[0]
    assert 'const previous = $("model").value' in menus
    assert '$("model").value = previous' in menus


def test_a_session_command_explains_itself_instead_of_answering_nothing():
    """`/compact` ne renvoie ni reponse ni erreur : juste le vide.

    Joe lance les CLI en un seul appel non interactif, donc une commande qui
    pilote une session n'a aucune session sur laquelle agir. Une bulle vide
    ressemble a une panne.
    """
    from joe.web_runs import _empty_answer_note, _leading_slash_command

    assert _leading_slash_command("/compact") == "compact"
    assert _leading_slash_command("  /clear now") == "clear"
    assert _leading_slash_command("explique /compact") == ""

    note = _empty_answer_note("/compact", "en")
    assert "`/compact` produced no output" in note
    assert "@file" in note and ".claude/commands/" in note

    french = _empty_answer_note("/compact", "fr")
    assert "n'a produit aucune sortie" in french

    # Une reponse vide sans commande reste expliquee, sobrement.
    plain = _empty_answer_note("Corrige le tri", "en")
    assert "empty answer" in plain
    assert "/" not in plain.split("Nothing")[0]


def test_a_custom_command_is_never_refused_on_the_strength_of_its_name():
    """Une commande personnalisee fonctionne vraiment, elle.

    Refuser `/review` ou `/init` au pretexte du nom aurait casse ce qui
    marche : rien n'est bloque en amont, on explique ce qu'on a observe.
    """
    from joe.web_runs import _empty_answer_note

    # L'explication n'apparait que si la sortie est vide ; la reconnaissance
    # du nom ne conditionne aucun blocage.
    note = _empty_answer_note("/review", "en")
    assert "produced no output" in note
    assert "does work" in note


def test_a_mentioned_project_file_is_not_sent_twice(tmp_path):
    """`@fichier` partait deux fois : la copie de Joe et l'original.

    Pire que du contexte gaspille : la copie est un instantane de son import,
    donc le modele recevait deux versions de « ce fichier » sans pouvoir dire
    laquelle est a jour.
    """
    from joe.web_runs import _without_live_duplicates

    (tmp_path / "README.md").write_text("vivant", encoding="utf-8")
    piece = {"name": "README.md", "path": str(tmp_path / "copie.md")}

    # Mentionne et present dans le projet : la CLI lira l'original.
    assert _without_live_duplicates([piece], "resume @README.md", tmp_path) == []

    # Mentionne mais absent du projet : la copie de Joe est la seule source.
    ailleurs = {"name": "rapport.pdf", "path": str(tmp_path / "x.pdf")}
    assert _without_live_duplicates([ailleurs], "lis @rapport.pdf", tmp_path) == [
        ailleurs
    ]

    # Choisi au menu, sans mention : conserve meme homonyme.
    assert _without_live_duplicates([piece], "resume le projet", tmp_path) == [piece]


def test_the_welcome_screen_cannot_be_dismissed_before_the_diagnostic_ends():
    """C'est la seule page qui dise quelles CLI manquent et comment les poser.

    Elle ne revient pas d'elle-meme : la fermer trop tot fait manquer
    l'information au moment precis ou elle sert.
    """
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    body = app.split("async function loadDoctor()")[1].split("\n}")[0]
    assert "start.disabled = true" in body
    # La touche Echap ferme un `<dialog>` par defaut : il faut la retenir aussi.
    assert 'dialog.addEventListener("cancel", holdEscape)' in body
    # Et tout doit etre rendu meme si le diagnostic echoue.
    assert "finally" in body
    assert body.index("finally") < body.index("start.disabled = false")
    assert 'dialog.removeEventListener("cancel", holdEscape)' in body


def test_a_missing_cli_is_guided_step_by_step():
    """Trois lignes libres laissaient deviner l'ordre des gestes."""
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'steps.className = "provider-steps"' in app
    for key in (
        "provider_step_install",
        "provider_step_sign_in",
        "provider_step_recheck",
        "provider_prerequisite",
    ):
        assert f't("{key}")' in app, key
    # Le role de chaque fournisseur aide a choisir laquelle installer.
    assert "provider_purpose_" in app


def test_the_agent_menu_follows_a_language_change():
    """« Automatique » restait dans la langue precedente.

    Cette option est construite en JavaScript : elle ne porte pas de balise
    `data-i18n`, donc la traduction du document ne l'atteignait pas, et rien ne
    reconstruisait ce menu au changement de langue.
    """
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    corps = app.split("function applyLanguage(")[1].split("\n}")[0]
    assert "updateProviderMenu(" in corps
    # Et la reconstruction doit preceder le repeinturage des menus dessines.
    assert corps.index("updateProviderMenu(") < corps.index("refreshSelectMenu(select)")


def test_a_retired_cli_says_so_where_it_is_chosen():
    """Gemini reste installee et detectee mais ne sert plus le grand public."""
    app = (
        Path(__file__).resolve().parent.parent
        / "src" / "joe" / "web_assets" / "app.js"
    ).read_text(encoding="utf-8")

    assert "provider.deprecated" in app
    assert 't("provider_retired"' in app


def test_compaction_never_targets_a_retired_cli_by_default():
    """Le defaut livre nommait Gemini CLI, retiree des comptes grand public.

    La compaction se declenche seule, dans un thread de fond, sur les longues
    conversations : elle echouait donc a chaque fois, et l'erreur remontait au
    milieu d'un run sans rapport — le bloc n'avait pas d'`except`.
    """
    from joe.memory import DEFAULT_CONFIG
    from joe.web_runs import _compaction_provider

    reglage = DEFAULT_CONFIG["semantic_compaction"]
    assert reglage["provider"] == ""
    assert reglage["model"] == ""

    installes = {"codex": 1, "claude": 1, "antigravity": 1, "gemini": 1}
    # Sans consigne, Joe prend un fournisseur en service, jamais le deprecie.
    assert _compaction_provider("", installes) == "codex"

    # Un choix explicite est respecte : une licence entreprise fait encore
    # repondre Gemini.
    assert _compaction_provider("gemini", installes) == "gemini"

    # Choisi mais absent : son successeur declare prend le relais, plutot que
    # le `KeyError` que produisait l'acces direct au dictionnaire.
    assert _compaction_provider("gemini", {"claude": 1, "antigravity": 1}) == (
        "antigravity"
    )

    # Faute de mieux, une CLI depreciee vaut mieux que pas de compaction.
    assert _compaction_provider("", {"gemini": 1}) == "gemini"

    # Aucun fournisseur : on renonce, sans lever.
    assert _compaction_provider("", {}) == ""


def test_compaction_filters_declared_but_unavailable_providers(monkeypatch):
    import joe.web_runs as web_runs_module
    from joe.providers import Provider
    from joe.web_runs import _installed_compaction_providers

    providers = {"codex": Provider("codex", "codex")}
    monkeypatch.setattr(web_runs_module, "disabled_providers", lambda: set())
    monkeypatch.setattr(web_runs_module, "resolve_executable", lambda _name: None)
    assert _installed_compaction_providers(providers) == {}

    monkeypatch.setattr(
        web_runs_module, "resolve_executable", lambda _name: "/usr/bin/codex"
    )
    assert _installed_compaction_providers(providers) == providers

    monkeypatch.setattr(web_runs_module, "disabled_providers", lambda: {"codex"})
    assert _installed_compaction_providers(providers) == {}
