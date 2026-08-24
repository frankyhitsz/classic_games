"""Storage-neutral contracts shared by pygame and optional adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional, Protocol


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

    def retry_failed_saves(self) -> int: ...

    def drain(self, timeout: Optional[float] = None) -> bool: ...

    def close(self) -> None: ...
