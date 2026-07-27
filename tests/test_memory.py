import json
import stat

from joe.memory import ProjectMemory, redact


def test_memory_is_created_and_active_files_are_rewritten(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.ensure()
    assert (tmp_path / ".agentflow" / "config.yaml").exists()
    memory.update_active(
        request="first", provider="codex", mode="fast", response="old"
    )
    memory.update_active(
        request="second", provider="claude", mode="fast", response="new"
    )
    text = (tmp_path / ".agentflow" / "session.md").read_text()
    assert "second" in text and "new" in text
    assert "first" not in text and "old" not in text
    assert memory.previous_provider() == "claude"


def test_context_is_bounded_and_includes_project_instructions(tmp_path):
    (tmp_path / "AGENTS.md").write_text("important rule")
    memory = ProjectMemory(tmp_path)
    memory.ensure()
    config_path = tmp_path / ".agentflow" / "config.yaml"
    config = json.loads(config_path.read_text())
    config["max_context_chars"] = 180
    config_path.write_text(json.dumps(config))
    context = memory.context("current request")
    assert len(context) <= 180
    assert "current request" in context


def test_logs_redact_common_secret_shapes(tmp_path):
    memory = ProjectMemory(tmp_path)
    path = memory.save_run(
        "one", {"stderr": 'API_KEY="super-secret"', "nested": ["token:abc"]}
    )
    assert "super-secret" not in path.read_text()
    assert "abc" not in path.read_text()
    assert "[REDACTED]" in redact("token:abc")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(memory.root.stat().st_mode) == 0o700


def test_active_memory_redacts_request(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.update_active(
        request="use token:private-value",
        provider="codex",
        mode="fast",
        response="done",
    )
    assert "private-value" not in (memory.root / "session.md").read_text()


def test_project_memory_gitignore_separates_shared_and_local_state(tmp_path):
    memory = ProjectMemory(tmp_path)
    memory.ensure()

    rules = (memory.root / ".gitignore").read_text()
    assert "conversations.json" in rules
    assert "runs/" in rules
    assert "session.md" in rules
    assert "project.md" not in rules
    assert "config.yaml" not in rules
