from joe.autonomous_builder import parse_autonomous_request


def test_natural_language_request_builds_overnight_schedule_and_log_loss():
    parsed = parse_autonomous_request(
        "Crée une campagne Autonomous cette nuit de 3h à 4h et minimise la log loss"
    )

    assert parsed is not None
    window = parsed["schedule"]["windows"][0]
    assert window["start"] == "03:00"
    assert window["end"] == "04:00"
    assert parsed["metric_name"] == "log_loss"
    assert parsed["metric_direction"] == "min"


def test_at_time_plus_duration_builds_schedule_window():
    parsed = parse_autonomous_request(
        "Lance un run Autonomous demain à 3h pendant 90 minutes"
    )

    assert parsed is not None
    window = parsed["schedule"]["windows"][0]
    assert window["start"] == "03:00"
    assert window["end"] == "04:30"
