"""Balance reads use the existing Python HTTP client and synthetic credentials."""
import io
import os
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from joe import deepseek_profile


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = tmp_path / "joe" / "deepseek"
    directory.mkdir(parents=True)
    path = directory / "credentials.json"
    path.write_text(json.dumps({"api_key": "synthetic-balance-token"}))
    path.chmod(0o600)
    return path


def respond(monkeypatch, payload):
    calls = []

    def open_request(request, *, timeout):
        calls.append((request, timeout))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(deepseek_profile, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    return calls


def payload(amount="4.50", currency="USD", available=True):
    return {"is_available": available, "balance_infos": [{
        "currency": currency, "total_balance": amount,
        "granted_balance": "1.00", "topped_up_balance": "3.50",
    }]}


def test_balance_uses_protected_key_official_endpoint_and_total(credentials, monkeypatch):
    calls = respond(monkeypatch, payload())
    result = deepseek_profile.fetch_deepseek_balance()
    assert result["balance_usd"] == 4.5
    assert result["amount"] == 4.5
    assert result["currency"] == "USD"
    assert result["data_available"] is True
    assert result["funds_sufficient"] is True
    request, timeout = calls[0]
    assert request.full_url == "https://api.deepseek.com/user/balance"
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer synthetic-balance-token"
    assert 0 < timeout <= 5
    assert "synthetic-balance-token" not in json.dumps(result)


@pytest.mark.parametrize("amount,is_available", [("0.00", False), ("-0.01", False)])
def test_zero_or_negative_balance_is_real_data_not_a_network_failure(credentials, monkeypatch, amount, is_available):
    respond(monkeypatch, payload(amount, available=is_available))
    result = deepseek_profile.fetch_deepseek_balance()
    assert result["data_available"] is True
    assert result["balance_usd"] == float(amount)
    assert result["funds_sufficient"] is False


def test_cny_is_not_labelled_or_converted_to_usd(credentials, monkeypatch):
    respond(monkeypatch, payload("12.34", "CNY"))
    result = deepseek_profile.fetch_deepseek_balance()
    assert result["data_available"]
    assert result["currency"] == "CNY"
    assert result["amount"] == 12.34
    assert result["balance_usd"] is None


def test_usd_is_preferred_without_summing_currencies(credentials, monkeypatch):
    response = payload("10.00", "CNY")
    response["balance_infos"] += payload()["balance_infos"]
    respond(monkeypatch, response)
    assert deepseek_profile.fetch_deepseek_balance()["balance_usd"] == 4.5


@pytest.mark.parametrize("error,kind,status", [
    (HTTPError("https://api.deepseek.com/user/balance", 401, "synthetic-balance-token", {}, None), "authentication", 401),
    (HTTPError("https://api.deepseek.com/user/balance", 503, "synthetic-balance-token", {}, None), "http_error", 503),
    (URLError("synthetic-balance-token"), "network", None),
    (TimeoutError("synthetic-balance-token"), "network", None),
])
def test_balance_failure_is_a_safe_unknown_value(credentials, monkeypatch, error, kind, status):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(deepseek_profile, "build_opener", lambda *args: SimpleNamespace(open=fail))
    result = deepseek_profile.fetch_deepseek_balance()
    assert result["balance_usd"] is None
    assert result["amount"] is None
    assert result["data_available"] is False
    assert result["error_kind"] == kind
    assert result["http_status"] == status
    assert "synthetic-balance-token" not in json.dumps(result)


@pytest.mark.parametrize("response", [
    {}, [], {"is_available": "yes", "balance_infos": []}, payload("NaN"), payload("Infinity"),
    payload("1e9999"), payload("invalid"), payload("1", "EUR"),
])
def test_malformed_balance_is_not_zero(credentials, monkeypatch, response):
    respond(monkeypatch, response)
    result = deepseek_profile.fetch_deepseek_balance()
    assert result["data_available"] is False
    assert result["balance_usd"] is None
    assert result["error_kind"] == "invalid_response"


def test_missing_credentials_do_not_make_a_request(credentials, monkeypatch):
    credentials.unlink()
    monkeypatch.setattr(deepseek_profile, "build_opener", lambda *args: pytest.fail("must not request without credentials"))
    result = deepseek_profile.fetch_deepseek_balance()
    assert not result["data_available"]
    assert result["error_kind"] == "credentials"


@pytest.mark.skipif(os.name == "nt", reason='POSIX file permissions')
def test_insecure_credentials_do_not_make_a_request(credentials, monkeypatch):
    credentials.chmod(0o644)
    monkeypatch.setattr(deepseek_profile, "build_opener", lambda *args: pytest.fail("must not request with insecure credentials"))
    assert deepseek_profile.fetch_deepseek_balance()["error_kind"] == "credentials"


@pytest.mark.parametrize("status", [200, 401, 302])
def test_real_http_client_handles_local_responses_without_following_redirects(credentials, monkeypatch, status):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/credential-must-not-reach-this-path")
            self.end_headers()
            self.wfile.write(json.dumps(payload()).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(deepseek_profile, "_BALANCE_URL", f"http://127.0.0.1:{server.server_port}/user/balance")
    try:
        result = deepseek_profile.fetch_deepseek_balance()
        assert result["data_available"] is (status == 200)
        assert received == [("/user/balance", "Bearer synthetic-balance-token")]
        if status != 200:
            assert result["http_status"] == status
            assert result["balance_usd"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_balance_refresh_uses_usage_cache_and_does_not_change_provider_readiness(credentials, monkeypatch):
    from joe import usage

    calls = []
    unknown = {"balance_usd": None, "amount": None, "currency": None,
               "data_available": False, "funds_sufficient": None, "error_kind": "network", "http_status": None}
    monkeypatch.setattr(deepseek_profile, "setup_error", lambda: None)
    monkeypatch.setattr("joe.providers.resolve_executable", lambda name: "/fixture/codex")
    monkeypatch.setattr(deepseek_profile, "fetch_deepseek_balance", lambda: calls.append(1) or dict(unknown))
    monkeypatch.setattr(usage, "_cache", None)
    monkeypatch.setattr(usage, "_last_available", {})
    for name in ["_codex_status", "_claude_status", "_refresh_claude_status"]:
        monkeypatch.setattr(usage, name, lambda: {"provider": "fixture", "available": False, "windows": []})
    for force in [False, False, True]:
        status = next(p for p in usage.usage_status(force=force) if p["provider"] == "deepseek")
        assert status["available"] is True
        assert status["availability_state"] == "quota_unknown"
        assert status["balance"]["data_available"] is False
        assert status["windows"] == []
    assert len(calls) == 2


def test_account_balance_does_not_depend_on_codex_installation(credentials, monkeypatch):
    from joe import usage

    calls = respond(monkeypatch, payload())
    monkeypatch.setattr("joe.providers.resolve_executable", lambda name: None)
    status = usage._deepseek_status()
    assert not status["available"]
    assert status["balance"]["balance_usd"] == 4.5
    assert status["quota_display"] == "token_billing"
    assert len(calls) == 1
