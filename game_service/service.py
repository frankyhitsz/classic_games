"""Storage-neutral contracts shared by pygame and optional adapters."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

from .catalog import GAME_BY_ID


class StorageErrorKind(str, Enum):
    BUSY = "busy"
    FULL = "full"
    READ_ONLY = "read_only"
    IO_ERROR = "io_error"
    CANT_OPEN = "cant_open"
    CORRUPT = "corrupt"
    CONSTRAINT = "constraint"
    SCHEMA_REPAIR_REQUIRED = "schema_repair_required"
    INTERRUPTED = "interrupted"
    INVALID_MUTATION = "invalid_mutation"
    INTERNAL = "internal"


class SaveState(str, Enum):
    SAVING = "saving"
    COMMITTED = "committed"
    DURABLE_PENDING = "durable_pending"
    RECOVERY_REQUIRED = "recovery_required"
    QUARANTINED = "quarantined"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True)
class SaveEvent:
    request_id: str
    attempt_uuid: str
    revision: int
    state: SaveState
    result: dict

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


@dataclass
class AttemptContext:
    """Dimensions fixed for the lifetime of one local game run."""

    game_id: str
    profile_id: str
    mode: str
    ruleset_version: str
    status: str
    attempt_uuid: str = field(default_factory=lambda: uuid.uuid4().hex)
    revision: int = 0

    @classmethod
    def for_game(cls, game_id: str, player: str, *,
                 profile_id: Optional[str] = None, mode: str = "classic",
                 status: str = "completed") -> "AttemptContext":
        if profile_id is None:
            profile_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"classic-games-local-profile:{player}").hex
        return cls(
            game_id=game_id,
            profile_id=profile_id,
            mode=mode,
            ruleset_version=GAME_BY_ID[game_id].ruleset_version,
            status=status,
        )

    def as_submit_kwargs(self) -> dict[str, str]:
        data = asdict(self)
        return {key: data[key] for key in (
            "profile_id", "mode", "ruleset_version", "status")}

    def next_revision(self) -> int:
        self.revision += 1
        return self.revision


@dataclass(frozen=True)
class StorageStatus:
    """A truthful snapshot of the local records subsystem."""

    ok: bool
    readable: bool
    writable: bool
    outbox_writable: bool
    error_code: Optional[str] = None
    retryable: bool = False
    recovery_notice: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataResult:
    """Typed result for callers that need to distinguish empty from failed."""

    ok: bool
    data: Any
    error_code: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False


def parse_score_response(result) -> tuple[Optional[int], Optional[str]]:
    """Validate the acknowledgement returned by either storage adapter."""
    if not isinstance(result, dict):
        return None, "保存服务没有返回有效结果"
    if result.get("ok") is not True:
        return None, str(result.get("error") or result.get("code")
                         or "保存失败")
    row_id = result.get("id")
    if type(row_id) is not int or row_id <= 0:
        return None, "保存服务返回了无效记录编号"
    return row_id, None


class GameDataService(Protocol):
    """Small interface consumed by games, independent of SQLite or HTTP."""

    is_local: bool
    pending_saves_are_durable: bool

    def health_async(self): ...

    def leaderboard_async(self, game_id: str, limit: int = 10): ...

    def recent_async(self, limit: int = 20): ...

    def submit_score_reliable_async(
            self, game_id: str, player: str, score: int, *, extra=None,
            replace: bool = False, submission_id: Optional[int] = None,
            request_id: Optional[str] = None,
            attempt_uuid: Optional[str] = None,
            revision: Optional[int] = None, profile_id: Optional[str] = None,
            mode: str = "classic", ruleset_version: Optional[str] = None,
            status: str = "completed"): ...

    def failed_save_count(self) -> int: ...

    def retry_failed_saves(self): ...

    def poll_pending_saves(self, interval_seconds: float = 2.0) -> int: ...

    def poll_save_events(self) -> list[SaveEvent]: ...

    def get_save_status(self, request_id: str) -> Optional[SaveEvent]: ...

    def drain(self, timeout: Optional[float] = None) -> bool: ...

    def close(self) -> None: ...
