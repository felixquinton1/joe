from joe.maintenance import format_audit, provider_audit, update_request


def test_provider_audit_detects_version_change(monkeypatch):
    monkeypatch.setattr("joe.maintenance.KNOWN_VERSIONS", {"codex": "1.0.0"})
    monkeypatch.setattr("joe.maintenance._version", lambda provider: "1.1.0")

    audit = provider_audit()

    assert audit[0]["provider"] == "codex"
    assert audit[0]["changed"] is True
    assert "nouvelle version" in format_audit(audit)


def test_update_request_requires_tests_and_version_update():
    results = [
        {
            "provider": "claude",
            "available": True,
            "known_version": "1.0.0",
            "current_version": "1.1.0",
            "changed": True,
        }
    ]

    request = update_request(results, force=False)

    assert "Claude" not in request
    assert "claude 1.1.0" in request
    assert "KNOWN_VERSIONS" in request
    assert "tests" in request
