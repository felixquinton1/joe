"""Semantique de l'API DeepSeek, distincte du transport JSON de Codex."""
from __future__ import annotations

from dataclasses import replace

from .models import ProviderFailure


def classify_failure(failure: ProviderFailure) -> ProviderFailure:
    kind = {
        401: "authentication", 402: "insufficient_balance",
        429: "rate_limit", 503: "unavailable",
    }.get(failure.http_status) if failure.http_status is not None else None
    if kind is None:
        message = failure.error_message.lower()
        kind = next((value for marker, value in (
            ("insufficient balance", "insufficient_balance"),
            ("invalid api key", "authentication"), ("rate limit", "rate_limit"),
            ("error sending request", "unavailable"), ("connection", "unavailable"),
        ) if marker in message), "process")
    return replace(failure, error_kind=kind)
