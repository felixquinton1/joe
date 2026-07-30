from joe.usage_tracking import parse_provider_usage


def test_parse_claude_jsonl_usage_and_estimated_model_cost():
    result = parse_provider_usage(
        "claude",
        '{"message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":1000000,"output_tokens":1000,"cache_read_input_tokens":10000}}}',
    )
    assert result["input_tokens"] == 1_000_000
    assert result["output_tokens"] == 1_000
    assert result["cost_status"] == "estimated_api"
    assert result["cost_usd"] is not None


def test_usage_unknown_provider_keeps_cost_unknown():
    result = parse_provider_usage(
        "copilot",
        '{"usage":{"inputTokens":12,"outputTokens":4},"model":"unknown"}',
    )
    assert result["input_tokens"] == 12
    assert result["output_tokens"] == 4
    assert result["cost_usd"] is None
    assert result["cost_status"] == "unknown"
