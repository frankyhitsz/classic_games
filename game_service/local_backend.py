"""Non-blocking desktop facade over the local SQLite records store."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import math
import os
import reprlib
import sqlite3
import stat
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from .catalog import GAME_BY_ID, public_games
from .maintenance import (_open_control_file, maintenance_lock,
                          recovered_application_session)
from .mutation import (MutationError, ScoreMutation, canonical_json,
                       normalize_score_mutation)
from .profile import ProfileIdentity, ProfileIdentityError
from .progress import ProgressPolicyError, merge_progress as merge_progress_values
from .service import (DataResult, SaveEvent, SaveState, StorageErrorKind,
                      LocalStateEvent, SlotLoadResult, SlotLoadStatus,
                      StorageStatus)
from .store import LocalGameStore, StoreError, default_database_path

SPOOL_SCHEMA_VERSION = 2
MAX_PENDING_ATTEMPTS = 10_000
MAX_STATE_TIMESTAMP = 1e20
MAX_SPOOL_FILE_BYTES = 64 * 1024
MAX_SPOOL_FILES = 10_000
MAX_SPOOL_TOTAL_BYTES = 64 * 1024 * 1024
MAX_LEGACY_SPOOL_BYTES = 4 * 1024 * 1024
MAX_LEGACY_SPOOL_ITEMS = 10_000
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 100_000
MAX_JSON_STRING = 64 * 1024
REPLAY_BATCH_SIZE = 128
REQUEST_LOCK_TIMEOUT_SECONDS = 1.0
APPLICATION_MAINTENANCE_TIMEOUT_SECONDS = 5.0
STATUS_REFRESH_SECONDS = 0.5
LOGGER = logging.getLogger(__name__)
SPOOL_FIELDS = frozenset({
    "schema_version", "request_id", "payload_hash", "attempt_uuid",
    "revision", "created_at", "attempt_count", "payload",
})


@dataclass(frozen=True)
class PendingSnapshot:
    """Read-only journal scan with a machine-readable completeness result."""

    entries: list
    source_count: int
    included_count: int
    omitted_count: int
    omitted_reasons: dict[str, int]

    @property
    def complete(self) -> bool:
        return self.omitted_count == 0


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant: {value}")


def _validate_json_shape(value) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise StoreError("json_too_complex", "JSON nesting or item count is too large")
        if isinstance(item, str) and len(item) > MAX_JSON_STRING:
            raise StoreError("json_string_too_large", "JSON string is too large")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _read_regular_nofollow(path: Path, limit: int) -> bytes:
    try:
        before = os.lstat(path)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink > 1
                or before.st_size > limit):
            raise OSError("unsafe pending file")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            after = os.fstat(descriptor)
            if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                    or not stat.S_ISREG(after.st_mode)
                    or after.st_nlink > 1 or after.st_size > limit):
                raise OSError("pending file changed while opening")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(limit + 1)
            if len(raw) > limit:
                raise OSError("pending file grew while reading")
            return raw
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise StoreError(
            "unsafe_pending_file", "pending file is unsafe or unreadable"
        ) from exc


@dataclass(frozen=True)
class StorageFailure:
    kind: StorageErrorKind
    code: str
    message: str
    retryable: bool
    quarantine: bool = False

    def result(self) -> dict:
        return {
            "ok": False, "code": self.code, "error": self.message,
            "retryable": self.retryable, "recovery_required": True,
            "storage_error_kind": self.kind.value,
            "pending_preserved": True,
        }


def classify_sqlite_error(exc: sqlite3.Error) -> StorageFailure:
    raw_code = getattr(exc, "sqlite_errorcode", None)
    base_code = raw_code & 0xFF if isinstance(raw_code, int) else None
    groups = {
        getattr(sqlite3, "SQLITE_BUSY", 5): StorageFailure(
            StorageErrorKind.BUSY, "database_busy", "成绩库正忙，稍后自动重试", True),
        getattr(sqlite3, "SQLITE_LOCKED", 6): StorageFailure(
            StorageErrorKind.BUSY, "database_locked", "成绩库暂时被占用", True),
        getattr(sqlite3, "SQLITE_FULL", 13): StorageFailure(
            StorageErrorKind.FULL, "storage_full", "磁盘空间不足，记录已保留", True),
        getattr(sqlite3, "SQLITE_READONLY", 8): StorageFailure(
            StorageErrorKind.READ_ONLY, "database_read_only", "成绩库为只读，记录已保留", True),
        getattr(sqlite3, "SQLITE_IOERR", 10): StorageFailure(
            StorageErrorKind.IO_ERROR, "database_io_error", "磁盘读写失败，记录已保留", True),
        getattr(sqlite3, "SQLITE_CANTOPEN", 14): StorageFailure(
            StorageErrorKind.CANT_OPEN, "database_unavailable", "成绩库暂时无法打开", True),
        getattr(sqlite3, "SQLITE_CORRUPT", 11): StorageFailure(
            StorageErrorKind.CORRUPT, "database_corrupt", "成绩库损坏，记录已隔离保留", False, True),
        getattr(sqlite3, "SQLITE_NOTADB", 26): StorageFailure(
            StorageErrorKind.CORRUPT, "database_not_sqlite", "成绩文件不是有效数据库", False, True),
        getattr(sqlite3, "SQLITE_CONSTRAINT", 19): StorageFailure(
            StorageErrorKind.CONSTRAINT, "database_integrity_error", "成绩库约束异常，记录已保留", False, True),
        getattr(sqlite3, "SQLITE_INTERRUPT", 9): StorageFailure(
            StorageErrorKind.INTERRUPTED, "database_interrupted", "成绩库操作被中断", True),
        getattr(sqlite3, "SQLITE_SCHEMA", 17): StorageFailure(
            StorageErrorKind.SCHEMA_REPAIR_REQUIRED, "schema_repair_required",
            "成绩库结构需要修复，记录已保留", False),
    }
    if base_code in groups:
        return groups[base_code]
    message = str(exc).lower()
    if "no such table" in message or "schema" in message:
        return StorageFailure(
            StorageErrorKind.SCHEMA_REPAIR_REQUIRED,
            "schema_repair_required", "成绩库结构需要修复，记录已保留", False)
    fallback_tokens = (
        "locked", "busy", "unavailable", "readonly", "read-only",
        "disk i/o", "unable to open", "database or disk is full")
    if any(token in message for token in fallback_tokens):
        return StorageFailure(
            StorageErrorKind.IO_ERROR, "database_unavailable",
            "成绩库暂时不可写，记录已保留", True)
    return StorageFailure(
        StorageErrorKind.INTERNAL, "database_operation_error",
        "成绩库操作失败，记录已保留", False)


def classify_os_error(exc: OSError) -> StorageFailure:
    if exc.errno == errno.ENOSPC:
        return StorageFailure(
            StorageErrorKind.FULL, "storage_full", "磁盘空间不足，记录已保留", True)
    if exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}:
        return StorageFailure(
            StorageErrorKind.READ_ONLY, "storage_read_only",
            "成绩目录不可写，记录已保留", True)
    return StorageFailure(
        StorageErrorKind.IO_ERROR, "storage_unavailable",
        "成绩目录暂时不可写，记录已保留", True)


def completed_future(value=None, exception: Optional[BaseException] = None) -> Future:
    future = Future()
    if exception is not None:
        future.set_exception(exception)
    else:
        future.set_result(value)
    return future


@dataclass(frozen=True)
class PendingSaveEnvelope:
    schema_version: int
    request_id: str
    payload_hash: str
    attempt_uuid: str
    revision: int
    created_at: float
    attempt_count: int
    payload: dict

    @classmethod
    def from_mutation(cls, mutation: ScoreMutation,
                      *, created_at: Optional[float] = None,
                      attempt_count: int = 0) -> "PendingSaveEnvelope":
        return cls(
            schema_version=SPOOL_SCHEMA_VERSION,
            request_id=mutation.request_id,
            payload_hash=mutation.payload_hash,
            attempt_uuid=mutation.attempt_uuid,
            revision=mutation.revision,
            created_at=time.time() if created_at is None else created_at,
            attempt_count=attempt_count,
            payload=mutation.transport_payload(),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "payload_hash": self.payload_hash,
            "attempt_uuid": self.attempt_uuid,
            "revision": self.revision,
            "created_at": self.created_at,
            "attempt_count": self.attempt_count,
            "payload": self.payload,
        }

    @classmethod
    def parse(cls, value) -> tuple["PendingSaveEnvelope", ScoreMutation]:
        if not isinstance(value, dict) or set(value) != SPOOL_FIELDS:
            raise StoreError("invalid_spool_envelope",
                             "pending save has unknown or missing fields")
        schema_version = value.get("schema_version")
        if schema_version not in (1, SPOOL_SCHEMA_VERSION):
            raise StoreError("unsupported_spool_version",
                             "pending save uses an unsupported version")
        if (type(value.get("created_at")) not in (int, float)
                or not math.isfinite(float(value["created_at"]))
                or not 0 <= float(value["created_at"]) <= MAX_STATE_TIMESTAMP
                or type(value.get("attempt_count")) is not int
                or not 0 <= value["attempt_count"] <= MAX_PENDING_ATTEMPTS
                or not isinstance(value.get("payload"), dict)):
            raise StoreError("invalid_spool_envelope",
                             "pending save envelope has invalid field types")
        payload = dict(value["payload"])
        if schema_version == 1:
            if (payload.get("request_id") != value.get("request_id")
                    or payload.get("attempt_uuid") != value.get("attempt_uuid")
                    or payload.get("revision") != value.get("revision")):
                raise StoreError(
                    "spool_hash_mismatch",
                    "pending save metadata does not match its payload")
            legacy_semantic = {
                key: item for key, item in payload.items()
                if key != "request_id"}
            try:
                legacy_hash = hashlib.sha256(
                    canonical_json(legacy_semantic).encode("utf-8")).hexdigest()
            except MutationError as exc:
                raise StoreError.from_mutation(exc) from exc
            if legacy_hash != value.get("payload_hash"):
                raise StoreError(
                    "spool_hash_mismatch",
                    "pending save metadata does not match its payload")
            legacy_profile = payload.get("profile_id")
            try:
                ProfileIdentity.validate_uuid(legacy_profile)
            except ProfileIdentityError:
                display_name = (
                    legacy_profile or payload.get("player") or "anonymous")
                payload["profile_id"] = ProfileIdentity.from_legacy_name(
                    display_name).profile_id
        try:
            mutation = normalize_score_mutation(**payload)
        except (MutationError, TypeError) as exc:
            message = (exc.message if isinstance(exc, MutationError)
                       else "pending save payload has unknown fields")
            raise StoreError("invalid_spool_payload", message) from exc
        if (value.get("request_id") != mutation.request_id
                or value.get("attempt_uuid") != mutation.attempt_uuid
                or value.get("revision") != mutation.revision
                or (schema_version == SPOOL_SCHEMA_VERSION
                    and value.get("payload_hash") != mutation.payload_hash)):
            raise StoreError("spool_hash_mismatch",
                             "pending save metadata does not match its payload")
        return cls(
            schema_version=SPOOL_SCHEMA_VERSION,
            request_id=mutation.request_id,
            payload_hash=mutation.payload_hash,
            attempt_uuid=mutation.attempt_uuid,
            revision=mutation.revision,
            created_at=float(value["created_at"]),
            attempt_count=value["attempt_count"],
            payload=mutation.transport_payload()), mutation


class PersistentSaveOutbox:
    """Cross-process-safe spool with one immutable file per request."""

    def __init__(self, path: Path | str,
                 legacy_path: Optional[Path | str] = None, *,
                 maintain: bool = True):
        selected = Path(path)
        if selected.suffix.lower() == ".json":
            self.path = selected.with_suffix("")
            self.legacy_path = (Path(legacy_path) if legacy_path is not None
                                else selected)
        else:
            self.path = selected
            self.legacy_path = (Path(legacy_path)
                                if legacy_path is not None else None)
        self.quarantine_path = self.path.with_name(f"{self.path.name}-quarantine")
        self.migration_backup_path = self.path.with_name(
            f"{self.path.name}-migration-backup")
        self._lock = threading.RLock()
        # Counting an unbounded quarantine directory delayed the first frame.
        # The background pending scan will add new items to this count.
        self.quarantined_count = 0
        self.migrated_spool_count = 0
        self.recovery_notice: Optional[str] = None
        if maintain:
            self._migrate_legacy_file()
        self._update_notice()

    def _target(self, request_id: str) -> Path:
        return self.path / f"{request_id}.json"

    def _request_lock_path(self, request_id: str) -> Path:
        return self.path / f".{request_id}.lock"

    @staticmethod
    def _write_bytes(path: Path, data: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name != "posix":
            return
        if not path.is_dir():
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _request_lock(self, request_id: str):
        lock_path = self._request_lock_path(request_id)
        deadline = time.monotonic() + REQUEST_LOCK_TIMEOUT_SECONDS
        descriptor = _open_control_file(lock_path)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            while not self._try_lock_descriptor(descriptor):
                if time.monotonic() >= deadline:
                    raise StoreError(
                        "spool_lock_timeout",
                        "pending save is busy in another process", 503,
                        retryable=True)
                time.sleep(0.01)
            try:
                yield
            finally:
                self._unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _try_lock_descriptor(descriptor: int) -> bool:
        if os.name == "nt":
            import msvcrt

            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @staticmethod
    def _unlock_descriptor(descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)

    @staticmethod
    def _conflict(existing: PendingSaveEnvelope,
                  incoming: PendingSaveEnvelope) -> StoreError:
        return StoreError(
            "request_id_conflict",
            "request_id already has a different pending payload", 409,
            retryable=False,
            details={"existing_payload_hash": existing.payload_hash,
                     "new_payload_hash": incoming.payload_hash})

    def _quarantine(self, path: Path, reason: str) -> bool:
        try:
            self.quarantine_path.mkdir(parents=True, exist_ok=True)
            target = self.quarantine_path / (
                f"{path.name}.{reason}-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
            os.replace(path, target)
            self._fsync_directory(self.quarantine_path)
            self._fsync_directory(path.parent)
        except OSError:
            return False
        self.quarantined_count += 1
        return True

    def _quarantine_value(self, value, reason: str) -> bool:
        try:
            self.quarantine_path.mkdir(parents=True, exist_ok=True)
            target = self.quarantine_path / (
                f"legacy-item.{reason}-{time.time_ns()}-{uuid.uuid4().hex[:8]}.json")
            try:
                encoded = canonical_json({"reason": reason, "value": value})
            except (MutationError, RecursionError, MemoryError, TypeError, ValueError):
                try:
                    preview = reprlib.repr(value)
                except Exception:  # noqa: BLE001
                    preview = f"<{type(value).__name__}>"
                encoded = json.dumps(
                    {"reason": reason, "value_type": type(value).__name__,
                     "safe_preview": preview[:2048]},
                    ensure_ascii=False, allow_nan=False)
            self._write_bytes(target, encoded.encode("utf-8"))
            self._fsync_directory(self.quarantine_path)
        except (MutationError, OSError, StoreError, RecursionError,
                MemoryError, TypeError, ValueError):
            return False
        self.quarantined_count += 1
        return True

    def _migrate_legacy_file(self) -> None:
        legacy = self.legacy_path
        if legacy is None or not legacy.is_file():
            return
        try:
            if legacy.stat().st_size > MAX_LEGACY_SPOOL_BYTES:
                self._quarantine(legacy, "too-large")
                self._update_notice()
                return
            value = json.loads(
                legacy.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant)
            _validate_json_shape(value)
        except MemoryError:
            self._quarantine(legacy, "memory-error")
            self._update_notice()
            return
        except (OSError, StoreError, ValueError, RecursionError):
            self._quarantine(legacy, "corrupt")
            self._update_notice()
            return
        if not isinstance(value, list):
            self._quarantine(legacy, "invalid-root")
            self._update_notice()
            return
        if len(value) > MAX_LEGACY_SPOOL_ITEMS:
            self._quarantine(legacy, "too-many-items")
            self._update_notice()
            return
        all_resolved = True
        for item in value:
            try:
                if not isinstance(item, dict):
                    raise TypeError("item is not an object")
                _validate_json_shape(item)
                payload = dict(item)
                legacy_profile = payload.get("profile_id")
                if legacy_profile is not None:
                    try:
                        ProfileIdentity.validate_uuid(legacy_profile)
                    except ProfileIdentityError:
                        payload["profile_id"] = (
                            ProfileIdentity.from_legacy_name(
                                legacy_profile).profile_id)
                mutation = normalize_score_mutation(**payload)
                self.add_mutation(mutation)
            except MemoryError:
                all_resolved = False
                break
            except OSError:
                all_resolved = False
                continue
            except (MutationError, StoreError, TypeError,
                    RecursionError, ValueError):
                if not self._quarantine_value(item, "invalid-item"):
                    all_resolved = False
        if not all_resolved:
            self._update_notice(["旧版待保存文件尚未完全迁移，将稍后重试"])
            return
        migrated = legacy.with_name(
            f"{legacy.name}.migrated-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
        try:
            os.replace(legacy, migrated)
        except OSError:
            pass
        self._update_notice()

    def _update_notice(self, extra: Optional[list[str]] = None) -> None:
        notices = list(extra or [])
        if self.migrated_spool_count:
            notices.append(
                f"已升级 {self.migrated_spool_count} 条旧版待保存记录")
        if self.quarantined_count:
            notices.append(
                f"已隔离 {self.quarantined_count} 条无法恢复的待保存记录")
        self.recovery_notice = "；".join(notices) or None

    def refresh_quarantine_count(self) -> None:
        try:
            count = 0
            with os.scandir(self.quarantine_path) as entries:
                for entry in entries:
                    if entry.is_file():
                        count += 1
                        if count >= MAX_SPOOL_FILES:
                            break
            self.quarantined_count = count
        except (FileNotFoundError, NotADirectoryError):
            self.quarantined_count = 0
        except OSError:
            return
        self._update_notice()

    def add(self, payload: dict) -> PendingSaveEnvelope:
        """Compatibility helper for callers that already have a payload."""
        try:
            mutation = normalize_score_mutation(**payload)
        except (MutationError, TypeError) as exc:
            if isinstance(exc, MutationError):
                raise StoreError.from_mutation(exc) from exc
            raise StoreError("invalid_spool_payload",
                             "pending save payload has unknown fields") from exc
        return self.add_mutation(mutation)

    def add_mutation(self, mutation: ScoreMutation, *,
                     created_at: Optional[float] = None) -> PendingSaveEnvelope:
        envelope = PendingSaveEnvelope.from_mutation(
            mutation, created_at=created_at)
        encoded = canonical_json(envelope.to_dict()).encode("utf-8")
        with self._lock:
            self.path.mkdir(parents=True, exist_ok=True)
            target = self._target(mutation.request_id)
            temp = self.path / (
                f".{mutation.request_id}.{uuid.uuid4().hex}.tmp")
            self._write_bytes(temp, encoded)
            try:
                try:
                    os.link(temp, target)
                except FileExistsError:
                    existing, _ = self._read_file(target)
                    if existing.payload_hash != envelope.payload_hash:
                        raise self._conflict(existing, envelope)
                    return existing
                except OSError:
                    # Hard links are unavailable on some filesystems. Publish
                    # the already-fsynced temp file under a cross-process
                    # request lock so readers never observe a partial target.
                    with self._request_lock(mutation.request_id):
                        if target.exists():
                            existing, _ = self._read_file(target)
                            if existing.payload_hash != envelope.payload_hash:
                                raise self._conflict(existing, envelope)
                            return existing
                        os.replace(temp, target)
                        self._fsync_directory(self.path)
                        return envelope
                else:
                    self._fsync_directory(self.path)
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
        return envelope

    def increment_attempt(self, request_id: str) -> None:
        target = self._target(request_id)
        with self._lock:
            if not target.is_file():
                return
            with self._request_lock(request_id):
                envelope, _mutation = self._read_file(target)
                updated = replace(
                    envelope,
                    attempt_count=min(
                        MAX_PENDING_ATTEMPTS, envelope.attempt_count + 1))
                encoded = canonical_json(updated.to_dict()).encode("utf-8")
                temp = self.path / f".{request_id}.{uuid.uuid4().hex}.tmp"
                self._write_bytes(temp, encoded)
                try:
                    os.replace(temp, target)
                    self._fsync_directory(self.path)
                finally:
                    try:
                        temp.unlink()
                    except FileNotFoundError:
                        pass

    def set_attempt_count_max(self, request_id: str, value: int) -> None:
        """Raise a retry count with one atomic rewrite instead of N fsyncs."""
        if type(value) is not int or not 0 <= value <= MAX_PENDING_ATTEMPTS:
            raise StoreError(
                "invalid_spool_envelope", "pending retry count is out of range")
        target = self._target(request_id)
        with self._lock:
            if not target.is_file():
                return
            with self._request_lock(request_id):
                envelope, _mutation = self._read_file(target)
                if value <= envelope.attempt_count:
                    return
                updated = replace(envelope, attempt_count=value)
                encoded = canonical_json(updated.to_dict()).encode("utf-8")
                temp = self.path / f".{request_id}.{uuid.uuid4().hex}.tmp"
                self._write_bytes(temp, encoded)
                try:
                    os.replace(temp, target)
                    self._fsync_directory(self.path)
                finally:
                    try:
                        temp.unlink()
                    except FileNotFoundError:
                        pass

    def _upgrade_v1_file(self, path: Path, original: bytes,
                         envelope: PendingSaveEnvelope) -> None:
        self.migration_backup_path.mkdir(parents=True, exist_ok=True)
        backup = self.migration_backup_path / (
            f"{path.name}.v1-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
        temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.upgrade"
        self._write_bytes(backup, original)
        self._fsync_directory(self.migration_backup_path)
        try:
            encoded = canonical_json(envelope.to_dict()).encode("utf-8")
            self._write_bytes(temp, encoded)
            os.replace(temp, path)
            self._fsync_directory(path.parent)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
        self.migrated_spool_count += 1

    def _read_file(self, path: Path) -> tuple[PendingSaveEnvelope, ScoreMutation]:
        try:
            original = _read_regular_nofollow(path, MAX_SPOOL_FILE_BYTES)
            value = json.loads(original.decode("utf-8"),
                               parse_constant=_reject_json_constant)
            _validate_json_shape(value)
        except (OSError, StoreError, ValueError, RecursionError) as exc:
            raise StoreError("corrupt_spool_file",
                             "pending save is not valid JSON") from exc
        envelope, mutation = PendingSaveEnvelope.parse(value)
        if value.get("schema_version") == 1:
            self._upgrade_v1_file(path, original, envelope)
        return envelope, mutation

    def list_envelopes(self) -> list[tuple[PendingSaveEnvelope, ScoreMutation]]:
        with self._lock:
            if not self.path.is_dir():
                return []
            result = []
            paths = sorted(self.path.glob("*.json"))
            notices = []
            total_bytes = 0
            for path in paths:
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    continue
            if total_bytes > MAX_SPOOL_TOTAL_BYTES:
                notices.append(
                    f"待保存文件共 {total_bytes // (1024 * 1024)} MiB，"
                    "已超过 64 MiB 告警线")
            if len(paths) > MAX_SPOOL_FILES:
                notices.append(
                    f"待保存目录有 {len(paths)} 个文件；本次只检查前 "
                    f"{MAX_SPOOL_FILES} 个")
                paths = paths[:MAX_SPOOL_FILES]
            for path in paths:
                try:
                    envelope, mutation = self._read_file(path)
                    canonical = self._target(envelope.request_id)
                    if path != canonical:
                        with self._request_lock(envelope.request_id):
                            if canonical.exists():
                                existing, _ = self._read_file(canonical)
                                if existing.payload_hash != envelope.payload_hash:
                                    self._quarantine(path, "filename-conflict")
                                    continue
                                path.unlink()
                            else:
                                os.replace(path, canonical)
                            self._fsync_directory(self.path)
                    result.append((envelope, mutation))
                except StoreError as exc:
                    self._quarantine(path, exc.code)
                except OSError:
                    notices.append(f"{path.name} 暂时无法处理，已保留原文件")
                    continue
                except MemoryError:
                    notices.append("待保存目录过大，本次扫描已提前停止")
                    break
            self._update_notice(notices)
            return result

    def snapshot_envelopes(self) -> PendingSnapshot:
        """Parse active score journals without upgrading or quarantining them."""
        entries = []
        reasons: dict[str, int] = {}
        try:
            with os.scandir(self.path) as iterator:
                paths = sorted(
                    (Path(entry.path) for entry in iterator
                     if entry.name.endswith(".json")),
                    key=lambda path: path.name)
        except (FileNotFoundError, NotADirectoryError):
            paths = []
        except OSError:
            return PendingSnapshot([], 0, 0, 1, {"directory_unreadable": 1})
        source_count = len(paths)
        total_bytes = 0
        for index, path in enumerate(paths):
            if index >= MAX_SPOOL_FILES:
                reasons["file_count_limit"] = len(paths) - index
                break
            try:
                metadata = os.lstat(path)
                if (not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink > 1):
                    raise StoreError("unsafe_spool_file", "unsafe file type")
                size = metadata.st_size
                if size > MAX_SPOOL_FILE_BYTES:
                    raise StoreError("spool_file_too_large", "too large")
                if total_bytes + size > MAX_SPOOL_TOTAL_BYTES:
                    reasons["total_size_limit"] = len(paths) - index
                    break
                total_bytes += size
                raw = _read_regular_nofollow(path, MAX_SPOOL_FILE_BYTES)
                value = json.loads(
                    raw.decode("utf-8"), parse_constant=_reject_json_constant)
                _validate_json_shape(value)
                envelope, mutation = PendingSaveEnvelope.parse(value)
                if path.name != f"{envelope.request_id}.json":
                    raise StoreError("non_canonical_name", "wrong filename")
                entries.append((envelope, mutation))
            except (OSError, StoreError, TypeError, ValueError,
                    RecursionError, UnicodeError):
                reasons["invalid_or_unreadable"] = (
                    reasons.get("invalid_or_unreadable", 0) + 1)
        omitted = sum(reasons.values())
        return PendingSnapshot(
            entries, source_count, len(entries), omitted, reasons)

    def list(self) -> list[dict]:
        return [mutation.transport_payload()
                for _envelope, mutation in self.list_envelopes()]

    def has_entries(self) -> bool:
        """Cheap startup hint that never parses or rewrites spool files."""
        try:
            with os.scandir(self.path) as entries:
                return any(entry.is_file() and entry.name.endswith(".json")
                           for entry in entries)
        except (FileNotFoundError, NotADirectoryError, OSError):
            return False

    def remove(self, request_id: str) -> None:
        try:
            self._target(request_id).unlink()
            self._fsync_directory(self.path)
        except FileNotFoundError:
            pass

    def quarantine_request(self, request_id: str, reason: str) -> bool:
        target = self._target(request_id)
        if target.exists():
            return self._quarantine(target, reason)
        return False

    def probe_writable(self) -> bool:
        probe = self.path / f".probe-{uuid.uuid4().hex}"
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            self._write_bytes(probe, b"ok")
        except OSError:
            return False
        finally:
            try:
                probe.unlink()
            except FileNotFoundError:
                pass
        return True


class PersistentStateOutbox:
    """Cross-process latest-value journal for local profile state."""

    SCHEMA_VERSION = 3
    FIELDS = frozenset({
        "schema_version", "operation_id", "key", "kind", "method", "args",
        "ruleset_version", "logical_revision", "updated_at", "components",
        "payload_hash",
    })

    ALLOWED_METHODS = frozenset({
        "ensure_profile", "set_setting", "set_progress", "merge_progress",
        "save_slot",
    })
    LEGACY_RULESETS = {
        "tetris": "tetris-assist-2",
        "snake": "snake-classic-1",
        "2048": "2048-classic-2",
        "sokoban": "sokoban-campaign-2",
        "zuma": "zuma-classic-2",
    }

    def __init__(self, path: Path | str, *, recover: bool = True):
        self.path = Path(path)
        self.quarantine_path = self.path.with_name(
            f"{self.path.name}-quarantine")
        self.migration_backup_path = self.path.with_name(
            f"{self.path.name}-migration-backup")
        self._lock = threading.RLock()
        self.quarantined_count = self._bounded_quarantine_count()
        self.recovery_notice: Optional[str] = None
        self._count = 0
        if recover:
            self._recover_reject_transactions()
        self.refresh_count()
        self._update_notice()

    def _bounded_quarantine_count(self) -> int:
        count = 0
        try:
            with os.scandir(self.quarantine_path) as entries:
                for entry in entries:
                    if entry.is_file():
                        count += 1
                        if count >= MAX_SPOOL_FILES:
                            break
        except (FileNotFoundError, NotADirectoryError, OSError):
            return 0
        return count

    @staticmethod
    def _digest(value: dict) -> str:
        return hashlib.sha256(
            canonical_json(value).encode("utf-8")).hexdigest()

    def _target(self, key: str) -> Path:
        name = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.path / f"{name}.json"

    def _key_lock_path(self, key: str) -> Path:
        name = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.path / f".state-{name}.lock"

    def next_revision(self, observed: int = 0) -> int:
        """Return a cross-process monotonic revision backed by a tiny journal."""
        if type(observed) is not int or observed < 0:
            raise StoreError("invalid_state_revision", "invalid observed revision")
        clock_path = self.path / ".state-clock"
        with self._lock, self._digest_lock("clock"):
            previous = 0
            try:
                raw = clock_path.read_text(encoding="ascii").strip()
                previous = int(raw)
                if previous < 0:
                    raise ValueError
            except FileNotFoundError:
                pass
            except (OSError, UnicodeError, ValueError):
                self._quarantine_clock_locked(clock_path)
                previous = 0
            if previous > (1 << 63) - 2:
                self._quarantine_clock_locked(clock_path)
                previous = 0
            revision = max(previous + 1, observed + 1, time.time_ns())
            if revision > (1 << 63) - 1:
                raise StoreError(
                    "state_clock_overflow", "local state clock is exhausted", 409)
            temp = self.path / f".state-clock.{uuid.uuid4().hex}.tmp"
            PersistentSaveOutbox._write_bytes(
                temp, str(revision).encode("ascii"))
            try:
                os.replace(temp, clock_path)
                PersistentSaveOutbox._fsync_directory(self.path)
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
        return revision

    def _quarantine_clock_locked(self, clock_path: Path) -> None:
        """Preserve a damaged clock before rebuilding it from a known high-water."""
        try:
            self.quarantine_path.mkdir(parents=True, exist_ok=True)
            target = self.quarantine_path / (
                f"state-clock.invalid-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
            os.replace(clock_path, target)
            PersistentSaveOutbox._fsync_directory(self.quarantine_path)
            PersistentSaveOutbox._fsync_directory(self.path)
        except FileNotFoundError:
            return
        except OSError:
            return
        self.quarantined_count += 1
        self._update_notice()

    @contextmanager
    def _key_lock(self, key: str):
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        with self._digest_lock(digest):
            yield

    @contextmanager
    def _digest_lock(self, digest: str):
        self.path.mkdir(parents=True, exist_ok=True)
        descriptor = _open_control_file(
            self.path / f".state-{digest}.lock")
        deadline = time.monotonic() + REQUEST_LOCK_TIMEOUT_SECONDS
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            while not PersistentSaveOutbox._try_lock_descriptor(descriptor):
                if time.monotonic() >= deadline:
                    raise StoreError(
                        "state_lock_timeout",
                        "pending local state is busy in another process",
                        503, retryable=True)
                time.sleep(0.01)
            try:
                yield
            finally:
                PersistentSaveOutbox._unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _kind(method: str) -> str:
        if method == "ensure_profile":
            return "profile"
        if method == "set_setting":
            return "setting"
        if method in {"set_progress", "merge_progress"}:
            return "progress"
        return "slot"

    @staticmethod
    def _ruleset(method: str, args: list) -> Optional[str]:
        if method in {"set_progress", "merge_progress"}:
            game_id = args[1]
            return args[4] or GAME_BY_ID[game_id].ruleset_version
        if method == "save_slot":
            game_id = args[1]
            return (args[4] if len(args) > 4 and args[4]
                    else GAME_BY_ID[game_id].ruleset_version)
        return None

    @classmethod
    def _operation(cls, key: str, method: str, args: tuple,
                   logical_revision: int,
                   operation_id: Optional[str] = None, *,
                   components: Optional[list[dict]] = None,
                   updated_at: Optional[float] = None) -> dict:
        if method not in cls.ALLOWED_METHODS:
            raise StoreError("invalid_state_operation", "unsupported state operation")
        if (type(logical_revision) is not int
                or not 0 <= logical_revision <= (1 << 63) - 1):
            raise StoreError(
                "invalid_state_revision", "invalid local state revision")
        arguments = list(args)
        try:
            ruleset_version = cls._ruleset(method, arguments)
        except (IndexError, KeyError, TypeError) as exc:
            raise StoreError(
                "invalid_state_operation", "invalid local state arguments") from exc
        if method in {"set_progress", "merge_progress"}:
            arguments[4] = ruleset_version
        elif method == "save_slot":
            if len(arguments) == 4:
                arguments.append(ruleset_version)
            else:
                arguments[4] = ruleset_version
        operation_id = operation_id or uuid.uuid4().hex
        timestamp = time.time() if updated_at is None else float(updated_at)
        if (not math.isfinite(timestamp)
                or not 0 <= timestamp <= MAX_STATE_TIMESTAMP):
            raise StoreError(
                "invalid_state_timestamp", "invalid local state timestamp")
        operation = {
            "schema_version": cls.SCHEMA_VERSION,
            "operation_id": operation_id,
            "key": key,
            "kind": cls._kind(method),
            "method": method,
            "args": arguments,
            "ruleset_version": ruleset_version,
            "logical_revision": logical_revision,
            "updated_at": timestamp,
            "components": [],
        }
        if method == "merge_progress":
            if components is None:
                component_semantic = {
                    "operation_id": operation_id,
                    "key": key,
                    "method": method,
                    "args": arguments,
                    "ruleset_version": ruleset_version,
                    "updated_at": timestamp,
                }
                components = [{
                    "operation_id": operation_id,
                    "payload_hash": cls._digest(component_semantic),
                }]
            operation["components"] = sorted(
                (dict(component) for component in components),
                key=lambda component: component["operation_id"])
        _validate_json_shape(operation)
        try:
            operation["payload_hash"] = cls._digest(operation)
        except (MutationError, RecursionError, MemoryError) as exc:
            if isinstance(exc, MutationError):
                raise StoreError.from_mutation(exc) from exc
            raise StoreError(
                "invalid_state_operation", "local state is too complex") from exc
        return operation

    @staticmethod
    def _order(operation: dict) -> tuple[int, str]:
        return operation["logical_revision"], operation["operation_id"]

    @classmethod
    def with_revision(cls, operation: dict, revision: int) -> dict:
        revised = dict(operation)
        revised["logical_revision"] = revision
        revised["payload_hash"] = cls._digest({
            key: value for key, value in revised.items()
            if key != "payload_hash"})
        return revised

    @classmethod
    def _merge_progress_operations(cls, existing: dict,
                                   incoming: dict) -> dict:
        if existing["args"][:3] != incoming["args"][:3]:
            raise StoreError(
                "state_key_conflict", "progress journal key has conflicting data")
        if existing["ruleset_version"] != incoming["ruleset_version"]:
            raise StoreError(
                "state_ruleset_conflict", "progress journal ruleset changed")
        game_id, key = incoming["args"][1], incoming["args"][2]
        try:
            value = merge_progress_values(
                game_id, key, existing["args"][3], incoming["args"][3])
        except ProgressPolicyError as exc:
            raise StoreError("invalid_progress", str(exc)) from exc
        components_by_id: dict[str, str] = {}
        for operation in (existing, incoming):
            for component in operation["components"]:
                component_id = component["operation_id"]
                component_hash = component["payload_hash"]
                prior_hash = components_by_id.get(component_id)
                if prior_hash is not None and prior_hash != component_hash:
                    raise StoreError(
                        "state_operation_conflict",
                        "progress component ID was reused with different data", 409)
                components_by_id[component_id] = component_hash
        components = [{"operation_id": component_id,
                       "payload_hash": components_by_id[component_id]}
                      for component_id in sorted(components_by_id)]
        component_identity = cls._digest({
            "key": incoming["key"], "components": components})
        aggregate_id = f"aggregate-{component_identity[:48]}"
        merged = cls._operation(
            incoming["key"], "merge_progress",
            (*incoming["args"][:3], value, incoming["ruleset_version"]),
            max(existing["logical_revision"], incoming["logical_revision"]),
            aggregate_id, components=components,
            updated_at=max(existing["updated_at"], incoming["updated_at"]))
        return merged

    def put(self, key: str, method: str, args: tuple, *,
            logical_revision: Optional[int] = None,
            operation_id: Optional[str] = None,
            components: Optional[list[dict]] = None,
            updated_at: Optional[float] = None) -> dict:
        incoming = self._operation(
            key, method, args,
            time.time_ns() if logical_revision is None else logical_revision,
            operation_id, components=components, updated_at=updated_at)
        published = True
        previous_operation = None
        with self._lock, self._key_lock(key):
            target = self._target(key)
            existed = target.is_file()
            if existed:
                try:
                    existing = self._parse(_read_regular_nofollow(
                        target, MAX_SPOOL_FILE_BYTES))
                except (OSError, StoreError, TypeError, ValueError,
                        RecursionError):
                    self._quarantine_locked(target, "invalid-current")
                    existed = False
                else:
                    previous_operation = existing
                    if incoming["kind"] == existing["kind"] == "progress":
                        incoming = self._merge_progress_operations(
                            existing, incoming)
                    elif self._order(incoming) == self._order(existing):
                        if incoming["payload_hash"] != existing["payload_hash"]:
                            raise StoreError(
                                "state_operation_conflict",
                                "state operation ID was reused with different data",
                                409,
                            )
                        incoming = existing
                        published = False
                    elif self._order(incoming) < self._order(existing):
                        incoming = existing
                        published = False
            try:
                encoded = canonical_json(incoming).encode("utf-8")
            except (MutationError, RecursionError, MemoryError) as exc:
                if isinstance(exc, MutationError):
                    raise StoreError.from_mutation(exc) from exc
                raise StoreError(
                    "invalid_state_operation", "local state is too complex") from exc
            if len(encoded) > MAX_SPOOL_FILE_BYTES:
                raise StoreError(
                    "value_too_large", "pending local state is too large")
            if published or not target.is_file():
                temp = self.path / f".{target.name}.{uuid.uuid4().hex}.tmp"
                PersistentSaveOutbox._write_bytes(temp, encoded)
                try:
                    os.replace(temp, target)
                    PersistentSaveOutbox._fsync_directory(self.path)
                finally:
                    try:
                        temp.unlink()
                    except FileNotFoundError:
                        pass
            if not existed and target.is_file():
                self._count += 1
        return {
            "payload_hash": incoming["payload_hash"],
            "published": published,
            "operation": incoming,
            "previous_operation": previous_operation,
        }

    @classmethod
    def _parse(cls, raw: bytes) -> dict:
        value = json.loads(raw.decode("utf-8"),
                           parse_constant=_reject_json_constant)
        _validate_json_shape(value)
        if not isinstance(value, dict):
            raise StoreError(
                "invalid_state_journal", "invalid pending local state")
        if value.get("schema_version") == 1:
            expected = {"schema_version", "key", "method", "args",
                        "updated_at", "payload_hash"}
            if (set(value) != expected
                    or not isinstance(value.get("key"), str)
                    or value.get("method") not in cls.ALLOWED_METHODS
                    or not isinstance(value.get("args"), list)
                    or type(value.get("updated_at")) not in (int, float)
                    or not math.isfinite(float(value["updated_at"]))
                    or not 0 <= float(value["updated_at"]) <= MAX_STATE_TIMESTAMP):
                raise StoreError(
                    "invalid_state_journal", "invalid pending local state")
            legacy_hash = value.get("payload_hash")
            unhashed = {key: child for key, child in value.items()
                        if key != "payload_hash"}
            if legacy_hash != cls._digest(unhashed):
                raise StoreError(
                    "state_journal_hash_mismatch",
                    "pending local state was modified")
            legacy_args = list(value["args"])
            method = value["method"]
            if method in {"set_progress", "merge_progress", "save_slot"}:
                try:
                    legacy_args[4] = cls.LEGACY_RULESETS[legacy_args[1]]
                except (IndexError, KeyError, TypeError) as exc:
                    raise StoreError(
                        "invalid_state_journal",
                        "legacy state has no compatible ruleset") from exc
            if method == "ensure_profile":
                canonical_key = f"profile:{legacy_args[1]}"
            elif method == "set_setting":
                canonical_key = f"setting:{legacy_args[0]}:{legacy_args[1]}"
            elif method in {"set_progress", "merge_progress"}:
                canonical_key = (
                    f"progress:{legacy_args[0]}:{legacy_args[1]}:"
                    f"{legacy_args[4]}:{legacy_args[2]}")
            else:
                canonical_key = (
                    f"slot:{legacy_args[0]}:{legacy_args[1]}:{legacy_args[2]}")
            upgraded = cls._operation(
                canonical_key, method, tuple(legacy_args),
                min((1 << 63) - 1,
                    max(0, int(float(value["updated_at"]) * 1_000_000_000))),
                f"legacy-{legacy_hash[:24]}",
                updated_at=float(value["updated_at"]))
            LocalGameStore.validate_state_operation(upgraded)
            return upgraded
        if value.get("schema_version") == 2:
            expected_v2 = cls.FIELDS - {"components"}
            if set(value) != expected_v2:
                raise StoreError(
                    "invalid_state_journal", "invalid pending local state")
            legacy_hash = value.get("payload_hash")
            if legacy_hash != cls._digest({
                    key: child for key, child in value.items()
                    if key != "payload_hash"}):
                raise StoreError(
                    "state_journal_hash_mismatch",
                    "pending local state was modified")
            upgraded = cls._operation(
                value["key"], value["method"], tuple(value["args"]),
                value["logical_revision"], value["operation_id"],
                updated_at=value["updated_at"])
            LocalGameStore.validate_state_operation(upgraded)
            return upgraded
        try:
            derived_ruleset = cls._ruleset(
                value.get("method"), value.get("args"))
        except (IndexError, KeyError, TypeError):
            derived_ruleset = object()
        if (set(value) != cls.FIELDS
                or value.get("schema_version") != cls.SCHEMA_VERSION
                or not isinstance(value.get("key"), str)
                or not value["key"] or len(value["key"]) > 512
                or value.get("method") not in cls.ALLOWED_METHODS
                or not isinstance(value.get("args"), list)
                or value.get("kind") != cls._kind(value.get("method"))
                or not isinstance(value.get("operation_id"), str)
                or not 1 <= len(value["operation_id"]) <= 128
                or value.get("ruleset_version") != derived_ruleset
                or type(value.get("logical_revision")) is not int
                or not 0 <= value["logical_revision"] <= (1 << 63) - 1
                or type(value.get("updated_at")) not in (int, float)
                or not math.isfinite(float(value["updated_at"]))
                or not 0 <= float(value["updated_at"]) <= MAX_STATE_TIMESTAMP
                or not isinstance(value.get("components"), list)):
            raise StoreError(
                "invalid_state_journal", "invalid pending local state")
        components = value["components"]
        if value["method"] == "merge_progress":
            if not components:
                raise StoreError(
                    "invalid_state_journal", "progress components are missing")
            component_ids = set()
            for component in components:
                if (not isinstance(component, dict)
                        or set(component) != {"operation_id", "payload_hash"}
                        or not isinstance(component["operation_id"], str)
                        or not 1 <= len(component["operation_id"]) <= 128
                        or component["operation_id"] in component_ids
                        or not isinstance(component["payload_hash"], str)
                        or len(component["payload_hash"]) != 64):
                    raise StoreError(
                        "invalid_state_journal", "invalid progress components")
                component_ids.add(component["operation_id"])
            if components != sorted(
                    components, key=lambda component: component["operation_id"]):
                raise StoreError(
                    "invalid_state_journal", "progress components are unordered")
        elif components:
            raise StoreError(
                "invalid_state_journal",
                "non-progress state cannot contain components")
        payload_hash = value["payload_hash"]
        valid = cls._digest({key: child for key, child in value.items()
                             if key != "payload_hash"})
        if payload_hash != valid:
            raise StoreError(
                "state_journal_hash_mismatch", "pending local state was modified")
        LocalGameStore.validate_state_operation(value)
        return value

    def _update_notice(self) -> None:
        self.recovery_notice = (
            f"已隔离 {self.quarantined_count} 条损坏的本机状态记录"
            if self.quarantined_count else None)

    def _quarantine_locked(self, path: Path, reason: str) -> bool:
        try:
            self.quarantine_path.mkdir(parents=True, exist_ok=True)
            target = self.quarantine_path / (
                f"{path.name}.{reason}-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
            os.replace(path, target)
            PersistentSaveOutbox._fsync_directory(self.quarantine_path)
            PersistentSaveOutbox._fsync_directory(path.parent)
        except OSError:
            return False
        self._count = max(0, self._count - 1)
        self.quarantined_count += 1
        self._update_notice()
        return True

    def list_entries(self, limit: int = REPLAY_BATCH_SIZE) -> list[dict]:
        with self._lock:
            if not self.path.is_dir():
                return []
            entries = []
            entry_index: dict[str, int] = {}
            total_bytes = 0
            visited = 0
            try:
                iterator = os.scandir(self.path)
            except OSError:
                return []
            with iterator:
                paths = (Path(entry.path) for entry in iterator
                         if entry.name.endswith(".json"))
                for path in paths:
                    visited += 1
                    if visited > MAX_SPOOL_FILES or len(entries) >= limit:
                        break
                    digest = path.stem
                    try:
                        if (len(digest) != 64
                                or any(char not in "0123456789abcdef"
                                       for char in digest)):
                            raise StoreError(
                                "state_journal_bad_name", "wrong key")
                        with self._digest_lock(digest):
                            size = path.stat().st_size
                            if size > MAX_SPOOL_FILE_BYTES:
                                raise StoreError(
                                    "state_journal_too_large", "too large")
                            if total_bytes + size > MAX_SPOOL_TOTAL_BYTES:
                                self.recovery_notice = (
                                    "待保存状态超过扫描总量，本次保留未扫描文件")
                                break
                            total_bytes += size
                            raw = _read_regular_nofollow(
                                path, MAX_SPOOL_FILE_BYTES)
                            raw_value = json.loads(
                                raw.decode("utf-8"),
                                parse_constant=_reject_json_constant)
                            value = self._parse(raw)
                            is_v1 = raw_value.get("schema_version") == 1
                            if not is_v1 and path != self._target(value["key"]):
                                raise StoreError(
                                    "state_journal_bad_name", "wrong key")
                            if is_v1:
                                value = self._upgrade_v1_locked(path, raw, value)
                            if value["key"] in entry_index:
                                entries[entry_index[value["key"]]] = value
                            else:
                                entry_index[value["key"]] = len(entries)
                                entries.append(value)
                    except (OSError, StoreError, TypeError, ValueError,
                            RecursionError):
                        try:
                            with self._digest_lock(digest):
                                if path.exists():
                                    self._quarantine_locked(path, "invalid")
                        except (OSError, StoreError):
                            pass
            return entries

    def snapshot_entries(self) -> PendingSnapshot:
        """Parse state journals without upgrade, quarantine, rename, or notice IO."""
        entries = []
        reasons: dict[str, int] = {}
        try:
            with os.scandir(self.path) as iterator:
                paths = sorted(
                    (Path(entry.path) for entry in iterator
                     if entry.name.endswith(".json")),
                    key=lambda path: path.name)
        except (FileNotFoundError, NotADirectoryError):
            paths = []
        except OSError:
            return PendingSnapshot([], 0, 0, 1, {"directory_unreadable": 1})
        source_count = len(paths)
        total_bytes = 0
        for index, path in enumerate(paths):
            if index >= MAX_SPOOL_FILES:
                reasons["file_count_limit"] = len(paths) - index
                break
            try:
                metadata = os.lstat(path)
                if (not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink > 1):
                    raise StoreError("unsafe_state_file", "unsafe file type")
                size = metadata.st_size
                if size > MAX_SPOOL_FILE_BYTES:
                    raise StoreError("state_journal_too_large", "too large")
                if total_bytes + size > MAX_SPOOL_TOTAL_BYTES:
                    reasons["total_size_limit"] = len(paths) - index
                    break
                total_bytes += size
                raw = _read_regular_nofollow(path, MAX_SPOOL_FILE_BYTES)
                raw_value = json.loads(
                    raw.decode("utf-8"), parse_constant=_reject_json_constant)
                value = self._parse(raw)
                if (raw_value.get("schema_version") != 1
                        and path != self._target(value["key"])):
                    raise StoreError("state_journal_bad_name", "wrong key")
                entries.append(value)
            except (OSError, StoreError, TypeError, ValueError,
                    RecursionError, UnicodeError):
                reasons["invalid_or_unreadable"] = (
                    reasons.get("invalid_or_unreadable", 0) + 1)
        omitted = sum(reasons.values())
        return PendingSnapshot(
            entries, source_count, len(entries), omitted, reasons)

    def _upgrade_v1_locked(self, source: Path, raw: bytes,
                           operation: dict) -> dict:
        self.migration_backup_path.mkdir(parents=True, exist_ok=True)
        backup = self.migration_backup_path / (
            f"{source.name}.v1-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
        PersistentSaveOutbox._write_bytes(backup, raw)
        PersistentSaveOutbox._fsync_directory(self.migration_backup_path)
        target = self._target(operation["key"])
        if target == source:
            self._rewrite_locked(target, operation)
            return operation
        with self._key_lock(operation["key"]):
            target_existed = target.is_file()
            if target_existed:
                existing = self._parse(_read_regular_nofollow(
                    target, MAX_SPOOL_FILE_BYTES))
                if operation["kind"] == existing["kind"] == "progress":
                    operation = self._merge_progress_operations(
                        existing, operation)
                elif self._order(operation) <= self._order(existing):
                    operation = existing
            self._rewrite_locked(target, operation)
            source.unlink()
            PersistentSaveOutbox._fsync_directory(self.path)
            if target_existed:
                self._count = max(0, self._count - 1)
        return operation

    def _rewrite_locked(self, target: Path, operation: dict) -> None:
        encoded = canonical_json(operation).encode("utf-8")
        temp = self.path / f".{target.name}.{uuid.uuid4().hex}.tmp"
        PersistentSaveOutbox._write_bytes(temp, encoded)
        try:
            os.replace(temp, target)
            PersistentSaveOutbox._fsync_directory(self.path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def remove_if_current(self, key: str, payload_hash: str) -> bool:
        with self._lock, self._key_lock(key):
            target = self._target(key)
            try:
                current = self._parse(_read_regular_nofollow(
                    target, MAX_SPOOL_FILE_BYTES))
                if current["payload_hash"] != payload_hash:
                    return False
                target.unlink()
                PersistentSaveOutbox._fsync_directory(self.path)
                self._count = max(0, self._count - 1)
                return True
            except FileNotFoundError:
                return True
            except (OSError, StoreError, TypeError, ValueError,
                    RecursionError):
                return False

    def reject_and_restore_if_current(
            self, key: str, payload_hash: str,
            previous_operation: Optional[dict], reason: str) -> bool:
        """Quarantine a rejected winner through a startup-recoverable marker."""
        safe_reason = "".join(
            char if char.isalnum() or char in "-_" else "-"
            for char in str(reason))[:80] or "rejected"
        with self._lock, self._key_lock(key):
            target = self._target(key)
            try:
                current = self._parse(_read_regular_nofollow(
                    target, MAX_SPOOL_FILE_BYTES))
            except (FileNotFoundError, OSError, StoreError, TypeError,
                    ValueError, RecursionError):
                return False
            if current["payload_hash"] != payload_hash:
                return False
            previous = None
            if previous_operation is not None:
                try:
                    previous = self._parse(
                        canonical_json(previous_operation).encode("utf-8"))
                except (MutationError, StoreError, TypeError, ValueError,
                        RecursionError, MemoryError):
                    return False
                if previous["key"] != key:
                    return False
            marker = self.path / (
                f".reject-{target.stem}-{uuid.uuid4().hex}.txn")
            temporary = marker.with_suffix(".tmp")
            transaction = {
                "version": 2, "key": key,
                "rejected_payload_hash": payload_hash,
                "previous_operation": previous,
                "reason": safe_reason,
            }
            transaction["marker_hash"] = self._digest(transaction)
            try:
                PersistentSaveOutbox._write_bytes(
                    temporary, canonical_json(transaction).encode("utf-8"))
                os.replace(temporary, marker)
                PersistentSaveOutbox._fsync_directory(self.path)
            except (MutationError, OSError):
                return False
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            if not self._complete_reject_transaction(marker, transaction):
                return False
            return True

    def _complete_reject_transaction(self, marker: Path,
                                     transaction: dict) -> bool:
        version = transaction.get("version") if isinstance(transaction, dict) else None
        if version == 2:
            expected = self._digest({
                name: value for name, value in transaction.items()
                if name != "marker_hash"})
            if (set(transaction) != {
                    "version", "key", "rejected_payload_hash",
                    "previous_operation", "reason", "marker_hash"}
                    or transaction.get("marker_hash") != expected):
                return False
        elif version != 1:
            return False
        key = transaction.get("key")
        previous = transaction.get("previous_operation")
        rejected_hash = transaction.get("rejected_payload_hash")
        reason = str(transaction.get("reason") or "rejected")
        if not isinstance(key, str) or not isinstance(rejected_hash, str):
            return False
        target = self._target(key)
        try:
            current = self._parse(_read_regular_nofollow(
                target, MAX_SPOOL_FILE_BYTES))
        except FileNotFoundError:
            current = None
        except (OSError, StoreError, TypeError, ValueError, RecursionError):
            return False
        if current is not None and current["payload_hash"] == rejected_hash:
            if not self._quarantine_locked(target, reason):
                return False
            current = None
        if previous is not None and current is None:
            try:
                previous = self._parse(canonical_json(previous).encode("utf-8"))
                if previous["key"] != key:
                    return False
                self._rewrite_locked(target, previous)
                self._count += 1
            except (MutationError, OSError, StoreError, TypeError,
                    ValueError, RecursionError, MemoryError):
                return False
        try:
            marker.unlink()
            PersistentSaveOutbox._fsync_directory(self.path)
        except OSError:
            return False
        return True

    def _quarantine_transaction(self, marker: Path, reason: str) -> None:
        try:
            self.quarantine_path.mkdir(parents=True, exist_ok=True)
            target = self.quarantine_path / (
                f"{marker.name}.{reason}-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
            os.replace(marker, target)
            PersistentSaveOutbox._fsync_directory(self.quarantine_path)
            PersistentSaveOutbox._fsync_directory(self.path)
        except OSError:
            return
        self.quarantined_count += 1
        self._update_notice()

    def _recover_reject_transactions(self) -> None:
        """Finish reject markers and legacy restore files after a crash."""
        if not self.path.is_dir():
            return
        for temporary in sorted(
                self.path.glob(".reject-*.tmp"))[:MAX_SPOOL_FILES]:
            self._quarantine_transaction(temporary, "incomplete-reject")
        for marker in sorted(self.path.glob(".reject-*.txn"))[:MAX_SPOOL_FILES]:
            try:
                transaction = json.loads(
                    _read_regular_nofollow(
                        marker, MAX_SPOOL_FILE_BYTES).decode("utf-8"),
                    parse_constant=_reject_json_constant)
                if not isinstance(transaction, dict):
                    continue
                key = transaction.get("key")
                if not isinstance(key, str):
                    raise StoreError(
                        "invalid_reject_transaction", "reject key is invalid")
                with self._key_lock(key):
                    if not self._complete_reject_transaction(marker, transaction):
                        raise StoreError(
                            "invalid_reject_transaction",
                            "reject transaction hash or contents are invalid")
            except (OSError, StoreError, TypeError, ValueError,
                    RecursionError, UnicodeError):
                self._quarantine_transaction(marker, "invalid-reject")
        for restore in sorted(self.path.glob(".*.restore"))[:MAX_SPOOL_FILES]:
            try:
                previous = self._parse(_read_regular_nofollow(
                    restore, MAX_SPOOL_FILE_BYTES))
                target = self._target(previous["key"])
                with self._key_lock(previous["key"]):
                    if not target.exists():
                        os.replace(restore, target)
                        PersistentSaveOutbox._fsync_directory(self.path)
                    else:
                        current = self._parse(_read_regular_nofollow(
                            target, MAX_SPOOL_FILE_BYTES))
                        if current["payload_hash"] == previous["payload_hash"]:
                            restore.unlink()
            except (OSError, StoreError, TypeError, ValueError,
                    RecursionError):
                continue

    def recover_transactions(self) -> None:
        """Retry crash markers after a transient filesystem failure clears."""
        with self._lock:
            self._recover_reject_transactions()
            self.refresh_count()

    def high_water(self) -> int:
        """Highest revision in active pending state journals."""
        return max(
            (entry["logical_revision"]
             for entry in self.list_entries(MAX_SPOOL_FILES)),
            default=0)

    def count(self) -> int:
        with self._lock:
            return self._count

    def refresh_count(self) -> int:
        count = 0
        try:
            with os.scandir(self.path) as entries:
                for entry in entries:
                    if entry.is_file() and entry.name.endswith(".json"):
                        count += 1
                        if count >= MAX_SPOOL_FILES:
                            break
        except (FileNotFoundError, NotADirectoryError, OSError):
            count = 0
        with self._lock:
            self._count = count
        return count

    def has_key(self, key: str) -> bool:
        with self._lock, self._key_lock(key):
            try:
                value = self._parse(_read_regular_nofollow(
                    self._target(key), MAX_SPOOL_FILE_BYTES))
            except (OSError, StoreError, TypeError, ValueError,
                    RecursionError):
                return False
        return value["key"] == key

    def read_key(self, key: str) -> Optional[dict]:
        """Read one semantic key without scanning unrelated journal files."""
        with self._lock, self._key_lock(key):
            try:
                value = self._parse(_read_regular_nofollow(
                    self._target(key), MAX_SPOOL_FILE_BYTES))
            except FileNotFoundError:
                return None
            except (OSError, StoreError, TypeError, ValueError,
                    RecursionError):
                return None
        return value if value["key"] == key else None

    def probe_writable(self) -> bool:
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            probe = self.path / f".state-probe-{uuid.uuid4().hex}"
            PersistentSaveOutbox._write_bytes(probe, b"ok")
            probe.unlink()
            PersistentSaveOutbox._fsync_directory(self.path)
        except OSError:
            return False
        return True


class LocalWriteWorker:
    """One serial executor with bounded lifecycle tracking."""

    def __init__(self, thread_name: str = "games-local-store"):
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=thread_name)
        self._condition = threading.Condition(threading.RLock())
        self._pending: set[Future] = set()
        self._closed = False

    def submit(self, operation: Callable, *args, **kwargs) -> Future:
        with self._condition:
            if self._closed:
                raise RuntimeError("LocalWriteWorker is closed")
            future = self._executor.submit(operation, *args, **kwargs)
            self._pending.add(future)
            future.add_done_callback(self._finished)
            return future

    def _finished(self, future: Future) -> None:
        with self._condition:
            self._pending.discard(future)
            self._condition.notify_all()

    def drain(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._pending:
                remaining = (None if deadline is None
                             else deadline - time.monotonic())
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self, *, cancel_pending: bool = False,
              timeout: float = 10.0) -> bool:
        with self._condition:
            if self._closed:
                return True
            self._closed = True
        drained = self.drain(max(0.0, timeout))
        self._executor.shutdown(
            wait=drained, cancel_futures=cancel_pending)
        return drained


def _preserve_corrupt_database(path: Path) -> Optional[Path]:
    if not path.is_file():
        return None
    backup = path.with_name(
        f"{path.name}.corrupt-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
    os.replace(path, backup)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            try:
                os.replace(sidecar, Path(f"{backup}{suffix}"))
            except OSError:
                pass
    return backup


class LocalBackendClient:
    """Default desktop data service; Flask, ports and requests are optional."""

    is_local = True
    capabilities = frozenset({
        "scores", "leaderboards", "profiles", "settings", "progress",
        "save_slots", "durable_score_outbox", "durable_state_outbox",
    })

    def __init__(self, store: Optional[LocalGameStore | Path | str] = None,
                 db_path: Optional[Path | str] = None,
                 legacy_db_path: Optional[Path | str] = None,
                 outbox_path: Optional[Path | str] = None,
                 defer_initialization: bool = False):
        if store is not None and not isinstance(store, LocalGameStore):
            if db_path is not None:
                raise TypeError("database path was provided twice")
            db_path = store
            store = None
        self.initialization_error: Optional[str] = None
        self.recovery_notice: Optional[str] = None
        self._permanent_initialization_error = False
        selected = (Path(db_path) if db_path is not None
                    else (store.db_path if store is not None
                          else default_database_path()))
        if (store is None and legacy_db_path is None and db_path is None
                and not os.environ.get("GAMES_DB")):
            legacy_db_path = (Path(__file__).resolve().parents[1]
                              / "data" / "scores.db")
        self._application_session = recovered_application_session(
            selected, timeout=2.0)
        if store is None and not defer_initialization:
            try:
                store = LocalGameStore(selected, legacy_db_path=legacy_db_path)
            except sqlite3.DatabaseError as exc:
                message = str(exc).lower()
                corrupt = "malformed" in message or "not a database" in message
                backup = None
                if corrupt:
                    try:
                        backup = _preserve_corrupt_database(selected)
                        store = LocalGameStore(
                            selected, legacy_db_path=legacy_db_path)
                    except (OSError, sqlite3.Error, StoreError):
                        store = None
                else:
                    store = None
                if store is None:
                    self.initialization_error = "本机成绩库暂时不可用"
                elif backup is not None:
                    self.recovery_notice = (
                        f"损坏的成绩库已保留为 {backup.name}，已创建新库")
            except StoreError as exc:
                store = None
                if exc.code == "unsupported_schema":
                    self._permanent_initialization_error = True
                    self.initialization_error = "本机成绩库版本高于当前程序，未作修改"
                else:
                    self.initialization_error = "本机成绩库迁移失败，原库未被覆盖"
            except OSError:
                store = None
                self.initialization_error = "本机成绩目录暂时不可用"
        self.store = store
        self._selected_db_path = selected
        self._legacy_db_path = legacy_db_path

        if outbox_path is None:
            spool_path = selected.with_name("pending")
            legacy_outbox = selected.with_name("pending_saves.json")
        else:
            supplied = Path(outbox_path)
            if supplied.suffix.lower() == ".json":
                spool_path = supplied.with_suffix("")
                legacy_outbox = supplied
            else:
                spool_path = supplied
                legacy_outbox = None
        self.outbox = PersistentSaveOutbox(
            spool_path, legacy_path=legacy_outbox)
        # Startup only needs to know whether work exists. Parsing, upgrading,
        # quarantining and fsyncing every envelope belongs on the worker.
        had_pending_scores = self.outbox.has_entries()
        self.state_outbox = PersistentStateOutbox(
            spool_path.with_name(f"{spool_path.name}-state"))
        notices = [self.recovery_notice,
                   getattr(store, "migration_notice", None),
                   self.outbox.recovery_notice,
                   self.state_outbox.recovery_notice]
        self.recovery_notice = "；".join(item for item in notices if item) or None

        self._worker = LocalWriteWorker("games-local-write")
        self._read_worker = LocalWriteWorker("games-local-read")
        self._closed = False
        self._lock = threading.RLock()
        self._state_publish_lock = threading.RLock()
        self._pending_envelopes: dict[str, tuple[PendingSaveEnvelope,
                                                 ScoreMutation]] = {}
        self._non_durable: dict[str, tuple[ScoreMutation, float]] = {}
        self._non_durable_state: dict[str, dict] = {}
        self._unpublished_state: set[str] = set()
        self._pending_state_count = self.state_outbox.count()
        self._save_events: deque[SaveEvent] = deque(maxlen=512)
        self._save_status: dict[str, SaveEvent] = {}
        self._local_state_events: deque[LocalStateEvent] = deque(maxlen=512)
        self._local_state_status: dict[str, LocalStateEvent] = {}
        self._state_status_refreshing: set[str] = set()
        self._save_status_refreshing: set[str] = set()
        self._state_status_refreshed_at: dict[str, float] = {}
        self._save_status_refreshed_at: dict[str, float] = {}
        self._slot_load_operations: dict[tuple[str, str, str], Future] = {}
        high_water = 0
        if self.store is not None:
            try:
                high_water = self.store.state_high_water()
            except (OSError, sqlite3.Error, StoreError):
                high_water = 0
        self._last_state_revision = max(time.time_ns(), high_water)
        self._retrying: set[str] = set()
        self._outbox_writable = self.outbox.probe_writable()
        self._state_outbox_writable = self.state_outbox.probe_writable()
        self.last_read_error: Optional[str] = None
        self._last_pending_scan = time.monotonic()
        self._retry_scan_future: Optional[Future] = None
        self._reopen_failure_count = 0
        self._next_reopen_at = 0.0
        self._reopen_state = "ready" if self.store is not None else "transient"
        self._last_reopen_error: Optional[str] = None
        self._retry_failure_count = 0
        self._next_auto_retry_at = 0.0
        if self.store is None and defer_initialization:
            self._read_worker.submit(self._try_reopen_store)
        self._read_worker.submit(self._refresh_state_high_water)
        if had_pending_scores or self._pending_state_count:
            self.retry_failed_saves()
        elif self.store is not None:
            self._worker.submit(self._run_maintenance)

    @property
    def pending_saves_are_durable(self) -> bool:
        with self._lock:
            return (not self._non_durable and not self._non_durable_state
                    and not self._unpublished_state)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("LocalBackendClient is closed")

    def _read(self, operation: Callable, *args, **kwargs):
        self._ensure_open()
        if self.store is None:
            self._try_reopen_store()
        if self.store is None:
            self.last_read_error = "本机记录暂时不可读"
            raise StoreError("database_unavailable", self.last_read_error,
                             503, retryable=True)
        try:
            result = operation(*args, **kwargs)
        except (StoreError, sqlite3.Error, OSError) as exc:
            self.last_read_error = "本机记录暂时不可读"
            if isinstance(exc, StoreError):
                raise
            raise StoreError("database_unavailable", self.last_read_error,
                             503, retryable=True) from exc
        self.last_read_error = None
        return result

    def _try_reopen_store(self) -> bool:
        with self._lock:
            if self.store is not None:
                return True
            if self._permanent_initialization_error:
                return False
            if time.monotonic() < self._next_reopen_at:
                return False
            try:
                reopened = LocalGameStore(
                    self._selected_db_path,
                    legacy_db_path=self._legacy_db_path)
            except StoreError as exc:
                if exc.code == "unsupported_schema":
                    self._permanent_initialization_error = True
                    self._reopen_state = "permanent_newer_schema"
                    self.initialization_error = "本机成绩库版本高于当前程序，未作修改"
                else:
                    self._reopen_state = "repair_required"
                    self._next_reopen_at = time.monotonic() + 60.0
                self._last_reopen_error = exc.code
                return False
            except sqlite3.Error as exc:
                failure = classify_sqlite_error(exc)
                if failure.kind is StorageErrorKind.CORRUPT:
                    try:
                        backup = _preserve_corrupt_database(
                            self._selected_db_path)
                        reopened = LocalGameStore(
                            self._selected_db_path,
                            legacy_db_path=self._legacy_db_path)
                    except (OSError, sqlite3.Error, StoreError):
                        pass
                    else:
                        self.store = reopened
                        self.initialization_error = None
                        self._reopen_failure_count = 0
                        self._next_reopen_at = 0.0
                        self._reopen_state = "ready"
                        self._last_reopen_error = None
                        if backup is not None:
                            self.recovery_notice = (
                                f"损坏的成绩库已保留为 {backup.name}，已创建新库")
                        return True
                self._reopen_failure_count += 1
                self._reopen_state = (
                    "transient" if failure.retryable else "repair_required")
                delay = (min(60.0, 2.0 ** self._reopen_failure_count)
                         if failure.retryable else 60.0)
                self._next_reopen_at = time.monotonic() + delay
                self._last_reopen_error = failure.code
                return False
            except OSError as exc:
                failure = classify_os_error(exc)
                self._reopen_failure_count += 1
                self._reopen_state = "transient"
                self._next_reopen_at = time.monotonic() + min(
                    60.0, 2.0 ** self._reopen_failure_count)
                self._last_reopen_error = failure.code
                return False
            self.store = reopened
            self.initialization_error = None
            self._reopen_failure_count = 0
            self._next_reopen_at = 0.0
            self._reopen_state = "ready"
            self._last_reopen_error = None
            if reopened.migration_notice:
                self.recovery_notice = reopened.migration_notice
            try:
                high_water = reopened.state_high_water()
            except (OSError, sqlite3.Error, StoreError):
                high_water = 0
            with self._lock:
                self._last_state_revision = max(
                    self._last_state_revision, high_water)
        return True

    def _run_maintenance(self) -> None:
        try:
            protected = tuple(
                component["operation_id"]
                for operation in self.state_outbox.list_entries()
                if operation["method"] == "merge_progress"
                for component in operation.get("components", ()))
            self.store.maintenance(protected) if self.store is not None else None
        except (sqlite3.Error, OSError, StoreError):
            # Housekeeping is opportunistic and must never make records
            # unavailable. A later launch will try again.
            pass

    def health(self) -> bool:
        try:
            return self.storage_status().ok
        except (StoreError, OSError):
            return False

    def health_async(self) -> Future:
        return self._read_worker.submit(self.health)

    def storage_status(self) -> StorageStatus:
        self._outbox_writable = self.outbox.probe_writable()
        self._state_outbox_writable = self.state_outbox.probe_writable()
        if self.store is None:
            self._try_reopen_store()
        if self.store is None:
            return StorageStatus(
                ok=False, readable=False, writable=False,
                outbox_writable=(self._outbox_writable
                                 and self._state_outbox_writable),
                score_outbox_writable=self._outbox_writable,
                state_outbox_writable=self._state_outbox_writable,
                error_code="database_unavailable", retryable=True,
                recovery_notice=self.recovery_notice)
        return self.store.storage_status(
            recovery_notice=self.recovery_notice,
            score_outbox_writable=self._outbox_writable,
            state_outbox_writable=self._state_outbox_writable)

    def storage_status_async(self) -> Future:
        return self._read_worker.submit(self.storage_status)

    def list_games(self) -> list[dict]:
        return public_games()

    def list_games_async(self) -> Future:
        return completed_future(self.list_games())

    def leaderboard(self, game_id: str, limit: int = 10, **dimensions) -> list[dict]:
        self._try_reopen_store()
        return (self._read(self.store.leaderboard, game_id, limit, **dimensions)
                if self.store else self._read(lambda: []))

    def leaderboard_result(self, game_id: str, limit: int = 10,
                           **dimensions) -> DataResult:
        try:
            data = self.leaderboard(game_id, limit, **dimensions)
        except StoreError as exc:
            return DataResult(False, None, exc.code, exc.message, exc.retryable)
        return DataResult(True, data)

    def leaderboard_async(self, game_id: str, limit: int = 10,
                          **dimensions) -> Future:
        return self._read_worker.submit(
            self.leaderboard, game_id, limit, **dimensions)

    def recent(self, limit: int = 20, **dimensions) -> list[dict]:
        self._try_reopen_store()
        return (self._read(self.store.recent, limit, **dimensions)
                if self.store else self._read(lambda: []))

    def recent_async(self, limit: int = 20, **dimensions) -> Future:
        return self._read_worker.submit(self.recent, limit, **dimensions)

    def recent_result(self, limit: int = 20, **dimensions) -> DataResult:
        try:
            data = self.recent(limit, **dimensions)
        except StoreError as exc:
            return DataResult(False, None, exc.code, exc.message, exc.retryable)
        return DataResult(True, data)

    def stats(self, game_id: str, **dimensions) -> dict:
        self._try_reopen_store()
        return (self._read(self.store.stats, game_id, **dimensions)
                if self.store else self._read(lambda: {}))

    def stats_result(self, game_id: str, **dimensions) -> DataResult:
        try:
            data = self.stats(game_id, **dimensions)
        except StoreError as exc:
            return DataResult(False, None, exc.code, exc.message, exc.retryable)
        return DataResult(True, data)

    def last_profile(self) -> Optional[dict]:
        self._try_reopen_store()
        return (self._read(self.store.last_profile)
                if self.store else self._read(lambda: None))

    def last_profile_async(self) -> Future:
        return self._read_worker.submit(self.last_profile)

    def list_profiles_async(self) -> Future:
        self._ensure_open()
        return self._read_worker.submit(
            self._read_store_method, "list_profiles")

    def _write_store_method(self, method: str, *args):
        if self.store is None and not self._try_reopen_store():
            raise StoreError(
                "database_unavailable", "本机数据暂时不可写", 503, True)
        return getattr(self.store, method)(*args)

    def _new_state_operation(self, key: str, method: str,
                             args: tuple) -> dict:
        with self._lock:
            revision = max(
                self._last_state_revision + 1, time.time_ns())
            self._last_state_revision = revision
        return self.state_outbox._operation(
            key, method, args, revision, uuid.uuid4().hex)

    def _refresh_state_high_water(self) -> int:
        try:
            high_water = self.state_outbox.high_water()
        except (OSError, StoreError):
            return 0
        with self._lock:
            self._last_state_revision = max(
                self._last_state_revision, high_water)
        return high_water

    def _emit_local_state_event(self, operation: dict, state: SaveState,
                                result: dict) -> LocalStateEvent:
        event = LocalStateEvent(
            key=operation["key"],
            kind=operation["kind"],
            logical_revision=operation["logical_revision"],
            state=state,
            result=dict(result),
        )
        with self._lock:
            current = self._local_state_status.get(event.key)
            if (current is None
                    or event.logical_revision >= current.logical_revision):
                self._local_state_status[event.key] = event
            while len(self._local_state_status) > 1024:
                self._local_state_status.pop(
                    next(iter(self._local_state_status)))
            self._local_state_events.append(event)
        return event

    def _durable_state_write(self, operation: dict):
        with maintenance_lock(
                self._selected_db_path, exclusive=False,
                timeout=APPLICATION_MAINTENANCE_TIMEOUT_SECONDS):
            return self._durable_state_write_locked(operation)

    def _publish_state_journal(self, operation: dict):
        """Allocate and publish one revision as an indivisible local step."""
        key = operation["key"]
        with self._state_publish_lock:
            with self._lock:
                allocate_revision = (
                    operation["operation_id"] in self._unpublished_state)
            if allocate_revision:
                existing = self.state_outbox.read_key(key)
                observed = operation["logical_revision"]
                if (existing is not None
                        and PersistentStateOutbox._order(existing)
                        > PersistentStateOutbox._order(operation)):
                    allocate_revision = False
                elif existing is not None:
                    observed = max(observed, existing["logical_revision"])
                if allocate_revision:
                    try:
                        revision = self.state_outbox.next_revision(observed)
                    except (OSError, StoreError):
                        pass
                    else:
                        operation = PersistentStateOutbox.with_revision(
                            operation, revision)
                        with self._lock:
                            self._last_state_revision = max(
                                self._last_state_revision, revision)
            try:
                receipt = self.state_outbox.put(
                    key, operation["method"], tuple(operation["args"]),
                    logical_revision=operation["logical_revision"],
                    operation_id=operation["operation_id"],
                    components=operation.get("components"),
                    updated_at=operation.get("updated_at"))
            except (OSError, StoreError) as exc:
                return operation, None, exc
            return operation, receipt, None

    def _durable_state_write_locked(self, operation: dict):
        key = operation["key"]
        try:
            LocalGameStore.validate_state_operation(operation)
        except StoreError as exc:
            with self._lock:
                self._unpublished_state.discard(operation["operation_id"])
                self._non_durable_state.pop(key, None)
            result = {**exc.result(), "journal_unchanged": True}
            self._emit_local_state_event(
                operation, SaveState.PERMANENT_FAILURE, result)
            return result
        journal_operation = operation
        payload_hash = None
        previous_operation = None
        operation, receipt, journal_error = self._publish_state_journal(operation)
        journal_failure = (
            classify_os_error(journal_error)
            if isinstance(journal_error, OSError) else journal_error)
        if receipt is not None:
            payload_hash = receipt["payload_hash"]
            journal_operation = receipt["operation"]
            previous_operation = receipt["previous_operation"]
            with self._lock:
                self._unpublished_state.discard(operation["operation_id"])
                self._last_state_revision = max(
                    self._last_state_revision,
                    journal_operation["logical_revision"])
            replaying_same_intent = (
                not receipt["published"]
                and journal_operation["payload_hash"]
                == operation["payload_hash"])
            if not receipt["published"] and not replaying_same_intent:
                result = {
                    "ok": True, "superseded": True,
                    "durable_pending": self.state_outbox.has_key(key),
                    "winning_logical_revision":
                        journal_operation["logical_revision"],
                    "winning_operation_id": journal_operation["operation_id"],
                }
                with self._lock:
                    current = self._non_durable_state.get(key)
                    if (current is not None
                            and PersistentStateOutbox._order(current)
                            <= PersistentStateOutbox._order(journal_operation)):
                        self._non_durable_state.pop(key, None)
                self._emit_local_state_event(
                    operation, SaveState.SUPERSEDED, result)
                return result
            journal_failure = None
            with self._lock:
                self._pending_state_count = self.state_outbox.count()
        try:
            result = self._write_store_method(
                "apply_state_operation", journal_operation)
        except StoreError as exc:
            waiting_for_profile = (
                exc.code == "profile_not_found"
                and journal_operation["method"] != "ensure_profile"
                and bool(journal_operation["args"])
                and self.state_outbox.has_key(
                    f"profile:{journal_operation['args'][0]}"))
            if not exc.retryable and not waiting_for_profile and exc.code not in {
                    "database_unavailable", "schema_repair_required"}:
                rejected_preserved = False
                if payload_hash is not None:
                    rejected_preserved = (
                        self.state_outbox.reject_and_restore_if_current(
                            key, payload_hash, previous_operation, exc.code))
                with self._lock:
                    self._pending_state_count = self.state_outbox.count()
                    if previous_operation is not None and not rejected_preserved:
                        self._non_durable_state[key] = previous_operation
                    else:
                        self._non_durable_state.pop(key, None)
                    self._unpublished_state.discard(
                        operation["operation_id"])
                result = {
                    **exc.result(),
                    "rejected_journal_preserved": rejected_preserved,
                    "previous_pending_restored": (
                        rejected_preserved
                        and previous_operation is not None),
                }
                self._emit_local_state_event(
                    operation, SaveState.PERMANENT_FAILURE, result)
                return result
            failure_result = exc.result()
        except sqlite3.Error as exc:
            failure = classify_sqlite_error(exc)
            failure_result = failure.result()
        except OSError as exc:
            failure = classify_os_error(exc)
            failure_result = failure.result()
        else:
            if payload_hash is not None:
                self.state_outbox.remove_if_current(key, payload_hash)
            with self._lock:
                self._pending_state_count = self.state_outbox.count()
                self._non_durable_state.pop(key, None)
                self._unpublished_state.discard(operation["operation_id"])
            event_result = result if isinstance(result, dict) else {"ok": True}
            event_state = (
                SaveState.SUPERSEDED
                if event_result.get("superseded") else SaveState.COMMITTED)
            self._emit_local_state_event(
                journal_operation, event_state, event_result)
            return result

        durable = payload_hash is not None
        if not durable:
            with self._lock:
                current = self._non_durable_state.get(key)
                if (current is None
                        or PersistentStateOutbox._order(operation)
                        > PersistentStateOutbox._order(current)):
                    self._non_durable_state[key] = operation
                self._unpublished_state.discard(operation["operation_id"])
        result = {
            **failure_result,
            "durable_pending": durable,
            "pending_preserved": True,
        }
        if journal_failure is not None:
            result["journal_error"] = (
                journal_failure.code if journal_failure is not None
                else "state_journal_unavailable")
        self._emit_local_state_event(
            journal_operation,
            SaveState.DURABLE_PENDING if durable
            else SaveState.NON_DURABLE_PENDING,
            result)
        return result

    def _submit_state_operation(self, key: str, method: str,
                                *args) -> Future:
        operation = self._new_state_operation(key, method, args)
        with self._lock:
            self._unpublished_state.add(operation["operation_id"])
        try:
            future = self._worker.submit(self._durable_state_write, operation)
        except Exception:
            with self._lock:
                self._unpublished_state.discard(operation["operation_id"])
            raise
        future.add_done_callback(
            lambda completed: self._recover_unexpected_state_failure(
                operation, completed))
        return future

    def _recover_unexpected_state_failure(
            self, operation: dict, future: Future) -> None:
        if not future.cancelled():
            try:
                failure = future.exception()
            except Exception as exc:  # noqa: BLE001
                failure = exc
            if failure is None:
                return
        durable_operation = self.state_outbox.read_key(operation["key"])
        durable = durable_operation is not None
        with self._lock:
            self._unpublished_state.discard(operation["operation_id"])
            if not durable:
                current = self._non_durable_state.get(operation["key"])
                if (current is None
                        or PersistentStateOutbox._order(operation)
                        > PersistentStateOutbox._order(current)):
                    self._non_durable_state[operation["key"]] = operation
        event_operation = durable_operation or operation
        self._emit_local_state_event(
            event_operation,
            SaveState.DURABLE_PENDING if durable
            else SaveState.NON_DURABLE_PENDING,
            {"ok": False, "code": "state_worker_failure",
             "error": "本机状态写入任务异常，已保留待重试",
             "retryable": True, "pending_preserved": True,
             "durable_pending": durable})

    def _remove_state_journal(self, key: str, payload_hash: str) -> None:
        self.state_outbox.remove_if_current(key, payload_hash)
        with self._lock:
            self._pending_state_count = self.state_outbox.count()

    def _read_store_method(self, method: str, *args):
        if self.store is None and not self._try_reopen_store():
            raise StoreError(
                "database_unavailable", "本机数据暂时不可读", 503, True)
        return self._read(getattr(self.store, method), *args)

    def ensure_profile_async(self, display_name: str,
                             profile_id: Optional[str] = None) -> Future:
        self._ensure_open()
        profile_id = profile_id or uuid.uuid4().hex
        return self._submit_state_operation(
            f"profile:{profile_id}", "ensure_profile", display_name,
            profile_id)

    def set_setting_async(self, profile_id: str, key: str, value) -> Future:
        self._ensure_open()
        return self._submit_state_operation(
            f"setting:{profile_id}:{key}", "set_setting",
            profile_id, key, value)

    def set_progress_async(self, profile_id: str, game_id: str,
                           key: str, value,
                           ruleset_version: Optional[str] = None) -> Future:
        self._ensure_open()
        ruleset_version = (ruleset_version
                           or GAME_BY_ID[game_id].ruleset_version)
        return self._submit_state_operation(
            f"progress:{profile_id}:{game_id}:{ruleset_version}:{key}",
            "set_progress", profile_id, game_id, key, value,
            ruleset_version)

    def merge_progress_async(self, profile_id: str, game_id: str,
                             key: str, value,
                             ruleset_version: Optional[str] = None) -> Future:
        self._ensure_open()
        ruleset_version = (ruleset_version
                           or GAME_BY_ID[game_id].ruleset_version)
        return self._submit_state_operation(
            f"progress:{profile_id}:{game_id}:{ruleset_version}:{key}",
            "merge_progress", profile_id, game_id, key, value,
            ruleset_version)

    def get_progress_async(self, profile_id: str, game_id: str,
                           key: str, default=None,
                           ruleset_version: Optional[str] = None) -> Future:
        self._ensure_open()
        return self._read_worker.submit(
            self._read_store_method, "get_progress", profile_id, game_id,
            key, default, ruleset_version)

    def save_slot_async(self, profile_id: str, game_id: str,
                        slot_id: str, state,
                        ruleset_version: Optional[str] = None) -> Future:
        self._ensure_open()
        ruleset_version = (ruleset_version
                           or GAME_BY_ID[game_id].ruleset_version)
        return self._submit_state_operation(
            f"slot:{profile_id}:{game_id}:{slot_id}",
            "save_slot", profile_id, game_id, slot_id, state,
            ruleset_version)

    def publish_slot_intent(self, profile_id: str, game_id: str,
                            slot_id: str, state,
                            ruleset_version: Optional[str] = None) -> dict:
        """Synchronously journal a final slot intent without waiting on SQLite."""
        self._ensure_open()
        ruleset_version = (ruleset_version
                           or GAME_BY_ID[game_id].ruleset_version)
        key = f"slot:{profile_id}:{game_id}:{slot_id}"
        operation = self._new_state_operation(
            key, "save_slot",
            (profile_id, game_id, slot_id, state, ruleset_version))
        with maintenance_lock(
                self._selected_db_path, exclusive=False,
                timeout=APPLICATION_MAINTENANCE_TIMEOUT_SECONDS):
            LocalGameStore.validate_state_operation(operation)
            with self._state_publish_lock:
                existing = self.state_outbox.read_key(key)
                observed = operation["logical_revision"]
                if existing is not None:
                    observed = max(observed, existing["logical_revision"])
                revision = self.state_outbox.next_revision(observed)
                operation = PersistentStateOutbox.with_revision(
                    operation, revision)
                receipt = self.state_outbox.put(
                    key, operation["method"], tuple(operation["args"]),
                    logical_revision=operation["logical_revision"],
                    operation_id=operation["operation_id"],
                    components=operation.get("components"),
                    updated_at=operation["updated_at"])
        with self._lock:
            self._last_state_revision = max(self._last_state_revision, revision)
            self._pending_state_count = self.state_outbox.count()
        result = {
            "ok": True, "durable_pending": True,
            "payload_hash": receipt["payload_hash"],
        }
        self._emit_local_state_event(
            receipt["operation"], SaveState.DURABLE_PENDING, result)
        try:
            self._worker.submit(
                self._durable_state_write, receipt["operation"])
        except RuntimeError:
            # The journal is already durable; the next startup scan will
            # replay it if shutdown has started concurrently.
            pass
        return result

    def load_slot_async(self, profile_id: str, game_id: str,
                        slot_id: str) -> Future:
        self._ensure_open()
        return self._read_worker.submit(
            self._read_store_method, "load_slot", profile_id, game_id,
            slot_id)

    def ensure_profile_and_load_slot_async(
            self, display_name: str, profile_id: str,
            game_id: str, slot_id: str) -> Future:
        """Chain profile creation and slot read without occupying a worker."""
        self._ensure_open()
        operation_key = (profile_id, game_id, slot_id)
        with self._lock:
            current = self._slot_load_operations.get(operation_key)
            if current is not None and not current.done():
                return current
            outer = Future()
            self._slot_load_operations[operation_key] = outer

        ensure_future = self.ensure_profile_async(display_name, profile_id)

        def finish(value: SlotLoadResult) -> None:
            if not outer.done():
                outer.set_result(value)
            with self._lock:
                if self._slot_load_operations.get(operation_key) is outer:
                    self._slot_load_operations.pop(operation_key, None)

        def after_read(read_future: Future, profile_pending: bool) -> None:
            try:
                slot = read_future.result()
            except StoreError as exc:
                if profile_pending and exc.code == "profile_not_found":
                    finish(SlotLoadResult(
                        SlotLoadStatus.PROFILE_PENDING,
                        error_code=exc.code, error=exc.message,
                        retryable=True))
                    return
                finish(SlotLoadResult(
                    SlotLoadStatus.TEMPORARY_FAILURE,
                    error_code=exc.code, error=exc.message,
                    retryable=exc.retryable))
                return
            except (sqlite3.Error, OSError) as exc:
                failure = (classify_sqlite_error(exc)
                           if isinstance(exc, sqlite3.Error)
                           else classify_os_error(exc))
                finish(SlotLoadResult(
                    SlotLoadStatus.TEMPORARY_FAILURE,
                    error_code=failure.code, error=failure.message,
                    retryable=failure.retryable))
                return
            if slot is None:
                if profile_pending:
                    finish(SlotLoadResult(
                        SlotLoadStatus.PROFILE_PENDING,
                        error_code="profile_pending",
                        error="档案仍在等待写入", retryable=True))
                else:
                    finish(SlotLoadResult(SlotLoadStatus.NO_SLOT))
                return
            finish(SlotLoadResult(SlotLoadStatus.LOADED, slot=slot))

        def after_ensure(completed: Future) -> None:
            try:
                result = completed.result()
                profile_pending = (
                    isinstance(result, dict) and result.get("ok") is False)
                read_future = self._read_worker.submit(
                    self._read_store_method, "load_slot", profile_id,
                    game_id, slot_id)
            except Exception as exc:  # noqa: BLE001
                finish(SlotLoadResult(
                    SlotLoadStatus.TEMPORARY_FAILURE,
                    error_code="profile_prepare_failed", error=str(exc),
                    retryable=True))
                return
            read_future.add_done_callback(
                lambda future: after_read(future, profile_pending))

        ensure_future.add_done_callback(after_ensure)
        return outer

    def quarantine_slot_async(self, profile_id: str, game_id: str,
                              slot_id: str, reason: str) -> Future:
        self._ensure_open()
        return self._worker.submit(
            self._write_store_method, "quarantine_slot",
            profile_id, game_id, slot_id, reason)

    def _mark_spooled(self, envelope: PendingSaveEnvelope,
                      mutation: ScoreMutation) -> None:
        with self._lock:
            self._pending_envelopes[mutation.request_id] = (envelope, mutation)
            self._non_durable.pop(mutation.request_id, None)

    def _mark_committed(self, request_id: str) -> None:
        with self._lock:
            self._pending_envelopes.pop(request_id, None)
            self._non_durable.pop(request_id, None)
            self._retrying.discard(request_id)

    def _emit_save_event(self, mutation: ScoreMutation,
                         state: SaveState, result: dict) -> SaveEvent:
        event = SaveEvent(
            request_id=mutation.request_id,
            attempt_uuid=mutation.attempt_uuid,
            revision=mutation.revision,
            state=state,
            result=dict(result),
        )
        with self._lock:
            self._save_status[mutation.request_id] = event
            while len(self._save_status) > 1024:
                self._save_status.pop(next(iter(self._save_status)))
            self._save_events.append(event)
        return event

    def poll_save_events(self) -> list[SaveEvent]:
        with self._lock:
            events = list(self._save_events)
            self._save_events.clear()
        return events

    def poll_local_state_events(self) -> list[LocalStateEvent]:
        with self._lock:
            events = list(self._local_state_events)
            self._local_state_events.clear()
        return events

    def get_local_state_status(self, key: str) -> Optional[LocalStateEvent]:
        now = time.monotonic()
        with self._lock:
            event = self._local_state_status.get(key)
            operation = self._non_durable_state.get(key)
            last_refresh = self._state_status_refreshed_at.get(key, 0.0)
            if (key not in self._state_status_refreshing
                    and now - last_refresh >= STATUS_REFRESH_SECONDS):
                self._state_status_refreshing.add(key)
                try:
                    self._read_worker.submit(
                        self._refresh_local_state_status, key)
                except RuntimeError:
                    self._state_status_refreshing.discard(key)
        if operation is not None:
            return LocalStateEvent(
                key=key, kind=operation["kind"],
                logical_revision=operation["logical_revision"],
                state=SaveState.NON_DURABLE_PENDING,
                result={"ok": False, "pending_preserved": True,
                        "durable_pending": False, "reconstructed": True})
        return event

    def _refresh_local_state_status(self, key: str) -> None:
        try:
            operation = self.state_outbox.read_key(key)
            receipt = None
            merge_applied = False
            if self.store is not None:
                try:
                    receipt = self.store.get_state_receipt(key)
                    if (operation is not None
                            and operation["method"] == "merge_progress"):
                        merge_applied = self.store.state_merge_components_applied(
                            operation)
                except (OSError, sqlite3.Error, StoreError):
                    pass
            receipt_order = (
                (receipt["logical_revision"], receipt["operation_id"])
                if receipt is not None else None)
            if (operation is not None
                    and ((operation["method"] == "merge_progress"
                          and not merge_applied)
                         or receipt_order is None
                         or PersistentStateOutbox._order(operation)
                         > receipt_order)):
                event = LocalStateEvent(
                    key=key, kind=operation["kind"],
                    logical_revision=operation["logical_revision"],
                    state=SaveState.DURABLE_PENDING,
                    result={"ok": False, "pending_preserved": True,
                            "durable_pending": True, "reconstructed": True})
            elif receipt is not None:
                event = LocalStateEvent(
                    key=key,
                    kind=PersistentStateOutbox._kind(receipt["method"]),
                    logical_revision=receipt["logical_revision"],
                    state=SaveState.COMMITTED,
                    result={**receipt, "reconstructed": True})
            else:
                return
            with self._lock:
                current = self._local_state_status.get(key)
            if (current is None
                    or current.logical_revision < event.logical_revision
                    or current.state != event.state):
                self._emit_local_state_event(
                    {"key": event.key, "kind": event.kind,
                     "logical_revision": event.logical_revision},
                    event.state, event.result)
        finally:
            with self._lock:
                self._state_status_refreshing.discard(key)
                self._state_status_refreshed_at[key] = time.monotonic()
                while len(self._state_status_refreshed_at) > 1024:
                    self._state_status_refreshed_at.pop(
                        next(iter(self._state_status_refreshed_at)))

    def get_save_status(self, request_id: str) -> Optional[SaveEvent]:
        now = time.monotonic()
        with self._lock:
            event = self._save_status.get(request_id)
            pending = self._pending_envelopes.get(request_id)
            non_durable = self._non_durable.get(request_id)
            last_refresh = self._save_status_refreshed_at.get(request_id, 0.0)
            if (request_id not in self._save_status_refreshing
                    and now - last_refresh >= STATUS_REFRESH_SECONDS):
                self._save_status_refreshing.add(request_id)
                try:
                    self._read_worker.submit(
                        self._refresh_save_status, request_id)
                except RuntimeError:
                    self._save_status_refreshing.discard(request_id)
        if event is not None:
            return event
        mutation = pending[1] if pending is not None else (
            non_durable[0] if non_durable is not None else None)
        if mutation is None:
            return None
        durable = pending is not None
        return SaveEvent(
            request_id=request_id,
            attempt_uuid=mutation.attempt_uuid,
            revision=mutation.revision,
            state=(SaveState.DURABLE_PENDING if durable
                   else SaveState.RECOVERY_REQUIRED),
            result={"ok": False, "pending_preserved": True,
                    "durable_pending": durable,
                    "reconstructed": True},
        )

    def _refresh_save_status(self, request_id: str) -> None:
        try:
            receipt = None
            if self.store is not None:
                try:
                    receipt = self.store.get_save_receipt(request_id)
                except (OSError, sqlite3.Error, StoreError):
                    pass
            if receipt is None:
                return
            event = SaveEvent(
                request_id=request_id,
                attempt_uuid=str(receipt.get("attempt_uuid") or ""),
                revision=int(receipt.get("revision") or 0),
                state=SaveState.COMMITTED,
                result={**receipt, "reconstructed": True})
            with self._lock:
                self._save_status[request_id] = event
                while len(self._save_status) > 1024:
                    self._save_status.pop(next(iter(self._save_status)))
        finally:
            with self._lock:
                self._save_status_refreshing.discard(request_id)
                self._save_status_refreshed_at[request_id] = time.monotonic()
                while len(self._save_status_refreshed_at) > 1024:
                    self._save_status_refreshed_at.pop(
                        next(iter(self._save_status_refreshed_at)))

    def _save_mutation(self, mutation: ScoreMutation,
                       already_spooled: bool = False,
                       occurred_at: Optional[float] = None) -> dict:
        with maintenance_lock(
                self._selected_db_path, exclusive=False,
                timeout=APPLICATION_MAINTENANCE_TIMEOUT_SECONDS):
            return self._save_mutation_locked(
                mutation, already_spooled, occurred_at)

    def _save_mutation_locked(self, mutation: ScoreMutation,
                              already_spooled: bool = False,
                              occurred_at: Optional[float] = None) -> dict:
        occurred_at = time.time() if occurred_at is None else occurred_at
        spool_error: Optional[Exception] = None
        if not already_spooled:
            try:
                envelope = self.outbox.add_mutation(
                    mutation, created_at=occurred_at)
                self._mark_spooled(envelope, mutation)
                self._outbox_writable = True
            except (OSError, StoreError) as exc:
                if isinstance(exc, StoreError):
                    # The request ID is already bound to another payload.
                    # Reject before touching SQLite and retain the original.
                    return exc.result()
                spool_error = exc
                self._outbox_writable = False
        storage_failure: Optional[StorageFailure] = None
        try:
            if self.store is None and not self._try_reopen_store():
                message = ("schema repair required"
                           if self._reopen_state == "repair_required"
                           else "local store unavailable")
                raise sqlite3.OperationalError(message)
            result = self.store.record_mutation(
                mutation, occurred_at=occurred_at)
        except StoreError as exc:
            result = exc.result()
        except sqlite3.Error as exc:
            storage_failure = classify_sqlite_error(exc)
            result = storage_failure.result()
        except OSError as exc:
            storage_failure = classify_os_error(exc)
            result = storage_failure.result()
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("unexpected local score save failure", exc_info=exc)
            storage_failure = StorageFailure(
                StorageErrorKind.INTERNAL, "internal_save_error",
                "保存过程中发生未预期错误，记录已保留", False, True)
            result = storage_failure.result()
        else:
            try:
                self.outbox.remove(mutation.request_id)
            except OSError:
                pass
            self._mark_committed(mutation.request_id)
            self._emit_save_event(mutation, SaveState.COMMITTED, result)
            return result

        if spool_error is not None:
            with self._lock:
                self._non_durable[mutation.request_id] = (
                    mutation, occurred_at)
            result["error"] += "，且无法写入待保存队列"
        if storage_failure is not None:
            durable = spool_error is None
            result["durable_pending"] = durable
            result["pending_preserved"] = True
            if storage_failure.quarantine and durable:
                quarantined = self.outbox.quarantine_request(
                    mutation.request_id, result["code"])
                result["quarantined"] = quarantined
                if quarantined:
                    with self._lock:
                        self._pending_envelopes.pop(mutation.request_id, None)
                    self._emit_save_event(
                        mutation, SaveState.QUARANTINED, result)
                    return result
            state = (SaveState.RECOVERY_REQUIRED
                     if (not durable or storage_failure.kind
                         is StorageErrorKind.SCHEMA_REPAIR_REQUIRED)
                     else SaveState.DURABLE_PENDING)
            self._emit_save_event(mutation, state, result)
            return result

        # StoreError represents a permanent request-level decision. It is
        # safe to remove this mutation; storage failures above are preserved.
        result["durable_pending"] = False
        if not result.get("retryable", False):
            if already_spooled or spool_error is None:
                try:
                    self.outbox.remove(mutation.request_id)
                except OSError:
                    pass
            self._mark_committed(mutation.request_id)
            self._emit_save_event(
                mutation, SaveState.PERMANENT_FAILURE, result)
        return result

    @staticmethod
    def _normalize_or_result(**payload) -> ScoreMutation | dict:
        try:
            return normalize_score_mutation(**payload)
        except MutationError as exc:
            return StoreError.from_mutation(exc).result()
        except TypeError:
            return StoreError(
                "unknown_fields", "score payload contains unknown fields").result()

    def submit_score(self, game_id: str, player: str, score: int,
                     extra=None, replace: bool = False,
                     submission_id: Optional[int] = None,
                     request_id: Optional[str] = None,
                     attempt_uuid: Optional[str] = None,
                     revision: Optional[int] = None,
                     profile_id: Optional[str] = None,
                     mode: str = "classic",
                     ruleset_version: Optional[str] = None,
                     status: str = "completed") -> dict:
        future = self.submit_score_async(
            game_id, player, score, extra=extra, replace=replace,
            submission_id=submission_id, request_id=request_id,
            attempt_uuid=attempt_uuid, revision=revision,
            profile_id=profile_id, mode=mode,
            ruleset_version=ruleset_version, status=status)
        return future.result()

    def submit_score_async(self, game_id: str, player: str, score: int,
                           extra=None, replace: bool = False,
                           submission_id: Optional[int] = None,
                           request_id: Optional[str] = None,
                           attempt_uuid: Optional[str] = None,
                           revision: Optional[int] = None,
                           profile_id: Optional[str] = None,
                           mode: str = "classic",
                           ruleset_version: Optional[str] = None,
                           status: str = "completed") -> Future:
        self._ensure_open()
        normalized = self._normalize_or_result(
            game_id=game_id, player=player, score=score, extra=extra,
            replace=replace, submission_id=submission_id,
            request_id=request_id, attempt_uuid=attempt_uuid,
            revision=revision, profile_id=profile_id, mode=mode,
            ruleset_version=ruleset_version, status=status)
        if isinstance(normalized, dict):
            return completed_future(normalized)
        occurred_at = time.time()
        future = self._worker.submit(
            self._save_mutation, normalized, False, occurred_at)
        future.add_done_callback(
            lambda completed: self._recover_unexpected_score_failure(
                normalized, occurred_at, completed))
        return future

    def _recover_unexpected_score_failure(
            self, mutation: ScoreMutation, occurred_at: float,
            future: Future) -> None:
        if not future.cancelled():
            try:
                failure = future.exception()
            except Exception as exc:  # noqa: BLE001
                failure = exc
            if failure is None:
                return
        durable = False
        try:
            envelope, current = self.outbox._read_file(
                self.outbox._target(mutation.request_id))
            durable = current.payload_hash == mutation.payload_hash
        except (OSError, StoreError, TypeError, ValueError, RecursionError):
            envelope = None
        with self._lock:
            if durable and envelope is not None:
                self._pending_envelopes[mutation.request_id] = (
                    envelope, mutation)
            else:
                self._non_durable[mutation.request_id] = (
                    mutation, occurred_at)
        result = {
            "ok": False, "code": "score_worker_failure",
            "error": "本机成绩写入任务异常，已保留待重试",
            "retryable": True, "pending_preserved": True,
            "durable_pending": durable,
        }
        self._emit_save_event(
            mutation,
            SaveState.DURABLE_PENDING if durable
            else SaveState.NON_DURABLE_PENDING,
            result)

    submit_score_reliable_async = submit_score_async

    def failed_save_count(self) -> int:
        with self._lock:
            return (len(set(self._pending_envelopes) | set(self._non_durable))
                    + self._pending_state_count
                    + len(self._non_durable_state)
                    + len(self._unpublished_state))

    def _replay_state_entries(self) -> tuple[int, bool, bool]:
        with maintenance_lock(
                self._selected_db_path, exclusive=False,
                timeout=APPLICATION_MAINTENANCE_TIMEOUT_SECONDS):
            return self._replay_state_entries_locked()

    def _replay_state_entries_locked(self) -> tuple[int, bool, bool]:
        completed = 0
        blocked = False
        repair_blocked = False
        entries = self.state_outbox.list_entries(REPLAY_BATCH_SIZE)
        priority = {"ensure_profile": 0, "set_setting": 1,
                    "set_progress": 2, "merge_progress": 2,
                    "save_slot": 3}
        entries.sort(key=lambda entry: (
            priority.get(entry["method"], 99), entry["updated_at"]))
        entries = entries[:REPLAY_BATCH_SIZE]
        for entry in entries:
            try:
                result = self._write_store_method(
                    "apply_state_operation", entry)
            except StoreError as exc:
                if not exc.retryable and exc.code not in {
                        "database_unavailable", "schema_repair_required"}:
                    if self.state_outbox.reject_and_restore_if_current(
                            entry["key"], entry["payload_hash"], None,
                            exc.code):
                        self._emit_local_state_event(
                            entry, SaveState.PERMANENT_FAILURE,
                            {**exc.result(),
                             "rejected_journal_preserved": True})
                else:
                    blocked = True
                    repair_blocked = exc.code == "schema_repair_required"
                    break
            except sqlite3.Error as exc:
                blocked = True
                repair_blocked = (classify_sqlite_error(exc).kind
                                  is StorageErrorKind.SCHEMA_REPAIR_REQUIRED)
                break
            except OSError:
                blocked = True
                break
            else:
                if self.state_outbox.remove_if_current(
                        entry["key"], entry["payload_hash"]):
                    completed += 1
                    self._emit_local_state_event(
                        entry,
                        (SaveState.SUPERSEDED if result.get("superseded")
                         else SaveState.COMMITTED),
                        {**result, "replayed": True})
        with self._lock:
            self._pending_state_count = self.state_outbox.count()
        return completed, blocked, repair_blocked

    def _retry_all(self) -> int:
        self.state_outbox.recover_transactions()
        with self._lock:
            pending = list(self._pending_envelopes.values())[:REPLAY_BATCH_SIZE]
            non_durable = list(self._non_durable.values())
            non_durable_state = list(
                self._non_durable_state.values())[:REPLAY_BATCH_SIZE]
        completed, blocked, repair_blocked = self._replay_state_entries()
        for operation in non_durable_state:
            result = self._durable_state_write(operation)
            completed += int(result.get("ok") is True)
            blocked = blocked or bool(result.get("retryable"))
        for envelope, mutation in pending:
            try:
                self.outbox.increment_attempt(mutation.request_id)
            except (OSError, StoreError):
                pass
            result = self._save_mutation(
                mutation, already_spooled=True,
                occurred_at=envelope.created_at)
            completed += int(result.get("ok") is True)
            if (result.get("storage_error_kind")
                    == StorageErrorKind.SCHEMA_REPAIR_REQUIRED.value):
                blocked = True
                repair_blocked = True
                break
            if result.get("retryable") and result.get("durable_pending"):
                blocked = True
                break
        for mutation, occurred_at in non_durable:
            result = self._save_mutation(
                mutation, occurred_at=occurred_at)
            completed += int(result.get("ok") is True)
            blocked = blocked or bool(result.get("retryable"))
        with self._lock:
            if completed:
                self._retry_failure_count = 0
                self._next_auto_retry_at = 0.0
            elif blocked:
                self._retry_failure_count += 1
                delay = (60.0 if repair_blocked else
                         min(60.0, 2.0 ** min(self._retry_failure_count, 5)))
                self._next_auto_retry_at = time.monotonic() + delay
        return completed

    def _scan_and_schedule_retry(self) -> int:
        self.outbox.refresh_quarantine_count()
        self.state_outbox.refresh_count()
        discovered = {
            envelope.request_id: (envelope, mutation)
            for envelope, mutation in self.outbox.list_envelopes()
        }
        notice = self.outbox.recovery_notice
        state_notice = self.state_outbox.recovery_notice
        notices = [item for item in (notice, state_notice)
                   if item and item not in (self.recovery_notice or "")]
        if notices:
            self.recovery_notice = "；".join(
                item for item in (self.recovery_notice, *notices) if item)
        with self._lock:
            self._pending_envelopes.update(discovered)
            candidates = set(self._pending_envelopes) | set(self._non_durable)
            candidates.difference_update(self._retrying)
            state_count = self.state_outbox.count()
            self._pending_state_count = state_count
            non_durable_state_count = len(self._non_durable_state)
            if not candidates and not state_count and not non_durable_state_count:
                return 0
            self._retrying.update(candidates)
        future = self._worker.submit(self._retry_all)

        def finish(_future: Future) -> None:
            with self._lock:
                self._retrying.difference_update(candidates)

        future.add_done_callback(finish)
        return len(candidates) + state_count + non_durable_state_count

    def retry_failed_saves(self) -> Future:
        self._ensure_open()
        with self._lock:
            if (self._retry_scan_future is not None
                    and not self._retry_scan_future.done()):
                return self._retry_scan_future
            self._retry_scan_future = self._read_worker.submit(
                self._scan_and_schedule_retry)
            return self._retry_scan_future

    def poll_pending_saves(self, interval_seconds: float = 2.0) -> int:
        """Periodically discover pending files created by another process."""
        now = time.monotonic()
        with self._lock:
            if now < self._next_auto_retry_at:
                return 0
            if now - self._last_pending_scan < max(0.1, interval_seconds):
                return 0
            self._last_pending_scan = now
        future = self.retry_failed_saves()
        if not future.done():
            return 0
        try:
            return int(future.result())
        except (OSError, StoreError, RuntimeError):
            return 0

    def drain(self, timeout: Optional[float] = None) -> bool:
        # A pending-directory scan may enqueue a required replay write. Wait
        # for that hand-off before declaring the durability pipeline drained.
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            scan = self._retry_scan_future
        if scan is not None and not scan.done():
            remaining = (None if deadline is None
                         else max(0.0, deadline - time.monotonic()))
            try:
                scan.result(timeout=remaining)
            except TimeoutError:
                return False
            except (OSError, StoreError, RuntimeError):
                pass
        remaining = (None if deadline is None
                     else max(0.0, deadline - time.monotonic()))
        return self._worker.drain(remaining)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        # A running discovery scan can still enqueue a replay. Let it finish
        # while the writer is alive, then drain the resulting required write.
        self._read_worker.close(cancel_pending=True, timeout=2.0)
        self._worker.close(timeout=10.0)
        self._application_session.close()
