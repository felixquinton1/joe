from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

ROLES = ("viewer", "operator", "maintainer")
_ROLE_LEVEL = {name: index for index, name in enumerate(ROLES)}


def auth_token_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "joe" / "auth-token"


def load_or_create_token(path: Path | None = None) -> str:
    target = path or auth_token_path()
    try:
        token = target.read_text().strip()
    except OSError:
        token = ""
    if token:
        return token
    target.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(token)
    temporary.chmod(0o600)
    os.replace(temporary, target)
    target.chmod(0o600)
    return token


@dataclass(frozen=True)
class LocalAuth:
    token: str = ""
    role: str = "maintainer"
    enabled: bool = False

    @classmethod
    def enabled_for(cls, role: str, path: Path | None = None) -> LocalAuth:
        if role not in ROLES:
            raise ValueError(f"invalid role: {role}")
        return cls(load_or_create_token(path), role, True)

    def accepts(self, candidate: str | None) -> bool:
        return bool(
            self.enabled
            and candidate
            and hmac.compare_digest(self.token, candidate)
        )

    def allows(self, required: str) -> bool:
        return _ROLE_LEVEL[self.role] >= _ROLE_LEVEL[required]


def required_role(method: str, path: str) -> str:
    if method == "GET":
        return "viewer"
    if path == "/api/projects" or path.startswith("/api/projects/"):
        return "maintainer"
    if path.endswith("/reject"):
        return "maintainer"
    return "operator"
