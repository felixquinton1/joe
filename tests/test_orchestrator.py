from pathlib import Path

from joe.models import Intent, Mode, ProviderResult, Route
from joe.orchestrator import Orchestrator


class FakeProvider:
    def __init__(self, name: str, *, fail: bool = False):
        self.name = name
        self.fail = fail
        self.calls = []

    def run(
        self, prompt, cwd, intent, timeout, *, model=None, effort=None,
        execution_mode=None, cancel_event=None, on_stream=None
    ):
        self.calls.append((prompt, cwd, intent, timeout))
        if on_stream and not self.fail:
            on_stream("stdout", f"{self.name} response\n")
        return ProviderResult(
            self.name,
            [self.name],
            "" if self.fail else f"{self.name} response",
            "quota" if self.fail else "",
            1 if self.fail else 0,
            0.01,
            error_kind="quota" if self.fail else None,
        )


def test_fast_uses_one_call(tmp_path):
    providers = {name: FakeProvider(name) for name in ("codex", "claude", "gemini", "copilot")}
    orchestrator = Orchestrator(tmp_path, providers=providers)
    response, log = orchestrator.execute(
        "simple", Route(Intent.ANALYZE, Mode.FAST, "codex")
    )
    assert response == "codex response"
    assert sum(len(item.calls) for item in providers.values()) == 1
    assert log.exists()


def test_failure_falls_back(tmp_path):
    providers = {
        "codex": FakeProvider("codex", fail=True),
        "claude": FakeProvider("claude"),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)
    response, _ = orchestrator.execute(
        "simple", Route(Intent.ANALYZE, Mode.FAST, "codex")
    )
    assert response == "gemini response"
    assert len(providers["codex"].calls) == 1
    assert len(providers["gemini"].calls) == 1


def test_review_uses_primary_intent_then_read_only_review(tmp_path):
    providers = {name: FakeProvider(name) for name in ("codex", "claude", "gemini", "copilot")}
    orchestrator = Orchestrator(tmp_path, providers=providers)
    response, _ = orchestrator.execute(
        "change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude")
    )
    assert "Review by claude" in response
    assert providers["codex"].calls[0][2] is Intent.MODIFY
    assert providers["claude"].calls[0][2] is Intent.ANALYZE


def test_consensus_is_read_only_and_uses_distinct_proposals(tmp_path):
    providers = {name: FakeProvider(name) for name in ("codex", "claude", "gemini", "copilot")}
    orchestrator = Orchestrator(tmp_path, providers=providers)
    response, _ = orchestrator.execute(
        "important", Route(Intent.MODIFY, Mode.CONSENSUS, "codex")
    )
    assert response == "gemini response"
    assert providers["codex"].calls
    assert providers["claude"].calls
    assert all(
        call[2] is Intent.ANALYZE
        for provider in providers.values()
        for call in provider.calls
    )
