from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__
from .auth import LocalAuth, auth_token_path, rotate_token
from .autonomous_builder import parse_autonomous_request
from .conversations import FREE_PROJECT_ID
from .doctor import doctor_report
from .files import MAX_FILE_BYTES
from .models import Mode
from .http_utils import RequestBodyError, read_json_body, validate_bind
from .provider_registry import get_provider_catalog, get_provider_names
from .providers import NETWORK_CONTROLLED_PROVIDERS
from .routes import Route, resolve
from .skills import (
    create_skill,
    import_skill,
    list_global_skills,
    list_skills,
    parse_skill_request,
    promote_skill,
)
from .web_runs import (
    ActiveConversationError,
    RunManager,
    _existing_directory,
    _web_facade,
)
from .worktrees import WorktreeError

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
    "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
    "/app_auth.js": ("app_auth.js", "text/javascript; charset=utf-8"),
    "/app_usage.js": ("app_usage.js", "text/javascript; charset=utf-8"),
    "/app_conversations.js": ("app_conversations.js", "text/javascript; charset=utf-8"),
    "/app_automation.js": ("app_automation.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
API_VERSION = "1.3"

# Champs qu'un profil viewer peut écrire sur une conversation : acquitter un
# état lu ne constitue pas une mutation de contenu.
ACQUITTAL_FIELDS = {"unread_completion"}


class JoeServer(ThreadingHTTPServer):
    manager: RunManager
    auth = LocalAuth("", "viewer", True)
    auth_path = auth_token_path()


class Handler(BaseHTTPRequestHandler):
    server: JoeServer
    route: Route
    query: dict[str, list[str]]

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        """Resolve the route, authorize from its declared role, then serve it.

        Les motifs étant ancrés et exclusifs, une route non déclarée échoue en
        404 franc au lieu d'être détournée vers un préfixe fourre-tout.
        """
        parsed = urlparse(self.path)
        if method == "GET" and parsed.path in _ASSETS:
            name, content_type = _ASSETS[parsed.path]
            return self._asset(name, content_type)
        match = resolve(method, parsed.path)
        if match is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.route, parameters = match
        self.query = parse_qs(parsed.query)
        if self.route.role and not self._authorize(self._route_role()):
            return
        handler = getattr(self, self.route.handler)
        handler(**{key: unquote(value) for key, value in parameters.items()})

    def _route_role(self) -> str:
        """Role for this request, including any elevation carried by the query."""
        elevation = self.route.query_role
        if elevation and self.query.get(elevation[0]) == [elevation[1]]:
            return elevation[2]
        return self.route.role

    def _param(self, name: str, default: str | None = None) -> str | None:
        return self.query.get(name, [default])[0]

    # --- Lecture ---------------------------------------------------------

    def _get_status(self) -> None:
        self._json(
            {
                "version": __version__,
                "api_version": API_VERSION,
                "project": str(self.server.manager.project),
                "conversation_store": str(self.server.manager.conversations.path),
                "conversation_backup": str(
                    self.server.manager.conversations.backup_path
                ),
                "providers": get_provider_names(),
                "provider_catalog": get_provider_catalog(),
                "modes": [item.value for item in Mode],
                "auth_required": self.server.auth.enabled,
                "profile": self.server.auth.role,
                "network_control_providers": list(NETWORK_CONTROLLED_PROVIDERS),
            }
        )

    def _get_capabilities(self) -> None:
        self._json(_web_facade().provider_capabilities())

    def _get_doctor(self) -> None:
        self._json(doctor_report(self.server.manager.project))

    def _get_files(self) -> None:
        self._json(self.server.manager.files.list(self._param("project", "free")))

    def _get_file_download(self, item_id: str) -> None:
        item = self.server.manager.files.get(item_id, self._param("project", "free"))
        if not item:
            return self.send_error(HTTPStatus.NOT_FOUND)
        self._file(item)

    def _get_usage(self) -> None:
        force = self.query.get("force") == ["1"]
        self._json(_web_facade().usage_status(force=force))

    def _get_conversations(self) -> None:
        self._json(self.server.manager.conversations.list_summaries())

    def _get_conversation(self, conversation_id: str) -> None:
        item = self.server.manager.conversations.get(conversation_id)
        self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

    def _get_search(self) -> None:
        self._json(
            self.server.manager.search(
                self._param("q", "") or "",
                self._param("project"),
            )
        )

    def _get_analytics(self) -> None:
        self._json(
            self.server.manager.conversations.analytics(self._param("project"))
        )

    def _get_preferences(self) -> None:
        self._json(self.server.manager.conversations.preferences())

    def _get_active_runs(self) -> None:
        self._json(
            [
                {
                    "run_id": run.run_id,
                    "conversation_id": run.conversation_id,
                    "request": run.request,
                }
                for run in self.server.manager.active_runs()
            ]
        )

    def _get_tasks(self) -> None:
        self._json(self.server.manager.list_tasks())

    def _get_task_diff(self, task_id: str) -> None:
        try:
            report = self.server.manager.task_diff(task_id)
        except WorktreeError as error:
            return self._json({"error": str(error)}, HTTPStatus.CONFLICT)
        self._json(
            report,
            HTTPStatus.OK if report is not None else HTTPStatus.NOT_FOUND,
        )

    def _get_automations(self) -> None:
        self._json(self.server.manager.list_automations())

    def _get_autonomous(self) -> None:
        self._json(self.server.manager.list_autonomous())

    def _get_approvals(self) -> None:
        self._json(self.server.manager.approvals.list(status="pending"))

    def _get_projects(self) -> None:
        self._json(self.server.manager.conversations.list_projects())

    def _get_project(self, project_id: str) -> None:
        item = self.server.manager.conversations.get_project(project_id)
        self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

    def _get_project_skills(self, project_id: str) -> None:
        workspace = self._project_workspace(project_id)
        if workspace is None:
            return self._json({}, HTTPStatus.NOT_FOUND)
        self._json(list_skills(workspace))

    def _get_global_skills(self) -> None:
        self._json(list_global_skills())

    def _get_history(self) -> None:
        self._json(self.server.manager.history())

    def _get_history_item(self, run_id: str) -> None:
        item = self.server.manager.history_item(run_id)
        self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

    def _get_events(self, run_id: str) -> None:
        self._events(
            run_id,
            _event_cursor(
                self.headers.get("Last-Event-ID"),
                self._param("after", "0") or "0",
            ),
        )

    # --- Écriture --------------------------------------------------------

    def _post_pair(self) -> None:
        self._pair()

    def _post_auth_rotate(self) -> None:
        token = rotate_token(self.server.auth_path)
        self.server.auth = LocalAuth(token, self.server.auth.role, True)
        self._json({"rotated": True})

    def _post_run_cancel(self, run_id: str) -> None:
        cancelled = self.server.manager.cancel(run_id)
        self._json(
            {"cancelled": cancelled},
            HTTPStatus.ACCEPTED if cancelled else HTTPStatus.NOT_FOUND,
        )

    def _post_run_reject(self, run_id: str) -> None:
        payload = self._read_payload(allow_empty=True)
        if payload is None:
            return
        selected_files = payload.get("files")
        if selected_files is not None and (
            not isinstance(selected_files, list)
            or not all(isinstance(item, str) for item in selected_files)
        ):
            return self._json(
                {"restored": False, "message": "Sélection invalide."},
                HTTPStatus.BAD_REQUEST,
            )
        restored, message = self.server.manager.reject_changes(
            run_id,
            selected_files,
        )
        self._json(
            {"restored": restored, "message": message},
            HTTPStatus.OK if restored else HTTPStatus.CONFLICT,
        )

    def _post_task_integrate(self, task_id: str) -> None:
        try:
            task = self.server.manager.integrate_task(task_id)
        except KeyError:
            return self._json({}, HTTPStatus.NOT_FOUND)
        except WorktreeError as error:
            return self._json({"error": str(error)}, HTTPStatus.CONFLICT)
        self._json(task, HTTPStatus.ACCEPTED)

    def _post_automations(self) -> None:
        payload = self._read_payload()
        if payload is None:
            return
        try:
            plan = self.server.manager.create_automation(payload)
        except (TypeError, ValueError) as error:
            return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        self._json(plan, HTTPStatus.CREATED)

    def _post_autonomous(self) -> None:
        payload = self._read_payload()
        if payload is None:
            return
        try:
            campaign = self.server.manager.create_autonomous(payload)
        except (TypeError, ValueError) as error:
            return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        self._json(campaign, HTTPStatus.CREATED)

    def _post_autonomous_cancel(self, campaign_id: str) -> None:
        campaign = self.server.manager.cancel_autonomous(campaign_id)
        self._json(
            campaign or {}, HTTPStatus.ACCEPTED if campaign else HTTPStatus.NOT_FOUND,
        )

    def _post_autonomous_resume(self, campaign_id: str) -> None:
        try:
            campaign = self.server.manager.resume_autonomous(campaign_id)
        except ValueError as error:
            return self._json({"error": str(error)}, HTTPStatus.CONFLICT)
        self._json(
            campaign or {}, HTTPStatus.ACCEPTED if campaign else HTTPStatus.NOT_FOUND,
        )

    def _post_autonomous_handoff(self, campaign_id: str) -> None:
        try:
            campaign = self.server.manager.handoff_autonomous(campaign_id)
        except ValueError as error:
            return self._json({"error": str(error)}, HTTPStatus.CONFLICT)
        self._json(
            campaign or {}, HTTPStatus.ACCEPTED if campaign else HTTPStatus.NOT_FOUND,
        )

    def _delete_autonomous(self, campaign_id: str) -> None:
        deleted = self.server.manager.delete_autonomous(campaign_id)
        self._json(
            {"deleted": deleted}, HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND,
        )

    def _post_automation_cancel(self, plan_id: str) -> None:
        plan = self.server.manager.cancel_automation(plan_id)
        self._json(
            plan or {},
            HTTPStatus.ACCEPTED if plan else HTTPStatus.NOT_FOUND,
        )

    def _post_conversations(self) -> None:
        payload = self._read_payload(allow_empty=True)
        if payload is None:
            return
        self._json(
            self.server.manager.conversations.create(payload.get("project_id")),
            HTTPStatus.CREATED,
        )

    def _post_files(self) -> None:
        payload = self._read_payload(max_bytes=MAX_FILE_BYTES * 2)
        if payload is None:
            return
        project_id = str(payload.get("project_id", "free"))
        if not self.server.manager.conversations.get_project(project_id):
            return self._json({"error": "Projet inconnu."}, HTTPStatus.BAD_REQUEST)
        try:
            item = self.server.manager.files.add(
                project_id,
                str(payload.get("name", "file")),
                str(payload.get("content_type", "")),
                str(payload.get("data", "")),
            )
        except (OSError, ValueError) as error:
            return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        self._json(item, HTTPStatus.CREATED)

    def _post_projects(self) -> None:
        payload = self._read_payload(allow_empty=True)
        if payload is None:
            return
        item = self.server.manager.conversations.create_project(
            str(payload.get("name", "Nouveau projet"))
        )
        changes = {key: value for key, value in payload.items() if key != "name"}
        if changes:
            item = self.server.manager.conversations.update_project(item["id"], changes) or item
        self._json(item, HTTPStatus.CREATED)

    def _post_global_skill_create(self) -> None:
        payload = self._read_payload()
        if payload is None:
            return
        self._skill_result(
            create_skill,
            None,
            str(payload.get("name", "")),
            str(payload.get("instructions", "")),
            global_scope=True,
        )

    def _post_global_skill_import(self) -> None:
        payload = self._read_payload()
        if payload is None:
            return
        self._skill_result(
            import_skill,
            None,
            Path(str(payload.get("source", ""))),
            name=str(payload.get("name", "")).strip() or None,
            global_scope=True,
        )

    def _post_project_skill_create(self, project_id: str) -> None:
        workspace = self._project_workspace(project_id)
        if workspace is None:
            return self._json({}, HTTPStatus.NOT_FOUND)
        payload = self._read_payload()
        if payload is None:
            return
        self._skill_result(
            create_skill,
            workspace,
            str(payload.get("name", "")),
            str(payload.get("instructions", "")),
        )

    def _post_project_skill_import(self, project_id: str) -> None:
        workspace = self._project_workspace(project_id)
        if workspace is None:
            return self._json({}, HTTPStatus.NOT_FOUND)
        payload = self._read_payload(allow_empty=True)
        if payload is None:
            return
        self._skill_result(
            import_skill,
            workspace,
            Path(str(payload.get("source", ""))),
            name=str(payload.get("name", "")).strip() or None,
            source_provider=str(payload.get("provider", "unknown")),
        )

    def _post_project_skill_promote(self, project_id: str) -> None:
        workspace = self._project_workspace(project_id)
        if workspace is None:
            return self._json({}, HTTPStatus.NOT_FOUND)
        payload = self._read_payload(allow_empty=True)
        if payload is None:
            return
        self._skill_result(
            promote_skill,
            workspace,
            str(payload.get("name", "")).strip(),
        )

    # --- Mise à jour -----------------------------------------------------

    def _patch_preferences(self) -> None:
        payload = self._read_payload()
        if payload is None:
            return
        self._json(self.server.manager.conversations.update_preferences(payload))

    def _patch_approval(self, approval_id: str) -> None:
        payload = self._read_payload()
        if payload is None:
            return
        try:
            item = self.server.manager.approvals.decide(
                approval_id,
                str(payload.get("decision", "")),
            )
        except ValueError as error:
            return self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        self._json(item or {}, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

    def _patch_conversation(self, conversation_id: str) -> None:
        payload = self._read_payload()
        if payload is None:
            return
        # Le rôle de base n'autorise que l'acquittement : tout autre champ
        # exige le rôle escaladé déclaré par la route.
        if not set(payload) <= ACQUITTAL_FIELDS:
            if not self._authorize(self.route.escalated_role):
                return
        item = self.server.manager.conversations.update(conversation_id, payload)
        self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

    def _patch_project(self, project_id: str) -> None:
        payload = self._read_payload()
        if payload is None:
            return
        item = self.server.manager.conversations.update_project(project_id, payload)
        self._json(item, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND)

    # --- Suppression -----------------------------------------------------

    def _delete_task(self, task_id: str) -> None:
        try:
            deleted = self.server.manager.delete_task(task_id)
        except WorktreeError as error:
            return self._json({"error": str(error)}, HTTPStatus.CONFLICT)
        self._json(
            {"deleted": deleted},
            HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND,
        )

    def _delete_file(self, item_id: str) -> None:
        deleted = self.server.manager.files.delete(
            item_id,
            self._param("project", "free"),
        )
        self._json(
            {"deleted": deleted},
            HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND,
        )

    def _delete_conversation(self, conversation_id: str) -> None:
        if self.server.manager.has_active_conversation(conversation_id):
            return self._json(
                {"error": "Interromps la tâche avant de supprimer la conversation."},
                HTTPStatus.CONFLICT,
            )
        deleted = self.server.manager.conversations.delete(conversation_id)
        self._json(
            {"deleted": deleted},
            HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND,
        )

    # --- Communs aux endpoints skills ------------------------------------

    def _project_workspace(self, project_id: str) -> Path | None:
        project = self.server.manager.conversations.get_project(project_id)
        if not project:
            return None
        return (
            _existing_directory(project.get("workspace_root"))
            or self.server.manager.project
        )

    def _skill_result(self, operation, *args, **kwargs) -> None:
        try:
            result = operation(*args, **kwargs)
        except (OSError, ValueError) as error:
            return self._json({"message": str(error)}, HTTPStatus.BAD_REQUEST)
        self._json(result, HTTPStatus.CREATED)

    def _post_runs(self) -> None:
        try:
            payload = self._read_payload()
            if payload is None:
                return
            request = str(payload.get("request", "")).strip()
            conversation_id = str(payload.get("conversation_id", "")).strip()
            if not request:
                raise ValueError("request is required")
            if not self.server.manager.conversations.get(conversation_id):
                raise ValueError("valid conversation_id is required")
            agent = payload.get("agent") or None
            mode = payload.get("mode") or None
            model = str(payload.get("model", "")).strip() or None
            effort = str(payload.get("effort", "")).strip() or None
            execution_mode = str(payload.get("execution_mode", "")).strip() or None
            attachments = payload.get("attachments") or []
            if (
                not isinstance(attachments, list)
                or len(attachments) > 24
                or not all(isinstance(item, str) for item in attachments)
            ):
                raise ValueError("invalid attachments")
            approval_id = str(payload.get("approval_id", "")).strip()
            prompt_label = str(payload.get("prompt_label", "")).strip()
            plan_stage = "propose" if payload.get("plan") is True else ""
            if agent is not None and agent not in set(get_provider_names()):
                raise ValueError("invalid agent")
            if mode not in {None, *(item.value for item in Mode)}:
                raise ValueError("invalid mode")
        except ValueError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        # La détection lexicale tranche sans appeler de modèle ; sinon on prend
        # LA décision du run, qui porte aussi la classification du routeur.
        autonomous_request = parse_autonomous_request(request)
        if autonomous_request is not None:
            if not self.server.auth.allows("maintainer"):
                return self._json(
                    {"error": "Le profil maintainer est requis pour créer Autonomous."},
                    HTTPStatus.FORBIDDEN,
                )
            try:
                run = self.server.manager.start_local_autonomous(
                    request,
                    conversation_id,
                    autonomous_request,
                )
            except (ActiveConversationError, ValueError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            return self._json({"run_id": run.run_id}, HTTPStatus.ACCEPTED)
        skill_request = parse_skill_request(request)
        decision = None
        classification = None
        if skill_request is None:
            decision = self.server.manager.decide(
                request,
                conversation_id,
                agent,
                mode,
                model,
                effort,
                execution_mode,
                plan_stage=plan_stage,
            )
            classification = decision.classification
            if (
                classification is not None
                and classification.action == "create_skill"
                and classification.confidence >= 0.85
            ):
                skill_request = {
                    "name": classification.skill_name,
                    "instructions": classification.skill_instructions,
                    "scope": classification.skill_scope,
                }
        if skill_request is not None:
            if not self.server.auth.allows("maintainer"):
                return self._json(
                    {"error": "Le profil maintainer est requis pour créer un skill."},
                    HTTPStatus.FORBIDDEN,
                )
            if not skill_request["name"] or not skill_request["instructions"]:
                return self._json(
                    {
                        "error": (
                            "Précise le nom et les instructions du skill, par "
                            "exemple : crée un skill « revue-python » qui vérifie "
                            "les tests et la lisibilité."
                        )
                    },
                    HTTPStatus.BAD_REQUEST,
                )
            try:
                run = self.server.manager.start_local_skill(
                    request,
                    conversation_id,
                    name=skill_request["name"],
                    instructions=skill_request["instructions"],
                    global_scope=skill_request["scope"] == "global",
                    classification=classification,
                )
            except ActiveConversationError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            return self._json({"run_id": run.run_id}, HTTPStatus.ACCEPTED)
        # Celle que l'on autorise ici est exactement celle qui sera exécutée :
        # le garde-fou et l'exécution ne peuvent plus diverger.
        needs_approval = decision.needs_approval
        if needs_approval and not self.server.auth.allows("maintainer"):
            return self._json(
                {"error": "Le profil maintainer est requis pour cet accès."},
                HTTPStatus.FORBIDDEN,
            )
        # Un accès complet ne peut être autorisé que par une approbation
        # durable, explicitement validée et consommée une seule fois. Aucun
        # champ du corps de la requête ne vaut autorisation.
        approved = bool(approval_id) and self.server.manager.approvals.allows(
            approval_id,
            conversation_id,
            request,
        )
        if needs_approval and not approved:
            conversation = self.server.manager.conversations.get(
                conversation_id
            ) or {}
            # La carte doit dire ce qui va tourner : sans le fournisseur, le
            # périmètre et le niveau d'accès, l'utilisateur approuve à l'aveugle.
            workspace, roots, _, _ = self.server.manager._project_scope(
                conversation_id
            )
            scope = ", ".join(str(path) for path in (workspace, *roots))
            approval = self.server.manager.approvals.create(
                "run",
                conversation_id,
                str(conversation.get("project_id", FREE_PROJECT_ID)),
                {
                    **{
                        key: value
                        for key, value in payload.items()
                        if key not in {"full_access_approved", "approval_id"}
                    },
                    "provider": decision.route.primary,
                    "model": decision.model,
                    "workflow": decision.route.mode.value,
                    "scope": scope,
                    "access": decision.execution_mode,
                },
                (
                    f"{decision.route.primary} va pouvoir exécuter des commandes "
                    f"et modifier des fichiers dans : {scope}"
                ),
            )
            return self._json(
                {
                    "error": (
                        "Ce projet est en validation manuelle. Autorise cette "
                        "demande pour la lancer."
                    ),
                    "approval": "run",
                    "approval_id": approval["id"],
                },
                HTTPStatus.PRECONDITION_REQUIRED,
            )
        try:
            run = self.server.manager.start(
                request,
                conversation_id,
                agent,
                mode,
                model,
                effort,
                execution_mode,
                attachments,
                decision=decision,
                plan_stage=plan_stage,
                prompt_label=prompt_label,
            )
        except ActiveConversationError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
        if approval_id:
            self.server.manager.approvals.consume(
                approval_id,
                conversation_id,
                request,
            )
        self._json({"run_id": run.run_id}, HTTPStatus.ACCEPTED)

    def _events(self, run_id: str, after: int = 0) -> None:
        run = self.server.manager.get_run(run_id)
        if not run:
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        index = min(after, len(run.events))
        try:
            while True:
                with run.condition:
                    if index >= len(run.events) and not run.done:
                        run.condition.wait(timeout=15)
                    events = run.events[index:]
                    index = len(run.events)
                    done = run.done and index >= len(run.events)
                if not events:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                for event in events:
                    data = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(
                        f"id: {event['event_id']}\ndata: {data}\n\n".encode()
                    )
                    self.wfile.flush()
                if done:
                    self.close_connection = True
                    return
        except (BrokenPipeError, ConnectionResetError):
            return

    def _asset(self, name: str, content_type: str) -> None:
        data = files("joe").joinpath("web_assets", name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _pair(self) -> None:
        authorization = self.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.startswith("Bearer ") else None
        if not self.server.auth.accepts(token):
            return self._json(
                {"error": "Jeton d’appairage invalide."},
                HTTPStatus.UNAUTHORIZED,
            )
        self._json(
            {"paired": True},
            headers={
                "Set-Cookie": (
                    f"joe_token={self.server.auth.token}; "
                    "HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000"
                )
            },
        )

    def _authorize(self, required: str) -> bool:
        if not self.server.auth.enabled:
            return True
        token = None
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
        if not token:
            for item in self.headers.get("Cookie", "").split(";"):
                key, separator, value = item.strip().partition("=")
                if separator and key == "joe_token":
                    token = value
                    break
        if not self.server.auth.accepts(token):
            self._json({"error": "Authentification Joe requise."}, HTTPStatus.UNAUTHORIZED)
            return False
        if not self.server.auth.allows(required):
            self._json(
                {
                    "error": (
                        f"Le profil {required} est requis. "
                        "Relance Joe avec --profile maintainer ou réduis les "
                        "permissions du projet."
                    )
                },
                HTTPStatus.FORBIDDEN,
            )
            return False
        return True

    def _read_payload(
        self,
        *,
        allow_empty: bool = False,
        max_bytes: int = 1024 * 1024,
    ) -> dict[str, Any] | None:
        try:
            return read_json_body(
                self.headers,
                self.rfile,
                allow_empty=allow_empty,
                max_bytes=max_bytes,
            )
        except RequestBodyError as exc:
            self._json({"error": str(exc)}, exc.status)
            return None

    def _file(self, item: dict[str, Any]) -> None:
        path = Path(str(item["path"]))
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        content_type = str(
            item.get("content_type")
            or mimetypes.guess_type(str(item.get("name", "")))[0]
            or "application/octet-stream"
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(str(item['name']))}",
        )
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(
    project: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    allow_remote: bool = False,
    profile: str = "maintainer",
) -> None:
    validate_bind(host, allow_remote=allow_remote)
    server = JoeServer((host, port), Handler)
    server.auth = LocalAuth.enabled_for(profile)
    server.manager = RunManager(project, profile=profile)
    server.serve_forever()




def _event_cursor(header: str | None, query: str | None) -> int:
    for value in (header, query):
        if value in {None, ""}:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0
