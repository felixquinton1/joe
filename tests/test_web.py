import http.client
import json
import threading
import time
from types import SimpleNamespace

from joe.models import Intent, Mode, Route
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
from joe.web_runs import _resolve_execution_mode


def start_server(tmp_path):
    server = JoeServer(("127.0.0.1", 0), Handler)
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
        assert payload["conversation_store"].endswith(
            ".agentflow/conversations.json"
        )
        assert "joe/backups" in payload["conversation_backup"]
        assert "codex" in payload["providers"]

        connection.request("GET", "/app.js")
        response = connection.getresponse()
        app = response.read().decode()
        assert response.status == 200
        assert "reconcileRun(conversationId, runId)" in app
        assert "window.location.reload()" in app

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
        assert "L’IA à la mode chez les jeunes".encode() in page
        assert b'id="cancel-project"' in page
        assert b'id="prompt-queue"' in page
        assert b'data-resizer="left"' in page
        assert b'data-resizer="right"' in page
        assert b'id="toggle-history"' in page
        assert b'id="toggle-activity"' in page
        assert b'src="/markdown.js"' in page
        assert b'<span class="brand-mark">J</span>' in page

        connection.request("GET", "/app.js")
        response = connection.getresponse()
        script = response.read()
        assert response.status == 200
        assert b"renderMarkdown" in script
        assert b"launchNextQueued" in script
        assert b"copyButton" in script
        assert b"moveConversation" in script
        assert b"setupPanelResizers" in script
        assert b"resizeComposer" in script
        assert b"toggleMobilePanel" in script
        assert b"loadActiveRuns" in script
        assert b"setSummaryPending" in script
        assert b"event.cli_version" not in script

        connection.request("GET", "/style.css")
        response = connection.getresponse()
        style = response.read()
        assert response.status == 200
        assert b"max-height:min(42vh,360px)" in style
        assert b".bubble,.composer textarea" in style
        assert b".activity-panel.mobile-open" in style
        assert b".message.workflow-summary-pending" in style
    finally:
        server.shutdown()
        thread.join(timeout=2)
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
    assert remote is False
    assert execution_mode == "workspace-write"


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


def test_consensus_remains_read_only_despite_project_default():
    route = Route(Intent.MODIFY, Mode.CONSENSUS, "codex")

    assert _resolve_execution_mode(
        None,
        "danger-full-access",
        route,
        "Décide de l’architecture",
    ) is None


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
