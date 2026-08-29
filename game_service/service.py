"""Storage-neutral contracts shared by pygame and optional adapters."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

from .catalog import GAME_BY_ID
from .profile import ProfileIdentity


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
    SUPERSEDED = "superseded"
    DURABLE_PENDING = "durable_pending"
    NON_DURABLE_PENDING = "non_durable_pending"
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


@dataclass(frozen=True)
class LocalStateEvent:
    key: str
    kind: str
    logical_revision: int
    state: SaveState
    result: dict
    operation_id: str = ""
    payload_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


class SlotLoadStatus(str, Enum):
    LOADED = "loaded"
    NO_SLOT = "no_slot"
    TEMPORARY_FAILURE = "temporary_failure"
    PROFILE_PENDING = "profile_pending"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class SlotLoadResult:
    status: SlotLoadStatus
    slot: Optional[dict] = None
    error_code: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False


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
            profile_id = ProfileIdentity.from_legacy_name(player).profile_id
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
    score_outbox_writable: Optional[bool] = None
    state_outbox_writable: Optional[bool] = None

    def __post_init__(self) -> None:
        # Keep the original aggregate field for older callers while exposing
        # the two durability channels independently.
        if self.score_outbox_writable is None:
            object.__setattr__(
                self, "score_outbox_writable", self.outbox_writable)
        if self.state_outbox_writable is None:
            object.__setattr__(
                self, "state_outbox_writable", self.outbox_writable)

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
    capabilities: frozenset[str]

    def health_async(self): ...

    def leaderboard_async(self, game_id: str, limit: int = 10): ...

    def recent_async(self, limit: int = 20): ...

    def last_profile_async(self): ...

    def list_profiles_async(self): ...

    def ensure_profile_async(
            self, display_name: str,
            profile_id: Optional[str] = None): ...

    def set_setting_async(self, profile_id: str, key: str, value): ...

    def set_progress_async(
            self, profile_id: str, game_id: str, key: str, value,
            ruleset_version: Optional[str] = None): ...

    def merge_progress_async(
            self, profile_id: str, game_id: str, key: str, value,
            ruleset_version: Optional[str] = None): ...

    def get_progress_async(
            self, profile_id: str, game_id: str, key: str, default=None,
            ruleset_version: Optional[str] = None): ...

    def save_slot_async(
            self, profile_id: str, game_id: str, slot_id: str, state,
            ruleset_version: Optional[str] = None): ...

    def publish_slot_intent(
            self, profile_id: str, game_id: str, slot_id: str, state,
            ruleset_version: Optional[str] = None) -> dict: ...

    def load_slot_async(
            self, profile_id: str, game_id: str, slot_id: str): ...

    def ensure_profile_and_load_slot_async(
            self, display_name: str, profile_id: str,
            game_id: str, slot_id: str): ...

    def quarantine_slot_async(
            self, profile_id: str, game_id: str,
            slot_id: str, reason: str): ...

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

    def poll_local_state_events(self) -> list[LocalStateEvent]: ...

    def get_local_state_status(self, key: str) -> Optional[LocalStateEvent]: ...

    def get_save_status(self, request_id: str) -> Optional[SaveEvent]: ...

    def drain(self, timeout: Optional[float] = None) -> bool: ...

    def report_recovery_notice(self, message: str) -> None: ...

    def close(self): ...
