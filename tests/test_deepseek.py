"""DeepSeek stays a separate, explicitly selected provider using Codex tools."""

import json
import os
import sys

import pytest

from joe import capabilities
from joe.cli import parser
from joe.models import Intent, Mode, Route
from joe.provider_registry import arbitration_order, counterpart, get_provider_catalog
from joe.providers import Provider
from joe.router import Router
from joe.route_classifier import RouteClassification, apply_classification


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    base = tmp_path / "config" / "joe" / "deepseek"
    runtime = base / "codex"
    runtime.mkdir(parents=True)
    base.chmod(0o700)
    runtime.chmod(0o700)
    credential = base / "credentials.json"
    credential.write_text(json.dumps({"api_key": "test-deepseek-key"}))
    credential.chmod(0o600)
    (runtime / "models.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "deepseek-flash",
                        "display_name": "DeepSeek Flash",
                        "supported_reasoning_levels": [
                            {"effort": x} for x in ["low", "high", "max"]
                        ],
                    },
                    {
                        "slug": "deepseek-v4-pro",
                        "display_name": "DeepSeek Pro",
                        "supported_reasoning_levels": [
                            {"effort": x} for x in ["low", "high", "max"]
                        ],
                    },
                ]
            }
        )
    )
    (runtime / "config.toml").write_text(
        'model="deepseek-flash"\nmodel_reasoning_effort="high"\nmodel_provider="deepseek"\n'
        '[model_providers.deepseek]\nbase_url="https://api.deepseek.com"\n'
        'wire_api="responses"\nenv_key="DEEPSEEK_API_KEY"\nrequires_openai_auth=false\n'
    )
    monkeypatch.setattr(capabilities, "_cache", None)
    return runtime


def test_deepseek_has_own_cli_and_catalog_identity():
    args = parser().parse_args(["--agent", "deepseek", "hello"])
    assert args.agent == "deepseek"
    item = next(x for x in get_provider_catalog() if x["id"] == "deepseek")
    assert item["label"] == "DeepSeek"


def test_deepseek_catalog_does_not_use_openai_models(profile, monkeypatch):
    monkeypatch.setattr("joe.capabilities.resolve_executable", lambda name: "/bin/codex" if name == "deepseek" else None)
    catalog = capabilities.provider_capabilities(refresh=True)["deepseek"]
    assert catalog["available"]
    assert {x["id"] for x in catalog["models"]} == {"deepseek-flash", "deepseek-v4-pro"}
    assert catalog["efforts"] == ["low", "high", "max"]
    assert capabilities.provider_defaults("deepseek") == ("deepseek-flash", "high")


def test_deepseek_missing_key_is_not_ready(profile, monkeypatch):
    (profile.parent / "credentials.json").unlink()
    monkeypatch.setattr("joe.capabilities.resolve_executable", lambda name: "/bin/codex" if name == "deepseek" else None)
    catalog = capabilities.provider_capabilities(refresh=True)["deepseek"]
    assert not catalog["available"]
    assert catalog.get("setup_required")


def test_deepseek_uses_isolated_environment_and_codex_streams(profile, tmp_path, monkeypatch):
    script = tmp_path / "codex-fixture"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        f'assert os.environ["CODEX_HOME"] == {str(profile)!r}\n'
        'assert os.environ["DEEPSEEK_API_KEY"] == "test-deepseek-key"\n'
        'assert "OPENAI_API_KEY" not in os.environ\n'
        'assert sys.stdin.read() == "fixture prompt"\n'
        'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"DEEPSEEK_FIXTURE_OK"}}))\n'
    )

    class ProcessFixture(Provider):
        def command(self, *args, **kwargs):
            return [sys.executable, str(script)]

    monkeypatch.setenv("OPENAI_API_KEY", "ordinary-openai-key")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ordinary-codex"))
    before = dict(os.environ)
    result = ProcessFixture("deepseek", sys.executable).run(
        "fixture prompt",
        tmp_path,
        Intent.ANSWER,
        3,
        model="deepseek-flash",
        effort="high",
        execution_mode="read-only",
    )
    assert result.ok, result.stderr
    assert result.provider == "deepseek"
    assert result.stdout == "DEEPSEEK_FIXTURE_OK"
    assert "test-deepseek-key" not in " ".join(result.command) + result.stdout + result.stderr
    assert dict(os.environ) == before


def test_deepseek_is_never_automatic_primary_or_reviewer():
    router = Router(frozenset({"claude", "deepseek"}))
    for request in ["Fix the code", "What is DeepSeek?"]:
        route = router.route(request, forced_mode=Mode.REVIEW)
        assert route.primary == "claude"
        assert route.reviewer != "deepseek"
        assert route.mode is Mode.FAST
    route = router.route("Fix the code", forced_agent="deepseek", forced_mode=Mode.REVIEW)
    assert route.primary == "deepseek"
    assert route.reviewer == "claude"
    assert route.mode is Mode.REVIEW


def test_deepseek_is_not_automatic_arbitrator_or_counterpart():
    assert counterpart("codex", ("deepseek",)) is None
    assert "deepseek" not in arbitration_order(eligible=("codex", "deepseek"))


def test_classifier_cannot_select_paid_manual_provider():
    route = Route(Intent.ANSWER, Mode.FAST, "codex")
    decision = RouteClassification(
        "answer", "none", "simple", "fast", "deepseek", "standard", "low", 0.9, "claude"
    )
    assert apply_classification(route, decision).primary == "codex"


def test_manual_provider_is_not_announced_as_a_fallback(tmp_path):
    from joe.models import ProviderResult
    from joe.orchestrator import Orchestrator

    class Fixture:
        def __init__(self, name, success):
            self.name, self.success, self.calls = name, success, 0

        def run(self, *args, **kwargs):
            self.calls += 1
            return ProviderResult(
                self.name,
                [],
                "OK" if self.success else "",
                "unavailable" if not self.success else "",
                0 if self.success else 1,
                0,
                error_kind=None if self.success else "unavailable",
            )

    codex, deepseek, claude = (
        Fixture("codex", False),
        Fixture("deepseek", True),
        Fixture("claude", True),
    )
    orchestrator = Orchestrator(
        tmp_path, providers={"codex": codex, "deepseek": deepseek, "claude": claude}
    )
    orchestrator.memory.ensure()
    config = orchestrator.memory.config()
    config["fallbacks"]["codex"] = ["deepseek", "claude"]
    (orchestrator.memory.root / "config.yaml").write_text(json.dumps(config))
    events = []
    result = orchestrator._run_with_fallback(
        "codex", "hello", Intent.ANSWER, [], on_event=events.append
    )
    assert result.provider == "claude"
    assert deepseek.calls == 0
    assert all(e.get("fallback") != "deepseek" for e in events)


def test_unavailable_explicit_deepseek_does_not_silently_choose_another_model():
    route = Router(frozenset({"codex"})).route("hello", forced_agent="deepseek")
    assert route.primary == "deepseek"


def test_insecure_key_file_prevents_launch(profile, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission check")
    credential = profile.parent / "credentials.json"
    credential.chmod(0o644)
    result = Provider("deepseek", sys.executable).run("hello", tmp_path, Intent.ANSWER, 1)
    assert not result.ok
    assert result.error_kind == "unavailable"
    assert "test-deepseek-key" not in result.stderr


def test_web_api_publishes_separate_deepseek_model_choices(profile, tmp_path):
    import http.client
    from conftest import build_test_server

    server, thread = build_test_server(tmp_path, role="maintainer")
    client = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        client.request("GET", "/api/status")
        status = json.loads(client.getresponse().read())
        assert any(
            p["id"] == "deepseek" and p["label"] == "DeepSeek" for p in status["provider_catalog"]
        )
        client.request("GET", "/api/capabilities", headers={"Authorization": "Bearer test-token"})
        response = client.getresponse()
        assert response.status == 200
        data = json.loads(response.read())
        assert {p["id"] for p in data["deepseek"]["models"]} == {
            "deepseek-flash",
            "deepseek-v4-pro",
        }
        assert data["deepseek"]["manual_only"]
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_deepseek_command_keeps_its_model_and_codex_sandbox(profile, tmp_path):
    command = Provider("deepseek", "codex").command(
        "hello", tmp_path, Intent.ANSWER, execution_mode="read-only"
    )
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "deepseek-flash"
    assert 'model_reasoning_effort="high"' in command
    assert 'model_provider="deepseek"' in command
    assert 'model_providers.deepseek.base_url="https://api.deepseek.com"' in command
    assert "model_providers.deepseek.requires_openai_auth=false" in command
    assert command[-1] == "-"
    assert "test-deepseek-key" not in " ".join(command)


def test_automatic_compaction_does_not_spend_deepseek():
    from joe.web_runs import _compaction_provider

    assert _compaction_provider("", {"deepseek": object()}) == ""
    assert _compaction_provider("deepseek", {"deepseek": object()}) == "deepseek"


def test_general_live_doctor_does_not_call_manual_deepseek(profile, tmp_path, monkeypatch):
    from joe.doctor import doctor_report

    class ForbiddenProbe:
        def run(self, *args, **kwargs):
            raise AssertionError("DeepSeek must be selected explicitly")

    monkeypatch.setattr("joe.doctor.resolve_executable", lambda *args, **kwargs: sys.executable)
    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr("joe.doctor.usage_status", lambda **kwargs: [])
    report = doctor_report(tmp_path, live=True, providers={"deepseek": ForbiddenProbe()})
    assert report["providers"][0]["manual_only"]
    assert "live" not in report["providers"][0]


def test_web_decision_honours_flash_high_defaults(profile, tmp_path, monkeypatch):
    from joe.orchestrator import Orchestrator
    from joe.web_runs import RunManager

    monkeypatch.setattr("joe.web_runs.routing_usage_status", lambda: [])
    manager = RunManager(tmp_path)
    manager.orchestrator = Orchestrator(
        tmp_path, providers={"deepseek": Provider("deepseek", sys.executable)}
    )
    conversation = manager.conversations.create("main")
    decision = manager.decide("Fix the function", conversation["id"], agent="deepseek")
    assert decision.model == "deepseek-flash"
    assert decision.effort == "high"


def test_concurrent_codex_and_deepseek_keep_their_environments(profile, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    ordinary_home = tmp_path / "ordinary-codex"
    monkeypatch.setenv("CODEX_HOME", str(ordinary_home))
    monkeypatch.setenv("OPENAI_API_KEY", "ordinary-openai-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    script = tmp_path / "parallel-fixture.py"
    script.write_text(
        "import json,os,sys,time\nfrom pathlib import Path\n"
        "name=sys.argv[1]\n"
        f"base=Path({str(tmp_path)!r})\n"
        "(base/(name+'.ready')).touch()\n"
        "deadline=time.monotonic()+3\n"
        "while not all((base/(x+'.ready')).exists() for x in ('codex','deepseek')):\n"
        "    assert time.monotonic()<deadline, 'processes did not overlap'\n"
        "    time.sleep(.01)\n"
        "if name=='deepseek':\n"
        f"    assert os.environ['CODEX_HOME']=={str(profile)!r}\n"
        "    assert os.environ['DEEPSEEK_API_KEY']=='test-deepseek-key'\n"
        "    assert 'OPENAI_API_KEY' not in os.environ\n"
        "else:\n"
        f"    assert os.environ['CODEX_HOME']=={str(ordinary_home)!r}\n"
        "    assert os.environ['OPENAI_API_KEY']=='ordinary-openai-key'\n"
        "    assert 'DEEPSEEK_API_KEY' not in os.environ\n"
        "assert sys.stdin.read()=='parallel prompt'\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'ISOLATION_OK_'+name}}))\n",
        encoding="utf-8",
    )

    class ProcessFixture(Provider):
        def command(self, *args, **kwargs):
            return [sys.executable, str(script), self.name]

    before = dict(os.environ)

    def run(name):
        return ProcessFixture(name, sys.executable).run(
            "parallel prompt", tmp_path, Intent.ANSWER, 5
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("codex", "deepseek")))
    assert all(result.ok for result in results), [r.stderr for r in results]
    assert [r.stdout for r in results] == ["ISOLATION_OK_codex", "ISOLATION_OK_deepseek"]
    assert dict(os.environ) == before


def jsonl_provider(tmp_path, events, code=0, name="deepseek"):
    script = tmp_path / "provider-jsonl.py"
    script.write_text(
        "import sys\nsys.stdin.read()\n"
        + "\n".join(f"print({json.dumps(event)!r})" for event in events)
        + f"\nsys.exit({code})\n", encoding="utf-8",
    )

    class Fixture(Provider):
        def command(self, *args, **kwargs):
            return [sys.executable, str(script)]

    return Fixture(name, sys.executable)


@pytest.mark.parametrize("status,kind,reason", [
    (401, "authentication", "Invalid API key"),
    (402, "insufficient_balance", "Insufficient Balance"),
    (429, "rate_limit", "Too Many Requests"),
    (503, "unavailable", "Service Unavailable"),
])
def test_deepseek_preserves_structured_error_before_final_text(profile, tmp_path, status, kind, reason):
    message = f"unexpected status {status}: {reason} test-deepseek-key"
    result = jsonl_provider(tmp_path, [
        {"type": "error", "message": message},
        {"type": "turn.failed", "error": {"message": message}},
    ], 1).run("hello", tmp_path, Intent.ANSWER, 3)
    assert result.error_kind == kind
    assert result.http_status == status
    assert reason in result.error_message
    assert reason in result.stderr
    assert "test-deepseek-key" not in json.dumps(result.metadata()) + result.stderr
    assert result.metadata()["http_status"] == status


def test_deepseek_usage_survives_final_text_and_is_emitted_once(profile, tmp_path):
    from joe.orchestrator import Orchestrator
    from joe.web_runs import LiveRun, _run_summary

    provider = jsonl_provider(tmp_path, [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "FIXTURE_OK"}},
        {"type": "turn.completed", "usage": {
            "input_tokens": 100, "cached_input_tokens": 40,
            "output_tokens": 10, "reasoning_output_tokens": 3,
        }},
    ])
    orchestrator = Orchestrator(tmp_path, providers={"deepseek": provider})
    orchestrator.memory.ensure()
    events, results = [], []
    result = orchestrator._run_with_fallback(
        "deepseek", "hello", Intent.ANSWER, results,
        model="deepseek-flash", on_event=events.append,
    )
    assert result.stdout == "FIXTURE_OK"
    assert result.usage["input_tokens"] == 100
    assert result.usage["cache_read_tokens"] == 40
    assert result.usage["reasoning_tokens"] == 3
    assert result.usage["total_tokens"] == 110
    usages = [e for e in events if e["type"] == "usage"]
    assert len(usages) == 1
    assert usages[0]["cost_status"] == "estimated_api"
    run = LiveRun("usage-test", "hello", "conversation")
    for event in events:
        run.emit(event)
    usage = _run_summary(run)["attempts"][0]["usage"]
    assert usage["total_tokens"] == 110
    assert usage["cost_provider"] == "deepseek"


def test_failed_turn_keeps_completed_usage_and_error_without_raw_transcript(profile, tmp_path):
    result = jsonl_provider(tmp_path, [
        {"type": "turn.started"},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"type": "turn.failed", "error": {"message": "unexpected status 402 Insufficient Balance"}},
    ], 1).run("hello", tmp_path, Intent.ANSWER, 3, model="deepseek-flash")
    assert result.error_kind == "insufficient_balance"
    assert result.usage["total_tokens"] == 12
    assert '"type"' not in result.stdout


RECOVERED_STREAM = [
    {"type": "turn.started"},
    {"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion: stream closed before response.completed)"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "LOCAL_RECOVERY_OK"}},
    {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
]


@pytest.mark.parametrize("name", ["deepseek", "codex"])
def test_reconnect_notice_followed_by_completed_turn_succeeds(profile, tmp_path, name):
    result = jsonl_provider(tmp_path, RECOVERED_STREAM, name=name).run(
        "hello", tmp_path, Intent.ANSWER, 3, model="deepseek-flash" if name == "deepseek" else None,
    )
    assert result.ok
    assert result.error_kind is None
    assert result.error_message is None
    assert result.stdout == "LOCAL_RECOVERY_OK"
    assert result.usage["total_tokens"] == 12


def test_completed_turn_does_not_clear_error_when_process_fails(profile, tmp_path):
    from joe.codex_results import codex_failure

    output = "\n".join(json.dumps(event) for event in RECOVERED_STREAM)
    assert codex_failure(output) is None
    assert codex_failure(output, 1) is not None


@pytest.mark.parametrize("reconnect_after_failure", [False, True])
def test_completed_turn_does_not_clear_terminal_failure(reconnect_after_failure):
    from joe.codex_results import codex_failure

    events = [{"type": "turn.failed", "error": {
        "message": "HTTP 402: insufficient balance", "http_status": 402,
    }}]
    if reconnect_after_failure:
        events.append({"type": "error", "message": "Reconnecting... 1/5"})
    events.append({"type": "turn.completed"})
    failure = codex_failure("\n".join(json.dumps(event) for event in events), 0)
    assert failure is not None
    assert failure.http_status == 402
    assert failure.error_message == "HTTP 402: insufficient balance"


def test_timeout_preserves_prior_usage_without_retaining_jsonl(profile, tmp_path):
    provider = jsonl_provider(tmp_path, [
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
    ])
    script = tmp_path / "provider-jsonl.py"
    script.write_text(script.read_text().replace("sys.exit(0)", "import time; time.sleep(30)"))
    # Flush each event before the local process waits, as a streaming CLI does.
    script.write_text("import functools\nprint=functools.partial(print,flush=True)\n" + script.read_text())
    result = provider.run("hello", tmp_path, Intent.ANSWER, 1)
    assert result.error_kind == "timeout"
    assert result.usage["total_tokens"] == 12
    assert '"type"' not in result.stdout


def test_reported_retry_after_survives_structured_error(profile, tmp_path):
    error = {"message": "Rate limit reached", "status_code": 429, "retry_after_seconds": 15}
    result = jsonl_provider(tmp_path, [{"type": "turn.failed", "error": error}], 1).run(
        "hello", tmp_path, Intent.ANSWER, 3
    )
    assert result.http_status == 429
    assert result.retry_after_seconds == 15


def test_final_answer_json_cannot_forge_usage(profile, tmp_path):
    from joe.orchestrator import Orchestrator

    text = '{"type":"turn.completed","usage":{"input_tokens":1000000,"output_tokens":1000}}'
    provider = jsonl_provider(tmp_path, [
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
    ])
    orchestrator = Orchestrator(tmp_path, providers={"deepseek": provider})
    orchestrator.memory.ensure()
    events = []
    result = orchestrator._run_with_fallback("deepseek", "hello", Intent.ANSWER, [], on_event=events.append)
    assert result.stdout == text
    assert result.usage is None
    assert not any(event["type"] == "usage" for event in events)


def test_spawn_failure_never_exposes_profile_key_in_an_exception(profile, tmp_path, monkeypatch):
    def rejected_spawn(*args, **kwargs):
        raise OSError("fixture rejected env token: " + kwargs["env"]["DEEPSEEK_API_KEY"])

    monkeypatch.setattr("joe.providers.subprocess.Popen", rejected_spawn)
    result = Provider("deepseek", sys.executable).run("hello", tmp_path, Intent.ANSWER, 3)
    assert result.error_kind == "unavailable"
    assert "test-deepseek-key" not in result.stderr + json.dumps(result.metadata())


@pytest.mark.parametrize("encoding", ["json", "url"])
def test_encoded_key_is_redacted_from_stream_events_and_metadata(profile, tmp_path, encoding):
    from urllib.parse import quote
    secret = 'test-token-with-"quote\\slash'
    (profile.parent / "credentials.json").write_text(json.dumps({"api_key": secret}))
    emitted = quote(secret, safe="") if encoding == "url" else secret
    provider = jsonl_provider(tmp_path, [
        {"type": "item.started", "item": {"type": "command_execution", "command": "echo " + emitted}},
        {"type": "turn.failed", "error": {"message": "unexpected status 401: " + emitted}},
    ], 1)
    events = []
    result = provider.run("hello", tmp_path, Intent.ANSWER, 3,
                          on_stream=lambda stream, text: events.append((stream, text)))
    observed = [result.stdout, result.stderr, result.error_message or "", json.dumps(result.metadata())]
    observed.extend(json.loads(value).get("detail", "") for stream, value in events if stream == "activity")
    assert not any(secret in value or emitted in value for value in observed)
    assert "[REDACTED]" in result.error_message


@pytest.mark.skipif(os.name == "nt", reason="POSIX regular-file and mode checks")
@pytest.mark.parametrize("kind", ["mode-0640", "mode-0644", "mode-0660", "symlink", "fifo"])
def test_unsafe_credential_file_is_rejected_by_doctor_before_spawn(profile, tmp_path, monkeypatch, kind):
    from joe.doctor import doctor_report

    credential = profile.parent / "credentials.json"
    if kind.startswith("mode-"):
        credential.chmod(int(kind.removeprefix("mode-"), 8))
    elif kind == "symlink":
        target = profile.parent / "other.json"
        credential.rename(target)
        credential.symlink_to(target)
    else:
        credential.unlink()
        os.mkfifo(credential, 0o600)
        import subprocess

        completed = subprocess.run(
            [sys.executable, "-c", "from joe.deepseek_profile import setup_error; assert setup_error()"],
            capture_output=True, text=True, timeout=3,
        )
        assert completed.returncode == 0, completed.stderr
        return

    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("unsafe credentials must be rejected before starting a CLI")

    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr("joe.doctor.usage_status", lambda **kwargs: [])
    monkeypatch.setattr("joe.doctor.node_runtime", lambda: {"present": False})
    monkeypatch.setattr("joe.doctor.resolve_executable", lambda *args, **kwargs: sys.executable)
    monkeypatch.setattr("joe.providers.subprocess.Popen", forbidden_spawn)
    report = doctor_report(tmp_path, providers={"deepseek": Provider("deepseek", sys.executable)})
    assert not report["providers"][0]["installed"]
    assert report["providers"][0]["runtime_issue"]
    result = Provider("deepseek", sys.executable).run("hello", tmp_path, Intent.ANSWER, 3)
    assert not result.ok


def test_reviewer_effort_is_forwarded_only_when_its_model_declares_it(monkeypatch):
    import time

    from joe.orchestrator_workflows import stage_effort

    monkeypatch.setattr(capabilities, "_cache", (time.monotonic(), {
        "codex": {"models": [{"id": "gpt-fixture", "efforts": ["low", "high"]}], "efforts": []},
        "claude": {"models": [], "efforts": ["low", "high"]},
        "deepseek": {"models": [{"id": "deepseek-flash", "efforts": ["high"]}]},
    }))
    assert stage_effort("codex", "gpt-fixture", "high") == "high"
    assert stage_effort("codex", "gpt-fixture", "max") is None
    assert stage_effort("claude", "claude-opus-5-5", "high") == "high"
    assert stage_effort("deepseek", "deepseek-flash", "high") is None


@pytest.mark.parametrize("primary,reviewer", [("codex", "deepseek"), ("deepseek", "codex")])
def test_review_uses_separate_profile_defaults_and_keys(profile, tmp_path, monkeypatch, primary, reviewer):
    from joe.orchestrator import Orchestrator
    import time

    monkeypatch.setenv("OPENAI_API_KEY", "ordinary-openai-key")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ordinary-codex"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(capabilities, "_cache", (time.monotonic(), {
        "codex": {"models": [{"id": "gpt-fixture", "default_effort": "medium"}]},
        "deepseek": {"models": [{"id": "deepseek-flash", "default_effort": "high"}]},
    }))
    script = tmp_path / "review-jsonl.py"
    script.write_text(
        "import json,os,sys\n"
        "provider=sys.argv[1]\n"
        "if provider=='deepseek':\n"
        " assert os.environ['DEEPSEEK_API_KEY']=='test-deepseek-key'\n"
        " assert 'OPENAI_API_KEY' not in os.environ\n"
        f" assert os.environ['CODEX_HOME']=={str(profile)!r}\n"
        "else:\n"
        " assert os.environ['OPENAI_API_KEY']=='ordinary-openai-key'\n"
        " assert 'DEEPSEEK_API_KEY' not in os.environ\n"
        f" assert os.environ['CODEX_HOME']=={str(tmp_path / 'ordinary-codex')!r}\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Complete fixture report.'}}))\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':5}}))\n"
    )
    calls = []

    class ReviewFixture(Provider):
        def command(self, prompt, cwd, intent, model=None, effort=None, execution_mode=None):
            calls.append((self.name, model, effort, execution_mode))
            return [sys.executable, str(script), self.name]

    providers = {name: ReviewFixture(name, sys.executable) for name in (primary, reviewer)}
    events = []
    orchestrator = Orchestrator(tmp_path, providers=providers)
    route = orchestrator.router.route(
        "Implement fixture change", forced_agent=primary, forced_reviewer=reviewer,
    )
    before = dict(os.environ)
    _, log = orchestrator.execute(
        "Implement fixture change", route, model="deepseek-v4-pro" if primary == "deepseek" else "gpt-primary",
        effort="max" if primary == "deepseek" else "xhigh", execution_mode="read-only", on_event=events.append,
    )
    assert calls[0][0] == primary
    assert calls[1] == (reviewer, "deepseek-flash" if reviewer == "deepseek" else "gpt-fixture",
                        "high" if reviewer == "deepseek" else "medium", "read-only")
    assert dict(os.environ) == before
    assert [event["provider"] for event in events if event["type"] == "provider_start"] == [primary, reviewer]
    assert [event["provider"] for event in events if event["type"] == "usage"] == [primary, reviewer]
    assert "test-deepseek-key" not in log.read_text()
    assert "ordinary-openai-key" not in log.read_text()


@pytest.mark.parametrize("failure,kind", [(401, "authentication"), (402, "insufficient_balance"),
                                          ("cancel", "cancelled")])
def test_explicit_review_failure_does_not_complete_or_replace_the_reviewer(profile, tmp_path, failure, kind):
    import threading
    from joe.orchestrator import OrchestrationError, Orchestrator

    implementation = tmp_path / "implementation"
    implementation.mkdir()
    review = tmp_path / "review"
    review.mkdir()
    providers = {
        "codex": jsonl_provider(implementation, [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Implementation complete."}},
        ], name="codex"),
        "deepseek": jsonl_provider(review, [
            {"type": "turn.failed", "error": {"message": f"unexpected status {failure}: fixture failure"}},
        ], 1),
    }
    class ForbiddenFallback:
        def run(self, *args, **kwargs):
            pytest.fail("An explicit reviewer must not be replaced")

    providers["claude"] = ForbiddenFallback()
    orchestrator = Orchestrator(tmp_path, providers=providers)
    # A fallback is configured deliberately: an explicit reviewer must remain fixed.
    config = orchestrator.memory.config()
    config["fallbacks"]["deepseek"] = ["claude"]
    (orchestrator.memory.root / "config.yaml").write_text(json.dumps(config))
    route = orchestrator.router.route("Implement change", forced_agent="codex", forced_reviewer="deepseek")
    events = []
    cancel = threading.Event()

    def on_event(event):
        events.append(event)
        if failure == "cancel" and event["type"] == "provider_start" and event["provider"] == "deepseek":
            cancel.set()

    with pytest.raises(OrchestrationError):
        orchestrator.execute("Implement change", route, execution_mode="read-only",
                             cancel_event=cancel, on_event=on_event)
    assert [e["provider"] for e in events if e["type"] == "provider_start"] == ["codex", "deepseek"]
    terminal = [e for e in events if e["type"] == "provider_end"][-1]
    assert terminal["provider"] == "deepseek"
    assert terminal["error"] == kind
    assert not terminal["ok"]
    assert not [e for e in events if e["type"] == "workflow_update" and e.get("stage") == "review"
                and e.get("status") == "complete"]


def test_reviewer_balance_failure_after_recovered_executor_quota_is_not_retried(profile, tmp_path):
    from joe.models import ProviderResult
    from joe.orchestrator import OrchestrationError, Orchestrator
    from joe.provider_health import recent_failure
    from joe.web_runs import _quota_retry_allowed

    def fixed(name, kind, text=""):
        class Fixed:
            def run(self, *args, **kwargs):
                return ProviderResult(name, [name], text, "", 1 if kind else 0, 0.1,
                                      error_kind=kind,
                                      http_status=402 if kind == "insufficient_balance" else None)
        return Fixed()

    orchestrator = Orchestrator(tmp_path, providers={
        "codex": fixed("codex", "quota"),
        "claude": fixed("claude", None, "Implementation complete."),
        "deepseek": fixed("deepseek", "insufficient_balance"),
    })
    config = orchestrator.memory.config()
    config["fallbacks"]["codex"] = ["claude"]
    (orchestrator.memory.root / "config.yaml").write_text(json.dumps(config))
    route = orchestrator.router.route("Implement change", forced_agent="codex", forced_reviewer="deepseek")
    events = []
    with pytest.raises(OrchestrationError) as raised:
        orchestrator.execute("Implement change", route, execution_mode="read-only", on_event=events.append)
    ends = [(e["provider"], e["error"]) for e in events if e["type"] == "provider_end"]
    assert ends == [("codex", "quota"), ("claude", None), ("deepseek", "insufficient_balance")]
    assert ":quota" in str(raised.value)
    assert raised.value.failures == (("deepseek", "insufficient_balance"),)
    assert not _quota_retry_allowed(raised.value)
    assert recent_failure("deepseek")[0] == "insufficient_balance"


def test_quota_retry_requires_an_unresolved_subscription_quota():
    from joe.orchestrator import OrchestrationError
    from joe.web_runs import _quota_retry_allowed

    assert _quota_retry_allowed(OrchestrationError("x", (("codex", "quota"), ("claude", "authentication"))))
    assert not _quota_retry_allowed(OrchestrationError("x", (("codex", "quota"), ("deepseek", "insufficient_balance"))))
    assert not _quota_retry_allowed(OrchestrationError("x", (("deepseek", "quota"),)))
    assert not _quota_retry_allowed(OrchestrationError("All providers failed (codex:quota)", ()))
    assert _quota_retry_allowed(OrchestrationError("All providers failed (codex:quota)"))
    assert not _quota_retry_allowed(RuntimeError("codex:quota"))
