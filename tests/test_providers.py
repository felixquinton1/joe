import os
import sys
import threading
import time
from pathlib import Path

import joe.providers as providers
from joe.models import Intent
from joe.providers import (
    Provider,
    _activity,
    _final_output,
    _redact_values,
    _secret_values,
    _terminal_quota,
    _process_group_options,
    classify_error,
    provider_runtime_issue,
    windows_aware_executable,
)


def test_managed_codex_requires_native_companions(tmp_path):
    managed = tmp_path / "Programs" / "Joe" / "bin" / "codex.exe"
    managed.parent.mkdir(parents=True)
    managed.write_text("binary")

    issue = provider_runtime_issue("codex", str(managed))

    assert "codex-code-mode-host.exe" in issue
    assert "codex-command-runner.exe" in issue
    assert "codex-windows-sandbox-setup.exe" in issue
    managed.with_name("codex-code-mode-host.exe").write_text("binary")
    managed.with_name("codex-command-runner.exe").write_text("binary")
    managed.with_name("codex-windows-sandbox-setup.exe").write_text("binary")
    assert provider_runtime_issue("codex", str(managed)) is None


def test_non_managed_codex_layout_is_left_to_the_cli():
    assert provider_runtime_issue("codex", "/usr/bin/codex") is None


def test_windows_resolver_prefers_npm_cmd_shim():
    class Windows:
        name = "nt"

    class Resolver:
        @staticmethod
        def which(name):
            return f"C:/npm/{name}" if name in {"codex", "codex.cmd"} else None

    assert windows_aware_executable(
        "codex", os_module=Windows, shutil_module=Resolver
    ) == "C:/npm/codex.cmd"


def test_windows_resolver_prefers_managed_exe_over_extensionless_alias(tmp_path):
    managed = tmp_path / "Programs" / "Joe" / "bin" / "codex.exe"
    managed.parent.mkdir(parents=True)
    managed.write_text("binary")

    class Windows:
        name = "nt"
        environ = {"LOCALAPPDATA": str(tmp_path)}

    class Resolver:
        @staticmethod
        def which(name):
            return "C:/WindowsApps/codex" if name == "codex" else None

    assert windows_aware_executable(
        "codex", os_module=Windows, shutil_module=Resolver
    ) == str(managed)


class ScriptProvider(Provider):
    def __init__(
        self, script: str, name: str = "fake", watchdog_seconds: float = 90
    ):
        super().__init__(
            name,
            sys.executable,
            watchdog_seconds=watchdog_seconds,
        )
        object.__setattr__(self, "script", script)

    def command(
        self, prompt, cwd, intent, model=None, effort=None, execution_mode=None
    ):
        return [sys.executable, "-c", self.script]


def test_provider_captures_stdout_stderr_and_metadata(tmp_path):
    provider = ScriptProvider(
        "import sys; print('ok'); print('warn', file=sys.stderr)"
    )
    streamed = []
    result = provider.run(
        "hello",
        tmp_path,
        Intent.ANALYZE,
        timeout=2,
        on_stream=lambda stream, text: streamed.append((stream, text)),
    )
    assert result.ok
    assert result.stdout.strip() == "ok"
    assert result.stderr.strip() == "warn"
    assert result.duration_seconds >= 0
    assert ("stdout", "ok\n") in streamed
    assert ("stderr", "warn\n") in streamed


def test_provider_timeout_is_reported(tmp_path):
    provider = ScriptProvider("import time; time.sleep(2)")
    result = provider.run("hello", tmp_path, Intent.ANALYZE, timeout=0.01)
    assert result.timed_out
    assert result.error_kind == "timeout"


def test_gemini_watchdog_stops_an_inactive_process(tmp_path):
    provider = ScriptProvider(
        "import time; time.sleep(2)",
        name="gemini",
        watchdog_seconds=0.05,
    )
    events = []

    result = provider.run(
        "hello",
        tmp_path,
        Intent.ANALYZE,
        timeout=1,
        on_stream=lambda stream, text: events.append((stream, text)),
    )

    assert result.timed_out
    assert any("Gemini ne répond plus" in text for _, text in events)


def test_silent_provider_emits_elapsed_time_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "PROVIDER_HEARTBEAT_SECONDS", 0.01)
    provider = ScriptProvider("import time; time.sleep(0.35)")
    events = []

    result = provider.run(
        "hello",
        tmp_path,
        Intent.ANALYZE,
        timeout=2,
        on_stream=lambda stream, text: events.append((stream, text)),
    )

    assert result.ok
    assert any("Toujours en cours" in text for _, text in events)
    assert any("Processus actif depuis" in text for _, text in events)


def test_provider_can_be_cancelled_without_waiting_for_timeout(tmp_path):
    provider = ScriptProvider("import time; time.sleep(30)")
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()

    result = provider.run(
        "hello", tmp_path, Intent.ANALYZE, timeout=60, cancel_event=cancel
    )

    assert result.error_kind == "cancelled"
    assert time.monotonic() - started < 3


def test_provider_does_not_wait_for_timeout_when_helper_keeps_pipes_open(tmp_path):
    if os.name == "nt":
        return
    provider = ScriptProvider(
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "print('done', flush=True)",
        watchdog_seconds=None,
    )
    started = time.monotonic()

    result = provider.run("hello", tmp_path, Intent.ANALYZE, timeout=10)

    assert result.ok
    assert result.stdout.strip() == "done"
    assert time.monotonic() - started < 4


def test_provider_process_groups_are_portable():
    assert _process_group_options("posix") == {"start_new_session": True}
    windows = _process_group_options("nt")
    assert "creationflags" in windows
    assert "start_new_session" not in windows


def test_commands_match_inspected_noninteractive_interfaces():
    cwd = Path("/tmp/project")
    codex = Provider("codex", "codex").command("p", cwd, Intent.ANALYZE)
    assert codex[:3] == ["codex", "--ask-for-approval", "never"]
    assert "project_doc_max_bytes=0" in codex
    assert ["exec", "--json"] == codex[5:7]
    assert codex[-1] == "-"
    assert "p" not in codex
    assert "--print" in Provider("claude", "claude").command("p", cwd, Intent.ANALYZE)
    assert "stream-json" in Provider("claude", "claude").command("p", cwd, Intent.ANALYZE)
    assert "--prompt" in Provider("gemini", "gemini").command("p", cwd, Intent.ANALYZE)
    assert "--prompt" in Provider("copilot", "copilot").command("p", cwd, Intent.ANALYZE)


def test_codex_prompt_uses_stdin_without_platform_command_line_limits(tmp_path):
    class StdinCodex(Provider):
        def command(
            self, prompt, cwd, intent, model=None, effort=None, execution_mode=None
        ):
            script = (
                "import json,sys; sys.stdin.reconfigure(encoding='utf-8'); "
                "n=len(sys.stdin.read()); "
                "print(json.dumps({'item':{'type':'agent_message','text':str(n)}}))"
            )
            return [sys.executable, "-c", script]

    prompt = "é" * 100_000
    result = StdinCodex("codex", sys.executable).run(
        prompt, tmp_path, Intent.ANALYZE, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(len(prompt))
    assert prompt not in result.command


def test_model_is_forwarded_to_each_cli():
    cwd = Path("/tmp/project")
    for name in ("codex", "claude", "gemini", "copilot"):
        command = Provider(name, name).command("p", cwd, Intent.ANALYZE, "chosen")
        assert "--model" in command
        assert "chosen" in command


def test_effort_and_safe_execution_modes_are_forwarded():
    cwd = Path("/tmp/project")
    codex = Provider("codex", "codex").command(
        "p", cwd, Intent.MODIFY, effort="high", execution_mode="read-only"
    )
    assert 'model_reasoning_effort="high"' in codex
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    claude = Provider("claude", "claude").command(
        "p", cwd, Intent.MODIFY, effort="xhigh", execution_mode="plan"
    )
    assert claude[claude.index("--effort") + 1] == "xhigh"
    assert claude[claude.index("--permission-mode") + 1] == "plan"


def test_provider_specific_read_only_modes_translate_across_providers():
    cwd = Path("/tmp/project")

    codex = Provider("codex", "codex").command(
        "p", cwd, Intent.MODIFY, execution_mode="plan"
    )
    claude = Provider("claude", "claude").command(
        "p", cwd, Intent.MODIFY, execution_mode="read-only"
    )
    gemini = Provider("gemini", "gemini").command(
        "p", cwd, Intent.MODIFY, execution_mode="plan"
    )
    copilot = Provider("copilot", "copilot").command(
        "p", cwd, Intent.MODIFY, execution_mode="read-only"
    )

    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert claude[claude.index("--permission-mode") + 1] == "plan"
    assert gemini[gemini.index("--approval-mode") + 1] == "plan"
    assert "--plan" in copilot
    assert not any(argument.startswith("--allow-tool") for argument in copilot)


def test_restricted_claude_mode_never_escalates_on_other_providers():
    cwd = Path("/tmp/project")

    claude = Provider("claude", "claude").command(
        "p", cwd, Intent.MODIFY, execution_mode="dontAsk"
    )
    codex = Provider("codex", "codex").command(
        "p", cwd, Intent.MODIFY, execution_mode="dontAsk"
    )

    assert claude[claude.index("--permission-mode") + 1] == "dontAsk"
    assert codex[codex.index("--sandbox") + 1] == "read-only"


def test_generic_write_modes_are_translated_for_non_codex_clis():
    cwd = Path("/tmp/project")
    claude = Provider("claude", "claude").command(
        "p", cwd, Intent.ANALYZE, execution_mode="danger-full-access"
    )
    gemini = Provider("gemini", "gemini").command(
        "p", cwd, Intent.ANALYZE, execution_mode="workspace-write"
    )
    copilot = Provider("copilot", "copilot").command(
        "p", cwd, Intent.ANALYZE, execution_mode="workspace-write"
    )

    assert claude[claude.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--allow-dangerously-skip-permissions" not in claude
    assert gemini[gemini.index("--approval-mode") + 1] == "yolo"
    assert "--allow-tool=write" in copilot
    assert "--allow-tool=shell" in copilot


def test_write_access_can_actually_run_commands_on_every_cli():
    """Écrire sans pouvoir lancer les tests écrits n'est pas un accès en écriture.

    `acceptEdits` (Claude) et `auto_edit` (Gemini) n'auto-approuvent que
    l'édition de fichiers : pytest et npm y sont refusés, et le mode `--print`
    n'offre aucun canal d'approbation en cours de run. Le symptôme observé
    était « Refusé par le sandbox » sur un projet pourtant réglé en accès
    automatique.
    """
    cwd = Path("/tmp/project")
    claude = Provider("claude", "claude").command(
        "p", cwd, Intent.MODIFY, execution_mode="workspace-write"
    )
    gemini = Provider("gemini", "gemini").command(
        "p", cwd, Intent.MODIFY, execution_mode="workspace-write"
    )
    copilot = Provider("copilot", "copilot").command(
        "p", cwd, Intent.MODIFY, execution_mode="workspace-write"
    )

    assert claude[claude.index("--allowedTools") + 1] == "Bash"
    assert gemini[gemini.index("--approval-mode") + 1] == "yolo"
    assert "--allow-tool=shell" in copilot


def test_read_only_access_never_grants_commands():
    cwd = Path("/tmp/project")
    claude = Provider("claude", "claude").command(
        "p", cwd, Intent.MODIFY, execution_mode="read-only"
    )
    gemini = Provider("gemini", "gemini").command(
        "p", cwd, Intent.MODIFY, execution_mode="read-only"
    )

    assert "--allowedTools" not in claude
    assert claude[claude.index("--permission-mode") + 1] == "plan"
    assert gemini[gemini.index("--approval-mode") + 1] == "plan"


def test_full_access_remains_scoped_to_declared_project_roots():
    cwd = Path("/tmp/project")
    extra = (Path("/tmp/vision"),)
    codex = Provider("codex", "codex", extra).command(
        "p", cwd, Intent.MODIFY, execution_mode="danger-full-access"
    )
    claude = Provider("claude", "claude", extra).command(
        "p", cwd, Intent.MODIFY, execution_mode="danger-full-access"
    )
    gemini = Provider("gemini", "gemini", extra).command(
        "p", cwd, Intent.MODIFY, execution_mode="danger-full-access"
    )
    copilot = Provider("copilot", "copilot", extra).command(
        "p", cwd, Intent.MODIFY, execution_mode="danger-full-access"
    )

    assert codex[codex.index("--sandbox") + 1] == "workspace-write"
    assert claude[claude.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--allow-dangerously-skip-permissions" not in claude
    assert gemini[gemini.index("--approval-mode") + 1] == "yolo"
    assert "--allow-tool=write" in copilot
    assert "--allow-tool=shell" in copilot
    for command, flag in (
        (codex, "--add-dir"),
        (claude, "--add-dir"),
        (gemini, "--include-directories"),
        (copilot, "--add-dir"),
    ):
        assert command[command.index(flag) + 1] == str(extra[0])


def test_additional_project_roots_are_forwarded_to_each_cli():
    cwd = Path("/tmp/project")
    extra = (Path("/tmp/vision"),)

    codex = Provider("codex", "codex", extra).command(
        "p", cwd, Intent.ANALYZE
    )
    assert codex[codex.index("--add-dir") + 1] == str(extra[0])
    for name, flag in (
        ("claude", "--add-dir"),
        ("gemini", "--include-directories"),
        ("copilot", "--add-dir"),
    ):
        command = Provider(name, name, extra).command("p", cwd, Intent.ANALYZE)
        assert command[command.index(flag) + 1] == str(extra[0])


def test_codex_remote_access_enables_native_search_and_write_network():
    command = Provider("codex", "codex", remote_access=True).command(
        "p", Path("/tmp/project"), Intent.MODIFY
    )

    assert "--search" in command
    assert command.index("--search") < command.index("exec")
    assert "sandbox_workspace_write.network_access=true" in command

    read_only = Provider("codex", "codex", remote_access=True).command(
        "p", Path("/tmp/project"), Intent.ANALYZE
    )
    assert "--search" in read_only
    assert read_only.index("--search") < read_only.index("exec")
    assert "sandbox_workspace_write.network_access=true" not in read_only


def test_error_classification():
    assert classify_error("Rate limit exceeded", 1) == "quota"
    assert classify_error("You've hit your usage limit · resets 3am", 1) == "quota"
    assert classify_error(
        '{"type":"rate_limit_event","api_error_status":429}', 1
    ) == "quota"
    assert classify_error("Please login", 1) == "authentication"
    assert classify_error("", 2) == "process"
    assert classify_error(
        "You exceeded your current quota", 0, "gemini"
    ) == "quota"
    assert classify_error(
        "L’authentification est valide et le quota est disponible",
        0,
        "codex",
    ) is None
    # Les marqueurs de quota terminal viennent du registre, plus d'un test
    # sur le nom du fournisseur.
    assert _terminal_quota(
        "gemini", "429 RESOURCE_EXHAUSTED: exceeded your current quota"
    )
    assert not _terminal_quota(
        "codex", "429 RESOURCE_EXHAUSTED: exceeded your current quota"
    )


def test_environment_secret_values_are_redacted_from_streams():
    values = _secret_values(
        {"PUBLIC": "visible-value", "SERVICE_TOKEN": "private-value"}
    )
    assert _redact_values("token=private-value", values) == "token=[REDACTED]"
    assert _redact_values("visible-value", values) == "visible-value"


def test_codex_json_stream_exposes_command_and_final_message():
    command = (
        '{"type":"item.started","item":{"type":"command_execution",'
        '"command":"sed -n 1,40p app.py"}}'
    )
    message = (
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"Terminé."}}'
    )

    activity = _activity("codex", "stdout", command)

    assert activity == {
        "kind": "command_execution",
        "label": "Commande",
        "detail": "sed -n 1,40p app.py",
    }
    assert _final_output("codex", f"{command}\n{message}\n") == "Terminé."


def test_codex_command_disables_duplicate_native_project_docs(tmp_path):
    provider = Provider("codex", "codex")
    command = provider.command("inspect", tmp_path, Intent.ANALYZE)

    assert "project_doc_max_bytes=0" in command


def test_codex_json_stream_does_not_repeat_completed_command():
    completed = (
        '{"type":"item.completed","item":{"type":"command_execution",'
        '"command":"sed -n 1,40p app.py"}}'
    )

    assert _activity("codex", "stdout", completed) is None


def test_claude_json_stream_exposes_file_tool_and_result():
    tool = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"Read","input":{"file_path":"src/app.py"}}]}}'
    )
    result = '{"type":"result","result":"Analyse terminée."}'

    activity = _activity("claude", "stdout", tool)

    assert activity["label"] == "Read"
    assert activity["detail"] == "src/app.py"
    assert _final_output("claude", f"{tool}\n{result}\n") == "Analyse terminée."


def test_claude_system_event_exposes_precise_model_without_ready_line():
    with_model = _activity(
        "claude",
        "stdout",
        '{"type":"system","subtype":"init","model":"claude-sonnet-4-5-20250929"}',
    )
    assert with_model == {
        "kind": "model",
        "label": "claude-sonnet-4-5-20250929",
        "detail": "",
    }
    # No precise model advertised → no "Session prête" noise is emitted.
    assert _activity("claude", "stdout", '{"type":"system","subtype":"init"}') is None


def test_gemini_init_event_exposes_precise_model():
    activity = _activity("gemini", "stdout", '{"type":"init","model":"gemini-3-pro"}')
    assert activity == {"kind": "model", "label": "gemini-3-pro", "detail": ""}
    assert _activity("gemini", "stdout", '{"type":"init"}') is None


def test_final_output_keeps_only_the_structured_result():
    result = (
        '{"type":"result","result":"Ce que je vais faire :\\nAnalyser.\\n\\n'
        'Résultat :\\nLes tests passent."}'
    )

    assert _final_output("claude", result) == (
        "## Résultat\n\nLes tests passent."
    )


def test_gemini_stream_fragments_are_reassembled_without_broken_words():
    first = '{"type":"message","role":"assistant","content":"Voici la syn"}'
    second = '{"type":"message","role":"assistant","content":"thèse.\\n\\n## Résultat"}'

    assert _final_output("gemini", f"{first}\n{second}\n") == (
        "Voici la synthèse.\n\n## Résultat"
    )


def test_network_control_declaration_matches_commands():
    """La portée déclarée du réglage réseau doit refléter les commandes réelles.

    Ce test échoue le jour où une CLI gagne (ou perd) un commutateur réseau
    sans que la déclaration exposée à l'interface soit mise à jour.
    """
    from joe.providers import NETWORK_CONTROLLED_PROVIDERS

    effective = []
    for name in ("codex", "claude", "gemini", "copilot"):
        blocked = Provider(name, name).command(
            "p", Path("/tmp"), Intent.MODIFY, execution_mode="workspace-write"
        )
        allowed = Provider(name, name, remote_access=True).command(
            "p", Path("/tmp"), Intent.MODIFY, execution_mode="workspace-write"
        )
        if blocked != allowed:
            effective.append(name)

    assert tuple(effective) == NETWORK_CONTROLLED_PROVIDERS


def test_registry_is_the_single_source_of_provider_behaviour():
    """Un comportement propre à un fournisseur se déclare une seule fois."""
    from joe.maintenance import KNOWN_VERSIONS
    from joe.memory import DEFAULT_CONFIG
    from joe.provider_registry import (
        default_fallbacks,
        get_provider_names,
        get_provider_specs,
        minimum_versions,
        usage_providers,
    )
    from joe.conversations import _VALID_AGENTS

    names = set(get_provider_names())
    assert _VALID_AGENTS == {"", *names}
    assert set(KNOWN_VERSIONS) == names
    assert KNOWN_VERSIONS == minimum_versions()
    assert DEFAULT_CONFIG["fallbacks"] == default_fallbacks()
    assert set(usage_providers()) <= names
    # Chaque repli déclaré doit pointer vers un fournisseur connu.
    for spec in get_provider_specs():
        assert set(spec.fallbacks) <= names, spec.name
        assert set(spec.reviewer_peers) <= names, spec.name


def test_the_watchdog_is_armed_by_the_declared_delay_alone():
    """Plus aucun test sur le nom du fournisseur n'arme le chien de garde."""
    from joe.provider_registry import get_provider_spec

    assert get_provider_spec("gemini").watchdog_seconds
    assert get_provider_spec("codex").watchdog_seconds is None
    assert Provider("gemini", "gemini").watchdog_delay == 90
    assert Provider("codex", "codex").watchdog_delay is None
    # Un override d'instance reste prioritaire.
    assert Provider("codex", "codex", watchdog_seconds=5).watchdog_delay == 5


def test_streaming_providers_all_have_a_parser_and_an_extractor():
    """Sinon le fournisseur se dégrade en silence : aucun événement, sortie brute."""
    from joe.providers import _ACTIVITY_PARSERS, _FINAL_EXTRACTORS
    from joe.provider_registry import get_provider_specs

    streaming = {spec.name for spec in get_provider_specs() if spec.streams_json}
    assert set(_ACTIVITY_PARSERS) == streaming
    assert set(_FINAL_EXTRACTORS) == streaming


def test_the_reviewer_counterpart_comes_from_the_registry():
    from joe.provider_registry import counterpart

    assert counterpart("codex") == "claude"
    assert counterpart("claude") == "codex"
    # Gemini et Copilot ont désormais un complémentaire déclaré, au lieu
    # d'être exclus par une expression codée en dur.
    assert counterpart("gemini") == "codex"
    assert counterpart("copilot") == "codex"
    assert counterpart("codex", eligible=("codex",)) is None
