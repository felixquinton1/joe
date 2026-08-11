import json
from pathlib import Path
import time

from joe.models import Intent, Mode, ProviderResult, Route
from joe.orchestrator import (
    OrchestrationError,
    Orchestrator,
    _external_reference_context,
    requires_fresh_workspace,
)
from joe.orchestrator_workflows import clean_report
from joe.provider_health import clear_cooldowns, record_result


class FakeProvider:
    def __init__(
        self, name: str, *, fail: bool = False, responses=None, delay: float = 0
    ):
        self.name = name
        self.fail = fail
        self.responses = list(responses or [])
        self.delay = delay
        self.calls = []
        self.execution_modes = []

    def run(
        self, prompt, cwd, intent, timeout, *, model=None, effort=None,
        execution_mode=None, cancel_event=None, on_stream=None
    ):
        self.calls.append((prompt, cwd, intent, timeout))
        self.execution_modes.append(execution_mode)
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


def test_mutable_state_request_requires_current_workspace_inspection(tmp_path):
    providers = {"claude": FakeProvider("claude")}
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, log = orchestrator.execute(
        "Que recommandes-tu comme amélioration de Joe actuellement ?",
        Route(Intent.ANALYZE, Mode.FAST, "claude"),
    )

    prompt = providers["claude"].calls[0][0]
    payload = json.loads(log.read_text())
    assert requires_fresh_workspace(
        "Est-ce que les worktrees sont déjà implémentés ?"
    )
    assert not requires_fresh_workspace("Qu'est-ce qu'un worktree ?")
    assert "# Current workspace observation" in prompt
    assert "# Grounding requirement" in prompt
    assert "untrusted historical context" in prompt
    assert response.startswith("> État actuel non vérifié")
    assert payload["workspace_observation"]["workspace"] == str(tmp_path)
    assert payload["tool_activity"] == []


def test_mutable_state_answer_records_observed_tool_activity(tmp_path):
    class ToolProvider(FakeProvider):
        def run(self, *args, on_stream=None, **kwargs):
            if on_stream:
                on_stream(
                    "activity",
                    '{"kind":"tool","label":"Read","detail":"src/joe/worktrees.py"}',
                )
            return super().run(*args, on_stream=None, **kwargs)

    providers = {"claude": ToolProvider("claude")}
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, log = orchestrator.execute(
        "Vérifie si les worktrees sont implémentés",
        Route(Intent.ANALYZE, Mode.FAST, "claude"),
    )

    payload = json.loads(log.read_text())
    assert response == "claude response"
    assert payload["tool_activity"] == [
        {
            "provider": "claude",
            "kind": "tool",
            "label": "Read",
            "detail": "src/joe/worktrees.py",
        }
    ]


def test_provider_start_reports_only_concrete_model_and_effort(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "joe.orchestrator.provider_defaults",
        lambda provider, model, effort: (
            model or "gpt-5.6-sol",
            effort or "low",
        ),
    )
    providers = {"codex": FakeProvider("codex")}
    events = []
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "simple",
        Route(Intent.ANALYZE, Mode.FAST, "codex"),
        on_event=events.append,
    )

    start = next(event for event in events if event["type"] == "provider_start")
    assert start == {
        "type": "provider_start",
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "effort": "low",
    }


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


def test_fallback_preserves_explicit_read_only_permission(tmp_path):
    providers = {
        "codex": FakeProvider("codex", fail=True),
        "claude": FakeProvider("claude"),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "do not modify",
        Route(Intent.MODIFY, Mode.FAST, "codex"),
        execution_mode="plan",
    )

    assert providers["codex"].execution_modes == ["plan"]
    assert providers["gemini"].execution_modes == ["plan"]


def test_review_uses_primary_intent_then_read_only_review(tmp_path):
    providers = {name: FakeProvider(name) for name in ("codex", "claude", "gemini", "copilot")}
    orchestrator = Orchestrator(tmp_path, providers=providers)
    response, _ = orchestrator.execute(
        "change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude")
    )
    assert "## Contrôle croisé" in response
    assert "Le détail de l’avis de Claude reste disponible" in response
    assert "claude response" not in response
    assert providers["codex"].calls[0][2] is Intent.MODIFY
    assert providers["claude"].calls[0][2] is Intent.ANALYZE
    assert "Do not invoke another AI provider" in providers["codex"].calls[0][0]


def test_review_fallback_never_uses_implementation_provider(tmp_path):
    providers = {
        "codex": FakeProvider("codex"),
        "claude": FakeProvider("claude", fail=True),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude")
    )

    assert "Le détail de l’avis de Gemini reste disponible" in response
    assert len(providers["codex"].calls) == 1
    assert len(providers["claude"].calls) == 1
    assert len(providers["gemini"].calls) == 1


def test_review_skips_provider_in_recent_cooldown(tmp_path):
    clear_cooldowns()
    record_result(
        ProviderResult(
            "gemini", ["gemini"], "", "quota", 1, 0.01,
            error_kind="quota",
        )
    )
    providers = {
        name: FakeProvider(name)
        for name in ("codex", "claude", "gemini", "copilot")
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "gemini")
    )

    assert "Le détail de l’avis de Claude reste disponible" in response
    assert providers["gemini"].calls == []
    assert len(providers["claude"].calls) == 1
    clear_cooldowns()


def test_forced_fast_provider_can_run_during_cooldown(tmp_path):
    clear_cooldowns()
    record_result(
        ProviderResult(
            "gemini", ["gemini"], "", "timeout", 124, 0.01,
            timed_out=True, error_kind="timeout",
        )
    )
    providers = {"gemini": FakeProvider("gemini")}
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "force test", Route(Intent.ANALYZE, Mode.FAST, "gemini")
    )

    assert response == "gemini response"
    assert len(providers["gemini"].calls) == 1
    clear_cooldowns()


def test_timed_out_implementation_is_not_retried_by_another_provider(tmp_path):
    providers = {
        name: FakeProvider(name)
        for name in ("codex", "claude", "gemini", "copilot")
    }
    providers["codex"].run = lambda *args, **kwargs: ProviderResult(
        "codex",
        ["codex"],
        "",
        "provider timed out after possible writes",
        124,
        0.01,
        timed_out=True,
        error_kind="timeout",
    )
    orchestrator = Orchestrator(tmp_path, providers=providers)

    try:
        orchestrator.execute(
            "change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude")
        )
    except OrchestrationError as error:
        assert "codex:timeout" in str(error)
    else:
        raise AssertionError("A timed-out implementation must fail closed")

    assert providers["claude"].calls == []
    assert providers["gemini"].calls == []
    assert providers["copilot"].calls == []


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
    assert all(
        mode is None
        for provider in providers.values()
        for mode in provider.execution_modes
    )
    assert "operational checks belong to a REVIEW workflow" in (
        providers["codex"].calls[0][0]
    )
    assert all(
        call[3] == 300
        for provider in providers.values()
        for call in provider.calls
    )


def test_consensus_can_use_codex_and_gemini_participants(tmp_path):
    providers = {
        name: FakeProvider(name)
        for name in ("codex", "claude", "gemini", "copilot")
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "important",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex", "gemini"),
    )

    assert response == "gemini response"
    assert providers["codex"].calls
    assert providers["gemini"].calls
    assert providers["claude"].calls == []


def test_consensus_uses_gemini_when_claude_quota_is_exhausted(tmp_path):
    providers = {
        "codex": FakeProvider("codex"),
        "claude": FakeProvider("claude", fail=True),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)
    events = []

    response, _ = orchestrator.execute(
        "important",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex"),
        execution_mode="danger-full-access",
        on_event=events.append,
    )

    assert response.startswith("> ⚠️ **Consensus dégradé**")
    assert "Claude indisponible, relais par Gemini" in response
    assert any(
        event.get("type") == "provider_fallback"
        and event.get("provider") == "claude"
        and event.get("fallback") == "gemini"
        for event in events
    )


def test_consensus_replaces_a_timed_out_participant(tmp_path):
    providers = {
        name: FakeProvider(name)
        for name in ("codex", "claude", "gemini", "copilot")
    }
    providers["claude"].run = lambda *args, **kwargs: ProviderResult(
        "claude",
        ["claude"],
        "",
        "provider timed out",
        124,
        0.01,
        timed_out=True,
        error_kind="timeout",
    )
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "important",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex"),
    )

    assert response.startswith("> ⚠️ **Consensus dégradé**")
    assert "Claude indisponible, relais par Gemini" in response


def test_consensus_still_fails_closed_on_process_error(tmp_path):
    providers = {
        "codex": FakeProvider("codex"),
        "claude": FakeProvider("claude", fail=True),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    providers["claude"].run = lambda *args, **kwargs: ProviderResult(
        "claude", ["claude"], "", "spawn failed", 1, 0.01,
        error_kind="process",
    )
    orchestrator = Orchestrator(tmp_path, providers=providers)

    try:
        orchestrator.execute(
            "important",
            Route(Intent.ANALYZE, Mode.CONSENSUS, "codex"),
        )
    except OrchestrationError as error:
        assert "claude:process" in str(error)
    else:
        raise AssertionError("Consensus must fail on a material process error")

    assert providers["gemini"].calls == []


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


def test_external_github_reference_is_added_as_bounded_context(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, size): return b"# Maestro\nagent orchestration"

    monkeypatch.setattr(
        "joe.orchestrator.urllib.request.urlopen",
        lambda *args, **kwargs: Response(),
    )
    context = _external_reference_context(
        "Compare https://github.com/RunMaestro/Maestro"
    )
    assert "External public reference: RunMaestro/Maestro" in context
    assert "agent orchestration" in context


def test_known_product_source_is_fetched_and_ledgered(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, size): return b"<h1>Maestro</h1> worktrees"

    monkeypatch.setattr(
        "joe.orchestrator.urllib.request.urlopen",
        lambda *args, **kwargs: Response(),
    )
    context = _external_reference_context("Compare Maestro and Crewly")
    assert "[VERIFIED]" in context
    assert "## Source: maestro" in context


def test_clean_report_omits_internal_validation_refusal_section():
    report = (
        "## Résultat\nTout est prêt.\n\n"
        "## Refusé\nLa suite pytest est refusée par le sandbox."
    )

    cleaned = clean_report(report)

    assert "## Résultat" in cleaned
    assert "Refusé" not in cleaned
    assert "pytest" not in cleaned


def test_clean_report_preserves_functional_refusal():
    report = "## Refusé\nLe projet n'est pas accessible en écriture."

    assert clean_report(report) == report


def test_clean_report_removes_leaked_provider_tool_protocol():
    report = (
        "Ce que je vais faire :\n\n"
        "<tool_call>\n{\"name\": \"view\"}\n</tool_call>\n\n"
        "<tool_response>\n{\"result\": \"ok\"}\n</tool_response>\n\n"
        "## Résultat\nSynthèse propre."
    )
    cleaned = clean_report(report)
    assert cleaned == "## Résultat\nSynthèse propre."
    assert "tool_call" not in cleaned
