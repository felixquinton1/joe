from joe.usage_tracking import parse_provider_usage
import pytest


def test_parse_claude_jsonl_usage_and_estimated_model_cost():
    result = parse_provider_usage(
        "claude",
        '{"message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":1000000,"output_tokens":1000,"cache_read_input_tokens":10000}}}',
    )
    assert result["input_tokens"] == 1_000_000
    assert result["output_tokens"] == 1_000
    assert result["cost_status"] == "estimated_api"
    assert result["cost_usd"] is not None


def test_parse_antigravity_claude_usage_keeps_the_estimated_api_cost():
    result = parse_provider_usage(
        "antigravity",
        '{"message":{"model":"claude-opus-4-6-thinking","usage":{"input_tokens":1000000,"output_tokens":1000}}}',
    )
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


def test_deepseek_cost_uses_its_own_rates_and_does_not_charge_cached_input_twice():
    result = parse_provider_usage("deepseek", '{"type":"turn.completed","usage":{"input_tokens":1000000,"cached_input_tokens":400000,"output_tokens":1000,"reasoning_output_tokens":300}}', "deepseek-flash")
    assert result["total_tokens"] == 1_001_000
    assert result["cache_read_tokens"] == 400_000
    assert result["reasoning_tokens"] == 300
    # Peak estimate: 600000 * 0.3/M + 400000 * 0.006/M + 1000 * 1.2/M.
    assert result["cost_usd"] == pytest.approx(0.1836)
    assert result["cost_status"] == "estimated_api"
    assert result["cost_provider"] == "deepseek"
    assert result["cost_rate_basis"] == "peak"


def test_codex_usage_is_not_priced_as_claude_and_duplicate_terminal_event_is_ignored():
    event = '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":10}}'
    result = parse_provider_usage("codex", event + "\n" + event, "claude-sonnet")
    assert result["input_tokens"] == 100
    assert result["total_tokens"] == 110
    assert result["cost_usd"] is None


def test_deepseek_unknown_model_has_unknown_cost():
    result = parse_provider_usage("deepseek", '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":10}}', "claude-sonnet")
    assert result["cost_usd"] is None


def test_deepseek_missing_model_is_not_priced_as_flash():
    result = parse_provider_usage("deepseek", '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":10}}')
    assert result["model"] == "unknown"
    assert result["cost_usd"] is None
    assert result["cost_status"] == "unknown"


def test_autonomous_token_budget_counts_inclusive_usage_once():
    from joe.autonomous_analysis import model_usage

    usage = {"input_tokens": 100, "cache_read_tokens": 40, "output_tokens": 10,
             "reasoning_tokens": 3, "total_tokens": 110}
    campaign = {"history": [{"kind": "agent_step", "status": "completed",
                              "attempts": [{"provider": "deepseek", "usage": usage}]}]}
    assert model_usage(campaign)["known_tokens"] == 110


def test_conversation_statistics_count_inclusive_usage_once(tmp_path):
    from joe.conversations import ConversationStore

    store = ConversationStore(tmp_path, tmp_path / "runs")
    conversation = store.create()
    store.append_message(conversation["id"], "assistant", "OK", "run", run_summary={
        "attempts": [{"provider": "deepseek", "status": "complete", "usage": {
            "input_tokens": 100, "cache_read_tokens": 40, "output_tokens": 10,
            "reasoning_tokens": 3, "total_tokens": 110,
            "cost_status": "estimated_api", "cost_usd": 0.00003,
        }}],
    })
    assert store.analytics()["runs"][0]["tokens"] == 110
