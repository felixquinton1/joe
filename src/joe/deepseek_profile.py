"""Profil DeepSeek local ; reutilise Codex sans toucher au compte ordinaire."""

from __future__ import annotations

import json
import os
import stat
import math
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
from typing import Any, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from pathlib import Path

MODELS = ("deepseek-flash", "deepseek-v4-pro")
EFFORTS = ("low", "high", "max")
DEEPSEEK_API_BASE_URL = "https://api.deepseek.com"


def model_defaults(model: str | None, effort: str | None) -> tuple[str, str]:
    selected_model, selected_effort = model or MODELS[0], effort or "high"
    if selected_model not in MODELS or selected_effort not in EFFORTS:
        raise ValueError("Unsupported DeepSeek model or effort")
    return selected_model, selected_effort


def apply_codex_profile(command: list[str]) -> list[str]:
    """Ajoute le profil a la commande Codex existante, sans la reconstruire."""
    overrides: dict[str, str | bool] = {
        "model_provider": "deepseek",
        "model_catalog_json": str(profile_directory() / "models.json"),
        "model_providers.deepseek.name": "DeepSeek",
        "model_providers.deepseek.base_url": DEEPSEEK_API_BASE_URL,
        "model_providers.deepseek.env_key": "DEEPSEEK_API_KEY",
        "model_providers.deepseek.wire_api": "responses",
        "model_providers.deepseek.requires_openai_auth": False,
        "model_providers.deepseek.supports_websockets": False,
    }
    arguments = [argument for key, value in overrides.items()
                 for argument in ("--config", f"{key}={json.dumps(value)}")]
    position = command.index("exec")
    return [*command[:position], *arguments, *command[position:]]


def profile_directory() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "joe" / "deepseek" / "codex"


def _api_key() -> str:
    path = profile_directory().parent / "credentials.json"
    try:
        if path.is_symlink():
            raise ValueError("credentials must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("credentials must be a regular file")
            if os.name != "nt" and (
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()
            ):
                raise ValueError("credentials must be owned by this user with mode 0600")
            text = stream.read(16_385)
            if len(text) > 16_384:
                raise ValueError("credentials file is too large")
        payload = json.loads(text)
    except (OSError, ValueError):
        # Les exceptions JSON/OS peuvent retenir le document d'identifiants.
        # On ne les enchaine jamais dans une trace visible par l'utilisateur.
        raise ValueError("DeepSeek credentials.json requires a protected regular file (0600)") from None
    key = payload.get("api_key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not key.strip():
        raise ValueError("DeepSeek API key is not configured")
    key = key.strip()
    if not key.isascii() or any(ord(character) < 33 or ord(character) == 127 for character in key):
        raise ValueError("DeepSeek credentials require a single ASCII API token")
    return key


def setup_error() -> str | None:
    try:
        directory = profile_directory()
        if not (directory / "config.toml").is_file():
            return "DeepSeek requires an isolated Codex config.toml"
        if not (directory / "models.json").is_file():
            return "DeepSeek requires its local models.json catalogue"
        _api_key()
        payload = json.loads((directory / "models.json").read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return "DeepSeek models.json is invalid"
        if not any(
            isinstance(item, dict) and item.get("slug") in MODELS for item in payload["models"]
        ):
            return "DeepSeek models.json contains no supported model"
    except (OSError, ValueError):
        return "DeepSeek profile or protected API key requires setup (credentials.json: 0600)"
    return None


def process_environment(parent: dict[str, str]) -> dict[str, str]:
    environment = dict(parent)
    environment["CODEX_HOME"] = str(profile_directory())
    environment["DEEPSEEK_API_KEY"] = _api_key()
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        environment.pop(name, None)
    return environment


class DeepSeekBalance(TypedDict):
    balance_usd: float | None
    amount: float | None
    currency: str | None
    data_available: bool
    funds_sufficient: bool | None
    error_kind: str | None
    http_status: int | None


_BALANCE_URL = f"{DEEPSEEK_API_BASE_URL}/user/balance"


class _NoBalanceRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        # Ne jamais transmettre l'identifiant a un hote redirige ni en HTTP.
        return None


def fetch_deepseek_balance() -> DeepSeekBalance:
    """Lit les fonds du compte sans lancer de modele ; l'echec reste inconnu.

    ``data_available`` decrit la disponibilite des donnees de solde, tandis que
    ``funds_sufficient`` reprend l'indication distincte de l'API sur la
    suffisance des fonds pour les appels. Un solde non-USD conserve sa devise
    reelle et ne remplit jamais ``balance_usd``.
    """
    result: DeepSeekBalance = {
        "balance_usd": None, "amount": None, "currency": None,
        "data_available": False, "funds_sufficient": None,
        "error_kind": None, "http_status": None,
    }
    try:
        key = _api_key()
    except (OSError, ValueError):
        result["error_kind"] = "credentials"
        return result
    request = Request(_BALANCE_URL, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json",
    }, method="GET")
    try:
        with build_opener(_NoBalanceRedirect()).open(request, timeout=4) as response:
            raw = response.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("oversized balance response")
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("is_available"), bool):
            raise ValueError("invalid balance response")
        balances = data.get("balance_infos")
        if not isinstance(balances, list):
            raise ValueError("invalid balance entries")
        supported = [entry for entry in balances if isinstance(entry, dict)
                     and entry.get("currency") in ("USD", "CNY")]
        selected = next((entry for entry in supported if entry["currency"] == "USD"),
                        supported[0] if supported else None)
        if selected is None or not isinstance(selected.get("total_balance"), str):
            raise ValueError("no supported balance")
        value = Decimal(selected["total_balance"])
        amount = float(value)
        if not value.is_finite() or not math.isfinite(amount):
            raise ValueError("non-finite balance")
    except HTTPError as exc:
        result["error_kind"] = "authentication" if exc.code == 401 else "http_error"
        result["http_status"] = exc.code
        exc.close()
        return result
    except (URLError, OSError, HTTPException):
        result["error_kind"] = "network"
        return result
    except (ValueError, InvalidOperation, OverflowError):
        result["error_kind"] = "invalid_response"
        return result
    result.update({
        "balance_usd": amount if selected["currency"] == "USD" else None,
        "amount": amount, "currency": selected["currency"], "data_available": True,
        "funds_sufficient": data["is_available"],
    })
    return result
