from __future__ import annotations

import json
import re
from typing import Any

_MILLION = 1_000_000
# API reference rates are deliberately labelled as estimates. They must not be
# presented as the user's subscription bill.
_CLAUDE_RATES = {
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
    "fable": (10.0, 50.0),
}


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and value >= 0 else 0


def _usage_object(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    message = item.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return message["usage"], message.get("model") if isinstance(message.get("model"), str) else None
    usage = item.get("usage")
    if isinstance(usage, dict):
        model = item.get("model") if isinstance(item.get("model"), str) else None
        return usage, model
    return None, None


def _model_rates(model: str | None) -> tuple[float, float] | None:
    normalized = (model or "").lower()
    for family, rates in _CLAUDE_RATES.items():
        if family in normalized:
            return rates
    return None


def _cost(tokens: dict[str, int], model: str | None, direct: Any) -> tuple[float | None, str]:
    if isinstance(direct, (int, float)) and direct >= 0:
        return float(direct), "reported"
    rates = _model_rates(model)
    if not rates:
        return None, "unknown"
    input_rate, output_rate = rates
    return (
        (tokens["input_tokens"] / _MILLION) * input_rate
        + (tokens["output_tokens"] / _MILLION) * output_rate
        + (tokens["cache_read_tokens"] / _MILLION) * input_rate * 0.1
        + (tokens["cache_creation_tokens"] / _MILLION) * input_rate * 1.25,
        "estimated_api",
    )


def parse_provider_usage(provider: str, output: str, model: str | None = None) -> dict[str, Any] | None:
    """Extract usage from JSONL without treating ordinary prose as usage."""
    totals = {key: 0 for key in (
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "reasoning_tokens"
    )}
    models: set[str] = set()
    direct_cost: float | None = None
    found = False
    for line in output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            item = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(item, dict):
            continue
        usage, item_model = _usage_object(item)
        if usage is None:
            continue
        found = True
        if item_model:
            models.add(item_model)
        aliases = {
            "input_tokens": ("input_tokens", "inputTokens"),
            "output_tokens": ("output_tokens", "outputTokens"),
            "cache_read_tokens": ("cache_read_input_tokens", "cacheReadInputTokens"),
            "cache_creation_tokens": ("cache_creation_input_tokens", "cacheCreationInputTokens"),
            "reasoning_tokens": ("reasoning_tokens", "reasoningTokens"),
        }
        for target, keys in aliases.items():
            totals[target] += max((_number(usage.get(key)) for key in keys), default=0)
        for key in ("cost_usd", "costUsd", "total_cost_usd", "totalCostUsd"):
            if isinstance(item.get(key), (int, float)):
                direct_cost = float(item[key])
    if not found:
        return None
    resolved_model = model or (sorted(models)[0] if models else None)
    cost, cost_status = _cost(totals, resolved_model, direct_cost)
    return {
        "provider": provider,
        "model": resolved_model,
        **totals,
        "cost_usd": round(cost, 8) if cost is not None else None,
        "cost_status": cost_status,
        "source": "provider_jsonl",
    }
