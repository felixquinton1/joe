import sys
from pathlib import Path

from joe.models import Intent
from joe.providers import Provider, _redact_values, _secret_values, classify_error


class ScriptProvider(Provider):
    def __init__(self, script: str):
        super().__init__("fake", sys.executable)
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


def test_commands_match_inspected_noninteractive_interfaces():
    cwd = Path("/tmp/project")
    codex = Provider("codex", "codex").command("p", cwd, Intent.ANALYZE)
    assert codex[:4] == ["codex", "--ask-for-approval", "never", "exec"]
    assert "--print" in Provider("claude", "claude").command("p", cwd, Intent.ANALYZE)
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


def test_error_classification():
    assert classify_error("Rate limit exceeded", 1) == "quota"
    assert classify_error("Please login", 1) == "authentication"
    assert classify_error("", 2) == "process"


def test_environment_secret_values_are_redacted_from_streams():
    values = _secret_values(
        {"PUBLIC": "visible-value", "SERVICE_TOKEN": "private-value"}
    )
    assert _redact_values("token=private-value", values) == "token=[REDACTED]"
    assert _redact_values("visible-value", values) == "visible-value"
