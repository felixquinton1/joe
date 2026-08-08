from joe.autonomous_builder import build_campaign_payload, parse_autonomous_request


def test_explicit_autonomous_request_extracts_bounds_and_sources():
    request = (
        "Crée et lance une campagne Autonomous pour le challenge Demo "
        "https://example.org/challenge pendant 2 heures, 12 itérations. "
        "Les données sont dans DEMO_DATA_ROOT et aucune soumission automatique."
    )

    parsed = parse_autonomous_request(request)

    assert parsed is not None
    assert parsed["max_duration_seconds"] == 7200
    assert parsed["max_iterations"] == 12
    assert parsed["urls"] == ["https://example.org/challenge"]
    assert parsed["restricted_data"] is True


def test_question_about_autonomous_does_not_launch_campaign():
    assert parse_autonomous_request("Est-ce que le mode Autonomous est possible ?") is None


def test_generic_payload_creates_cross_platform_runner_and_safe_charter():
    request = "Lance une campagne Autonomous pour mon benchmark pendant 30 minutes"
    parsed = parse_autonomous_request(request)

    payload = build_campaign_payload(request, "conversation-1", parsed)

    assert payload["conversation_id"] == "conversation-1"
    assert payload["command"][1] == "autonomous_run.py"
    assert payload["resume_command"][-1] == "--resume"
    assert payload["max_duration_seconds"] == 1800
    assert "sans autorisation explicite" in payload["data_policy"]
