import json
import subprocess
import time

from joe.models import Intent, Mode, ProviderResult, Route
from joe.orchestrator import (
    OrchestrationError,
    Orchestrator,
    requires_fresh_workspace,
)
from joe.orchestrator_workflows import clean_report, complete_synthesis
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


def test_cancelled_provider_emits_terminal_event_before_run_stops(tmp_path):
    class CancelledProvider(FakeProvider):
        def run(self, *args, **kwargs):
            return ProviderResult(
                self.name,
                [self.name],
                "",
                "cancelled",
                130,
                0.01,
                error_kind="cancelled",
            )

    events = []
    orchestrator = Orchestrator(
        tmp_path,
        providers={"codex": CancelledProvider("codex")},
    )

    try:
        orchestrator.execute(
            "stop",
            Route(Intent.ANALYZE, Mode.FAST, "codex"),
            on_event=events.append,
        )
    except OrchestrationError as error:
        assert "interrompue" in str(error)
    else:
        raise AssertionError("A cancelled provider must stop orchestration")

    assert events[-1] == {
        "type": "provider_end",
        "provider": "codex",
        "ok": False,
        "error": "cancelled",
    }


def test_mutable_state_request_requires_current_workspace_inspection(tmp_path):
    providers = {"claude": FakeProvider("claude")}
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, log = orchestrator.execute(
        "Que recommandes-tu comme amélioration de Joe actuellement ?",
        Route(Intent.ANALYZE, Mode.FAST, "claude"),
    )

    prompt = providers["claude"].calls[0][0]
    payload = json.loads(log.read_text(encoding="utf-8"))
    assert requires_fresh_workspace(
        "Est-ce que les worktrees sont déjà implémentés ?"
    )
    assert not requires_fresh_workspace("Qu'est-ce qu'un worktree ?")
    assert not requires_fresh_workspace(
        "Should every code-modifying task run in an isolated Git worktree?"
    )
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

    payload = json.loads(log.read_text(encoding="utf-8"))
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
    # Gemini ne sert plus les comptes grand public : il est passe en fin de
    # chaine, donc c'est Claude qui reprend.
    assert response == "claude response"
    assert len(providers["codex"].calls) == 1
    assert len(providers["claude"].calls) == 1
    assert not providers["gemini"].calls


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
    # Le repli est Claude depuis que Gemini est passe en fin de chaine.
    assert providers["claude"].execution_modes == ["plan"]


def test_the_examiner_may_correct_the_code_it_examines(tmp_path):
    """Une fonctionnalite qui marche a moitie est le cas courant.

    L'examinateur etait lance en lecture seule et ne pouvait que decrire les
    defauts ; c'est l'auteur qui reecrivait, d'apres cette description plutot
    que d'apres le code. Celui qui a commis l'erreur etait celui a qui on
    demandait de la reparer, sans jamais la voir.
    """
    providers = {name: FakeProvider(name) for name in ("codex", "claude", "gemini", "copilot")}
    orchestrator = Orchestrator(tmp_path, providers=providers)
    response, _ = orchestrator.execute(
        "change",
        Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude"),
        execution_mode="acceptEdits",
    )
    assert "## Contrôle croisé" in response
    assert providers["codex"].calls[0][2] is Intent.MODIFY
    # L'examinateur aussi : sans intention modifiante, il n'a pas le droit
    # d'ecrire et son examen ne peut rien corriger.
    assert providers["claude"].calls[0][2] is Intent.MODIFY
    # Les memes droits que l'auteur, jamais plus.
    assert providers["claude"].execution_modes == ["acceptEdits"]
    assert "Do not invoke another AI provider" in providers["codex"].calls[0][0]


def test_a_read_only_request_gets_an_opinion_not_a_second_pass(tmp_path):
    """Rien n'a ete modifie : il n'y a pas de seconde passe a faire.

    Donner le droit d'ecrire a l'examinateur ferait modifier le depot par une
    demande qui avait justement exclu cela.
    """
    providers = {name: FakeProvider(name) for name in ("codex", "claude", "gemini", "copilot")}
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "analyse", Route(Intent.ANALYZE, Mode.REVIEW, "codex", "claude")
    )

    assert providers["claude"].calls[0][2] is Intent.ANALYZE
    assert "Do not modify files" in providers["claude"].calls[0][0]


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

    # Claude echoue : le relecteur de secours suit ses pairs declares, et
    # Gemini n'est plus en tete de liste.
    assert "reste disponible" in response
    assert "Codex" not in response.split("Contrôle croisé")[1]
    assert len(providers["codex"].calls) == 1
    assert len(providers["claude"].calls) == 1


def test_review_skips_provider_in_recent_cooldown(tmp_path):
    clear_cooldowns()
    record_result(
        ProviderResult(
            "antigravity", ["agy"], "", "quota", 1, 0.01,
            error_kind="quota",
        )
    )
    providers = {
        name: FakeProvider(name)
        for name in ("codex", "claude", "antigravity", "copilot")
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "antigravity")
    )

    # Antigravity est en cooldown : c'est Claude qui a examine, et le pied de
    # page le nomme.
    assert "Claude a examiné le travail" in response
    assert providers["antigravity"].calls == []
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
    # L'arbitre est un tiers, mais plus Gemini : il n'est plus prefere.
    assert response in {"copilot response", "gemini response"}
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
    assert "normal tools and investigation process" in (
        providers["codex"].calls[0][0]
    )
    assert all(
        call[3] == 900
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

    assert providers["codex"].calls
    assert providers["gemini"].calls
    # Gemini propose ici : il n'arbitre donc plus sa propre proposition, et la
    # synthèse revient au premier tiers disponible.
    assert response == "claude response"
    assert providers["claude"].calls


def test_consensus_rejects_incomplete_synthesis_and_falls_back(tmp_path):
    incomplete = (
        "Ce que je vais faire :\n\n"
        "1. Vérifier les résultats.\n"
        "2. Synthétiser les propositions.\n\n"
        "Je commence par lire les fichiers pertinents."
    )
    from joe.provider_registry import arbitration_order

    noms = ("codex", "claude", "gemini", "copilot")
    # Qui arbitre depend du registre, et ce classement bouge : Gemini l'etait
    # avant d'etre depreciee. On demande donc au registre plutot que de figer
    # un nom qui redeviendrait faux au prochain changement.
    arbitre = arbitration_order(proposers=("codex", "claude"), eligible=noms)[0]
    providers = {
        "codex": FakeProvider("codex", responses=[
            "codex proposal", "codex review", "## Résultat\n\nConsensus complet."
        ]),
        "claude": FakeProvider(
            "claude", responses=["claude proposal", "claude review"]
        ),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    providers[arbitre] = FakeProvider(arbitre, responses=[incomplete])
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "important",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex"),
    )

    assert response.startswith("> ⚠️ **Consensus dégradé**")
    assert "Consensus complet" in response
    assert len(providers[arbitre].calls) == 1
    assert len(providers["codex"].calls) == 3


def test_complete_synthesis_accepts_short_finished_answer():
    assert complete_synthesis("## Résultat\n\nAccord établi. Recommandation finale : lancer A.")


def test_consensus_relays_an_exhausted_participant(tmp_path):
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

    from joe.provider_registry import get_provider_spec

    # Le suppleant est le premier repli declare de Claude qui soit disponible
    # et qui ne soit pas deja l'autre proposant : Codex propose, il ne peut pas
    # remplacer son vis-a-vis.
    suppleant = next(
        name
        for name in get_provider_spec("claude").fallbacks
        if name in providers and name != "codex"
    )
    assert response.startswith("> ⚠️ **Consensus dégradé**")
    assert f"Claude indisponible, relais par {suppleant.capitalize()}" in response
    assert any(
        event.get("type") == "provider_fallback"
        and event.get("provider") == "claude"
        and event.get("fallback") == suppleant
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

    from joe.provider_registry import get_provider_spec

    suppleant = next(
        name
        for name in get_provider_spec("claude").fallbacks
        if name in providers and name != "codex"
    )
    assert response.startswith("> ⚠️ **Consensus dégradé**")
    assert f"Claude indisponible, relais par {suppleant.capitalize()}" in response


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


def test_the_examination_replaces_the_separate_correction_round(tmp_path):
    """Trois appels devenaient deux, et le rapport final change d'auteur.

    L'auteur rapportait un etat que l'examinateur venait de modifier : c'est
    desormais le dernier a avoir touche au code qui decrit le resultat.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    class CorrectingProvider(FakeProvider):
        def run(self, prompt, cwd, intent, timeout, **kwargs):
            (cwd / "correction.py").write_text("fixed\n", encoding="utf-8")
            return super().run(prompt, cwd, intent, timeout, **kwargs)

    providers = {
        "codex": FakeProvider("codex", responses=["implementation"]),
        "claude": CorrectingProvider(
            "claude",
            responses=["VERDICT: CORRECTIONS_APPLIED\ncorrected implementation"],
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

    assert "corrected implementation" in response
    # L'auteur n'est plus rappele pour une troisieme etape.
    assert len(providers["codex"].calls) == 1
    assert len(providers["claude"].calls) == 1
    assert not any(event.get("stage") == "correction" for event in events)
    assert "est repassé sur le travail" in response
    assert orchestrator.memory.previous_provider() == "claude"


def test_an_unsubstantiated_correction_marker_does_not_replace_the_report(tmp_path):
    """Un verdict textuel ne constitue pas une modification du dépôt."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    providers = {
        "codex": FakeProvider("codex", responses=["implementation"]),
        "claude": FakeProvider(
            "claude",
            responses=["VERDICT: CORRECTIONS_APPLIED\nclaimed correction"],
        ),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "large change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude")
    )

    assert response.startswith("implementation")
    assert "claimed correction" not in response
    assert orchestrator.memory.previous_provider() == "codex"


def test_a_material_correction_wins_even_without_the_expected_marker(tmp_path):
    """L'état du dépôt prime sur un marqueur omis par le modèle."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    class CorrectingProvider(FakeProvider):
        def run(self, prompt, cwd, intent, timeout, **kwargs):
            (cwd / "correction.py").write_text("fixed\n", encoding="utf-8")
            return super().run(prompt, cwd, intent, timeout, **kwargs)

    providers = {
        "codex": FakeProvider("codex", responses=["implementation"]),
        "claude": CorrectingProvider("claude", responses=["Final corrected state"]),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "large change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude")
    )

    assert response.startswith("Final corrected state")
    assert "est repassé sur le travail" in response


def test_a_corrected_review_drops_live_progress_before_the_verdict(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    class CorrectingProvider(FakeProvider):
        def run(self, prompt, cwd, intent, timeout, **kwargs):
            (cwd / "correction.py").write_text("fixed\n", encoding="utf-8")
            return super().run(prompt, cwd, intent, timeout, **kwargs)

    review = (
        "We’ll inspect the code and run the tests.\n\n"
        "We reproduced a bug. We’ll correct it now.\n\n"
        "VERDICT: CORRECTIONS_APPLIED\n\n"
        "Verified the final implementation. All tests pass."
    )
    providers = {
        "codex": FakeProvider("codex", responses=["implementation"]),
        "claude": CorrectingProvider("claude", responses=[review]),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "large change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude")
    )

    assert response.startswith("Verified the final implementation.")
    assert "We’ll inspect" not in response
    assert "We’ll correct" not in response
    assert "VERDICT:" not in response


def test_an_approved_examination_keeps_the_author_report(tmp_path):
    """Rien n'a ete corrige : le compte rendu de l'auteur decrit encore l'etat."""
    providers = {
        "codex": FakeProvider(
            "codex",
            responses=[
                "Implementation complete. The separate review by another "
                "provider has not run yet; Joe does that step next."
            ],
        ),
        "claude": FakeProvider("claude", responses=["VERDICT: APPROVED"]),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    response, _ = orchestrator.execute(
        "large change", Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude")
    )

    assert response.startswith("Implementation complete.")
    assert "has not run yet" not in response
    assert "does that step next" not in response
    assert "sans relever de correction" in response
    assert "Do not mention a future" in providers["codex"].calls[0][0]


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
    # On compte les runs simultanés plutôt que de chronométrer : un seuil de
    # durée échouait sur un runner chargé alors que l'exécution restait bien
    # parallèle. Deux runs en même temps prouvent le parallélisme, quelle que
    # soit la vitesse de la machine.
    import threading

    concurrency = {"active": 0, "peak": 0}
    lock = threading.Lock()

    class TrackingProvider(FakeProvider):
        def run(self, *args, **kwargs):
            with lock:
                concurrency["active"] += 1
                concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
            try:
                return super().run(*args, **kwargs)
            finally:
                with lock:
                    concurrency["active"] -= 1

    providers = {
        "codex": TrackingProvider("codex", delay=0.12),
        "claude": TrackingProvider("claude", delay=0.12),
        "gemini": TrackingProvider("gemini"),
        "copilot": TrackingProvider("copilot"),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "important",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex"),
    )

    assert concurrency["peak"] == 2


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


def test_only_the_step_that_answers_the_user_may_ask_a_question():
    """La question fermée n'a de sens que pour l'étape qui parle à l'utilisateur.

    Portée par le contexte commun, la consigne atteignait aussi les propositions
    et les relectures d'un consensus : chacune finissait sur une question que
    personne ne pouvait cliquer, et qui polluait le matériau de la synthèse.
    """
    from joe.orchestrator_workflows import examination_prompt, review_prompt
    from joe.prompt_language import question_rules

    marker = "joe:question"

    # Étapes internes : jamais de question.
    assert marker not in review_prompt("contexte", "candidat")

    # Étapes qui rendent la réponse : la consigne est présente.
    assert marker in examination_prompt("contexte", "implémentation", [])
    assert marker in question_rules("fr")


def test_every_stage_ends_on_the_requested_response_language():
    """La dernière consigne lue décide de la langue.

    Le contexte projet, l'historique et, en consensus, les propositions à
    synthétiser peuvent peser des milliers de mots dans l'autre langue : la
    consigne placée avant eux ne tenait pas.
    """
    from joe.orchestrator_workflows import examination_prompt, review_prompt
    from joe.prompt_language import response_language

    for prompt in (
        review_prompt("context", "candidate", "en"),
        examination_prompt("context", "implementation", [], "en"),
    ):
        assert prompt.endswith(response_language("en"))
        assert "Réponds à l'utilisateur" not in prompt

    # L'exemple de question suit lui aussi la langue : cité en français, il
    # produisait des boutons français dans une interface anglaise.
    assert "Where should we start?" in examination_prompt("c", "i", [], "en")


def test_an_english_run_carries_no_french_instruction_to_any_stage(tmp_path):
    """Une question posée en anglais recevait une réponse en français.

    La consigne de langue était noyée : l'organisation de la réponse, l'exemple
    de question et le « nous » demandé étaient écrits en français, et le modèle
    suivait la masse plutôt que la consigne.
    """
    from joe.prompt_language import response_language

    providers = {
        name: FakeProvider(name) for name in ("codex", "claude", "gemini")
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "Should we vendor our dependencies?",
        Route(Intent.ANALYZE, Mode.CONSENSUS, "codex", reviewer="claude"),
        language="en",
    )

    prompts = [call[0] for provider in providers.values() for call in provider.calls]
    assert prompts, "le consensus doit avoir appelé des fournisseurs"
    for prompt in prompts:
        assert prompt.endswith(response_language("en"))
        assert "Ce que je vais faire" not in prompt
        assert "Réponds à l'utilisateur" not in prompt


def test_the_collective_voice_names_the_pronoun_of_the_answer():
    """« Use 'nous' » dans un prompt anglais suffisait à faire basculer la réponse."""
    from joe.orchestrator_workflows import examination_prompt

    assert "'we'" in examination_prompt("context", "implementation", [], "en")
    assert "'nous'" in examination_prompt("contexte", "implémentation", [], "fr")


def test_the_examiner_is_told_which_files_the_author_touched(tmp_path):
    """Sans cette liste, l'examinateur part du rapport et cherche a l'aveugle.

    Le diff entier n'est pas passe a dessein : la CLI sait ouvrir les fichiers
    elle-meme, et le contexte est deja plafonne. Ce qui lui manque, c'est
    l'endroit ou regarder.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    class ProviderQuiEcrit(FakeProvider):
        def run(self, prompt, cwd, intent, timeout, **kwargs):
            (cwd / "fonctionnalite.py").write_text("a moitie\n", encoding="utf-8")
            return super().run(prompt, cwd, intent, timeout, **kwargs)

    providers = {
        "codex": ProviderQuiEcrit("codex"),
        "claude": FakeProvider("claude"),
        "gemini": FakeProvider("gemini"),
        "copilot": FakeProvider("copilot"),
    }
    orchestrator = Orchestrator(tmp_path, providers=providers)

    orchestrator.execute(
        "ajoute la fonctionnalite",
        Route(Intent.MODIFY, Mode.REVIEW, "codex", "claude"),
    )

    examen = providers["claude"].calls[0][0]
    assert "<CHANGED_FILES>" in examen
    assert "fonctionnalite.py" in examen
    # Et la consigne qui distingue cette etape d'une relecture : corriger.
    assert "Edit the files yourself" in examen
