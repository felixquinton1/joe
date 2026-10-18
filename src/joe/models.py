from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Mode(str, Enum):
    FAST = "fast"
    REVIEW = "review"
    CONSENSUS = "consensus"


class Intent(str, Enum):
    ANSWER = "answer"
    ANALYZE = "analyze"
    MODIFY = "modify"


@dataclass(frozen=True)
class Route:
    intent: Intent
    mode: Mode
    primary: str
    reviewer: str | None = None
    reason: str = ""
    explicit_reviewer: bool = False


@dataclass
class ProviderResult:
    provider: str
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    duration_seconds: float
    timed_out: bool = False
    error_kind: str | None = None
    error_message: str | None = None
    http_status: int | None = None
    retry_after_seconds: float | None = None
    usage: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return (
            self.returncode == 0
            and not self.timed_out
            and self.error_kind is None
        )

    def metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("stdout")
        data.pop("stderr")
        return data


@dataclass(frozen=True)
class ProviderFailure:
    error_message: str
    http_status: int | None = None
    retry_after_seconds: float | None = None
    error_kind: str | None = None
