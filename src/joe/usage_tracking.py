from __future__ import annotations

import json
from typing import Any

from .provider_registry import provider_backend

_MILLION = 1_000_000
# API reference rates are deliberately labelled as estimates. They must not be
# presented as the user's subscription bill.
_CLAUDE_RATES = {
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
    "fable": (10.0, 50.0),
}
# Tarifs de pointe conservateurs, USD / million de tokens ; jamais une facture.
# https://api-docs.deepseek.com/quick_start/pricing/ (verifie le 2026-10-18).
_DEEPSEEK_RATES = {
    "deepseek-flash": (0.006, 0.3, 1.2),
    "deepseek-v4-pro": (0.044, 1.32, 3.96),
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
    codex_backend = provider_backend(provider) == "codex"
    completed_turn = False
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
        if codex_backend:
            if item.get("type") == "turn.started":
                completed_turn = False
            if item.get("type") != "turn.completed" or completed_turn:
                continue
            completed_turn = True
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
        if codex_backend:
            aliases["cache_read_tokens"] = ("cached_input_tokens", "cache_read_input_tokens")
            aliases["cache_creation_tokens"] = ("cache_write_input_tokens", "cache_creation_input_tokens")
            aliases["reasoning_tokens"] = ("reasoning_output_tokens", "reasoning_tokens")
        for target, keys in aliases.items():
            totals[target] += max((_number(usage.get(key)) for key in keys), default=0)
        for key in ("cost_usd", "costUsd", "total_cost_usd", "totalCostUsd"):
            if isinstance(item.get(key), (int, float)):
                direct_cost = float(item[key])
    if not found:
        return None
    resolved_model = model or (sorted(models)[0] if models else (
        "unknown" if provider == "deepseek" else None
    ))
    cost, cost_status = None, "unknown"
    if provider in {"claude", "antigravity"}:
        cost, cost_status = _cost(totals, resolved_model, direct_cost)
    elif provider == "deepseek" and resolved_model in _DEEPSEEK_RATES:
        hit, miss, output_rate = _DEEPSEEK_RATES[resolved_model]
        cached = min(totals["cache_read_tokens"], totals["input_tokens"])
        cost = (cached * hit + (totals["input_tokens"] - cached) * miss
                + totals["output_tokens"] * output_rate) / _MILLION
        cost_status = "estimated_api"
    elif not codex_backend:
        cost, cost_status = _cost(totals, None, direct_cost)
    result = {
        "provider": provider,
        "model": resolved_model,
        **totals,
        "cost_usd": round(cost, 8) if cost is not None else None,
        "cost_status": cost_status,
        "source": "provider_jsonl",
    }
    result["total_tokens"] = (
        totals["input_tokens"] + totals["output_tokens"]
        if codex_backend else sum(totals.values())
    )
    if provider == "deepseek":
        result.update(cost_provider="deepseek", cost_rate_basis="peak", cost_rates_as_of="2026-10-18")
    return result


def usage_token_count(usage: dict[str, Any]) -> int:
    """Compter une fois l'entree/sortie inclusive de Codex, avec les anciens resumes."""
    if isinstance(usage.get("total_tokens"), (int, float)):
        return _number(usage["total_tokens"])
    return sum(_number(usage.get(key)) for key in (
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_creation_tokens", "reasoning_tokens",
    ))
