import sys
import threading
import time
from pathlib import Path

from joe.models import Intent
from joe.providers import (
    Provider,
    _activity,
    _final_output,
    _redact_values,
    _secret_values,
    _terminal_gemini_quota,
    classify_error,
)


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


def test_commands_match_inspected_noninteractive_interfaces():
    cwd = Path("/tmp/project")
    codex = Provider("codex", "codex").command("p", cwd, Intent.ANALYZE)
    assert codex[:5] == ["codex", "--ask-for-approval", "never", "exec", "--json"]
    assert "--print" in Provider("claude", "claude").command("p", cwd, Intent.ANALYZE)
    assert "stream-json" in Provider("claude", "claude").command("p", cwd, Intent.ANALYZE)
    assert "--prompt" in Provider("gemini", "gemini").command("p", cwd, Intent.ANALYZE)
    assert "--prompt" in Provider("copilot", "copilot").command("p", cwd, Intent.ANALYZE)


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


def test_additional_project_roots_are_forwarded_to_each_cli():
    cwd = Path("/tmp/project")
    extra = (Path("/tmp/vision"),)

    codex = Provider("codex", "codex", extra).command(
        "p", cwd, Intent.ANALYZE
    )
    assert codex[codex.index("--add-dir") + 1] == "/tmp/vision"
    for name, flag in (
        ("claude", "--add-dir"),
        ("gemini", "--include-directories"),
        ("copilot", "--add-dir"),
    ):
        command = Provider(name, name, extra).command("p", cwd, Intent.ANALYZE)
        assert command[command.index(flag) + 1] == "/tmp/vision"


def test_codex_remote_access_is_scoped_to_workspace_write():
    command = Provider("codex", "codex", remote_access=True).command(
        "p", Path("/tmp/project"), Intent.MODIFY
    )

    assert "sandbox_workspace_write.network_access=true" in command


def test_error_classification():
    assert classify_error("Rate limit exceeded", 1) == "quota"
    assert classify_error("You've hit your usage limit · resets 3am", 1) == "quota"
    assert classify_error(
        '{"type":"rate_limit_event","api_error_status":429}', 1
    ) == "quota"
    assert classify_error("Please login", 1) == "authentication"
    assert classify_error("", 2) == "process"
    assert _terminal_gemini_quota(
        "429 RESOURCE_EXHAUSTED: exceeded your current quota"
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


def test_gemini_stream_fragments_are_reassembled_without_broken_words():
    first = '{"type":"message","role":"assistant","content":"Voici la syn"}'
    second = '{"type":"message","role":"assistant","content":"thèse.\\n\\n## Résultat"}'

    assert _final_output("gemini", f"{first}\n{second}\n") == (
        "Voici la synthèse.\n\n## Résultat"
    )
