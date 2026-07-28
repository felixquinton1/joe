from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, BinaryIO, Mapping

MAX_JSON_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class RequestBodyError(ValueError):
    message: str
    status: HTTPStatus = HTTPStatus.BAD_REQUEST

    def __str__(self) -> str:
        return self.message


def read_json_body(
    headers: Mapping[str, str],
    stream: BinaryIO,
    *,
    allow_empty: bool = False,
    max_bytes: int = MAX_JSON_BODY_BYTES,
) -> dict[str, Any]:
    raw_length = headers.get("Content-Length")
    if raw_length is None:
        if allow_empty:
            return {}
        raise RequestBodyError(
            "Content-Length requis.",
            HTTPStatus.LENGTH_REQUIRED,
        )
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise RequestBodyError("Content-Length invalide.") from exc
    if length < 0:
        raise RequestBodyError("Content-Length invalide.")
    if length > max_bytes:
        raise RequestBodyError(
            f"Corps JSON trop volumineux (maximum {max_bytes} octets).",
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )
    if length == 0:
        if allow_empty:
            return {}
        raise RequestBodyError("Corps JSON requis.")
    raw = stream.read(length)
    if len(raw) != length:
        raise RequestBodyError("Corps JSON incomplet.")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestBodyError("JSON invalide.") from exc
    if not isinstance(payload, dict):
        raise RequestBodyError("Le corps JSON doit être un objet.")
    return payload


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]")
    if normalized.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_bind(host: str, *, allow_remote: bool = False) -> None:
    if not is_loopback_host(host) and not allow_remote:
        raise ValueError(
            "Bind non local refusé sans --allow-remote. "
            "Joe n’authentifie pas son API HTTP."
        )
