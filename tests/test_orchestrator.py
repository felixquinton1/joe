from pathlib import Path
import time

from joe.models import Intent, Mode, ProviderResult, Route
from joe.orchestrator import Orchestrator


class FakeProvider:
    def __init__(
        self, name: str, *, fail: bool = False, responses=None, delay: float = 0
    ):
        self.name = name
        self.fail = fail
        self.responses = list(responses or [])
        self.delay = delay
        self.calls = []

    def run(
        self, prompt, cwd, intent, timeout, *, model=None, effort=None,
        execution_mode=None, cancel_event=None, on_stream=None
    ):
        self.calls.append((prompt, cwd, intent, timeout))
        time.sleep(self.delay)
        if on_stream and not self.fail:
            on_stream("stdout", f"{self.name} response\n")
        response = self.responses.pop(0) if self.responses else f"{self.name} response"
        return ProviderResult(
            self.name,
            [self.name],
            "" if self.fail else response,
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
    assert "## Contrôle croisé" in response
    assert "### Avis de Claude" in response
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


def test_review_applies_one_correction_pass_when_requested(tmp_path):
    providers = {
        "codex": FakeProvider(
            "codex", responses=["implementation", "corrected implementation"]
        ),
        "claude": FakeProvider(
            "claude", responses=["VERDICT: CORRECTIONS_REQUIRED\nFix the test."]
        ),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    events = []
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "large change",
        Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude"),
        on_event=events.append,
    )

    assert response.startswith("corrected implementation")
    assert len(providers["codex"].calls) == 2
    assert len(providers["claude"].calls) == 1
    assert any(
        event.get("stage") == "correction"
        and event.get("status") == "complete"
        for event in events
    )


def test_consensus_emits_structured_completed_opinions(tmp_path):
    providers = {
        name: FakeProvider(name)
        for name in ("codex", "claude", "gemini", "copilot")
    }
    events = []
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "important",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex"),
        on_event=events.append,
    )

    completed = [
        event for event in events
        if event.get("type") == "workflow_update"
        and event.get("status") == "complete"
    ]
    assert {event["stage"] for event in completed} == {
        "proposal_codex",
        "proposal_claude",
        "review_codex",
        "review_claude",
        "synthesis",
    }
    assert all(
        event.get("content")
        for event in completed
        if event["stage"] != "synthesis"
    )


def test_consensus_runs_proposals_and_reviews_in_parallel(tmp_path):
    providers = {
        "codex": FakeProvider("codex", delay=0.12),
        "claude": FakeProvider("claude", delay=0.12),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    started = time.monotonic()
    orchestrator.execute(
        "important",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex"),
    )

    assert time.monotonic() - started < 0.4


def test_consensus_marks_each_proposal_complete_without_waiting_for_peer(
    tmp_path,
):
    providers = {
        "codex": FakeProvider("codex", delay=0.01),
        "claude": FakeProvider("claude", delay=0.08),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    events = []
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "important",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex"),
        on_event=events.append,
    )

    codex_complete = next(
        index
        for index, event in enumerate(events)
        if event.get("stage") == "proposal_codex"
        and event.get("status") == "complete"
    )
    claude_finished = next(
        index
        for index, event in enumerate(events)
        if event.get("type") == "provider_end"
        and event.get("provider") == "claude"
    )
    assert codex_complete < claude_finished


def test_health_check_skips_project_context_and_uses_short_timeout(tmp_path):
    providers = {
        name: FakeProvider(name)
        for name in ("codex", "claude", "gemini", "copilot")
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "petit test de Gemini",
        Route(
            Intent.ANALYZE,
            Mode.FAST,
            "gemini",
            reason="analyze; fast; preferred=gemini; health-check",
        ),
        extra_context="SECRET CONVERSATION CONTEXT",
    )

    prompt, _, _, timeout = providers["gemini"].calls[0]
    assert "SECRET CONVERSATION CONTEXT" not in prompt
    assert "17 × 23" in prompt
    assert timeout == 30
