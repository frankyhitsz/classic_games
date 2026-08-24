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
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from .catalog import public_games
from .mutation import (MutationError, ScoreMutation, canonical_json,
                       normalize_score_mutation)
from .profile import ProfileIdentity, ProfileIdentityError
from .service import (DataResult, SaveEvent, SaveState, StorageErrorKind,
                      StorageStatus)
from .store import LocalGameStore, StoreError, default_database_path

SPOOL_SCHEMA_VERSION = 2
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
LOGGER = logging.getLogger(__name__)
SPOOL_FIELDS = frozenset({
    "schema_version", "request_id", "payload_hash", "attempt_uuid",
    "revision", "created_at", "attempt_count", "payload",
})


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
                or type(value.get("attempt_count")) is not int
                or value["attempt_count"] < 0
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
                 legacy_path: Optional[Path | str] = None):
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
        descriptor = os.open(
            lock_path, os.O_RDWR | os.O_CREAT, 0o600)
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
                updated = replace(envelope, attempt_count=envelope.attempt_count + 1)
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
            if path.stat().st_size > MAX_SPOOL_FILE_BYTES:
                raise StoreError(
                    "spool_file_too_large",
                    "pending save exceeds the 64 KiB limit")
            original = path.read_bytes()
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

    def list(self) -> list[dict]:
        return [mutation.transport_payload()
                for _envelope, mutation in self.list_envelopes()]

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
    """Latest-value journal for profile, settings, progress and save slots."""

    ALLOWED_METHODS = frozenset({
        "ensure_profile", "set_setting", "set_progress", "merge_progress",
        "save_slot",
    })

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.quarantine_path = self.path.with_name(
            f"{self.path.name}-quarantine")
        self._lock = threading.RLock()

    @staticmethod
    def _digest(value: dict) -> str:
        return hashlib.sha256(
            canonical_json(value).encode("utf-8")).hexdigest()

    def _target(self, key: str) -> Path:
        name = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.path / f"{name}.json"

    def put(self, key: str, method: str, args: tuple) -> str:
        if method not in self.ALLOWED_METHODS:
            raise StoreError("invalid_state_operation", "unsupported state operation")
        operation = {
            "schema_version": 1, "key": key, "method": method,
            "args": list(args), "updated_at": time.time(),
        }
        _validate_json_shape(operation)
        try:
            operation["payload_hash"] = self._digest(operation)
            encoded = canonical_json(operation).encode("utf-8")
        except (MutationError, RecursionError, MemoryError) as exc:
            if isinstance(exc, MutationError):
                raise StoreError.from_mutation(exc) from exc
            raise StoreError(
                "invalid_state_operation", "local state is too complex") from exc
        if len(encoded) > MAX_SPOOL_FILE_BYTES:
            raise StoreError("value_too_large", "pending local state is too large")
        with self._lock:
            self.path.mkdir(parents=True, exist_ok=True)
            target = self._target(key)
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
        return operation["payload_hash"]

    @classmethod
    def _parse(cls, raw: bytes) -> dict:
        value = json.loads(raw.decode("utf-8"),
                           parse_constant=_reject_json_constant)
        _validate_json_shape(value)
        if (not isinstance(value, dict)
                or set(value) != {"schema_version", "key", "method", "args",
                                  "updated_at", "payload_hash"}
                or value.get("schema_version") != 1
                or not isinstance(value.get("key"), str)
                or value.get("method") not in cls.ALLOWED_METHODS
                or not isinstance(value.get("args"), list)
                or type(value.get("updated_at")) not in (int, float)
                or not math.isfinite(float(value["updated_at"]))):
            raise StoreError(
                "invalid_state_journal", "invalid pending local state")
        payload_hash = value.pop("payload_hash")
        valid = cls._digest(value)
        value["payload_hash"] = payload_hash
        if payload_hash != valid:
            raise StoreError(
                "state_journal_hash_mismatch", "pending local state was modified")
        return value

    def list_entries(self) -> list[dict]:
        with self._lock:
            if not self.path.is_dir():
                return []
            entries = []
            for path in sorted(self.path.glob("*.json"))[:MAX_SPOOL_FILES]:
                try:
                    if path.stat().st_size > MAX_SPOOL_FILE_BYTES:
                        raise StoreError("state_journal_too_large", "too large")
                    value = self._parse(path.read_bytes())
                    if path != self._target(value["key"]):
                        raise StoreError("state_journal_bad_name", "wrong key")
                    entries.append(value)
                except (OSError, StoreError, ValueError, RecursionError):
                    try:
                        self.quarantine_path.mkdir(parents=True, exist_ok=True)
                        os.replace(
                            path, self.quarantine_path / (
                                f"{path.name}.invalid-{time.time_ns()}"))
                    except OSError:
                        pass
            return entries

    def remove_if_current(self, key: str, payload_hash: str) -> bool:
        with self._lock:
            target = self._target(key)
            try:
                current = self._parse(target.read_bytes())
                if current["payload_hash"] != payload_hash:
                    return False
                target.unlink()
                PersistentSaveOutbox._fsync_directory(self.path)
                return True
            except FileNotFoundError:
                return True
            except (OSError, StoreError, ValueError, RecursionError):
                return False

    def count(self) -> int:
        try:
            return sum(1 for path in self.path.glob("*.json") if path.is_file())
        except OSError:
            return 0

    def has_key(self, key: str) -> bool:
        try:
            value = self._parse(self._target(key).read_bytes())
        except (OSError, StoreError, ValueError, RecursionError):
            return False
        return value["key"] == key


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

    def close(self, *, cancel_pending: bool = False) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=cancel_pending)


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
        self.state_outbox = PersistentStateOutbox(
            spool_path.with_name(f"{spool_path.name}-state"))
        notices = [self.recovery_notice,
                   getattr(store, "migration_notice", None),
                   self.outbox.recovery_notice]
        self.recovery_notice = "；".join(item for item in notices if item) or None

        self._worker = LocalWriteWorker("games-local-write")
        self._read_worker = LocalWriteWorker("games-local-read")
        self._closed = False
        self._lock = threading.RLock()
        self._pending_envelopes: dict[str, tuple[PendingSaveEnvelope,
                                                 ScoreMutation]] = {}
        self._non_durable: dict[str, tuple[ScoreMutation, float]] = {}
        self._pending_state_count = self.state_outbox.count()
        self._save_events: deque[SaveEvent] = deque(maxlen=512)
        self._save_status: dict[str, SaveEvent] = {}
        self._retrying: set[str] = set()
        self._outbox_writable = self.outbox.probe_writable()
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
        if self.outbox.path.is_dir() or self._pending_state_count:
            self.retry_failed_saves()
        elif self.store is not None:
            self._worker.submit(self._run_maintenance)

    @property
    def pending_saves_are_durable(self) -> bool:
        with self._lock:
            return not self._non_durable

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
        return True

    def _run_maintenance(self) -> None:
        try:
            self.store.maintenance() if self.store is not None else None
        except (sqlite3.Error, OSError):
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
        if self.store is None:
            self._try_reopen_store()
        if self.store is None:
            return StorageStatus(
                ok=False, readable=False, writable=False,
                outbox_writable=self._outbox_writable,
                error_code="database_unavailable", retryable=True,
                recovery_notice=self.recovery_notice)
        return self.store.storage_status(
            self._outbox_writable, self.recovery_notice)

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

    def _durable_state_write(self, key: str, method: str, *args):
        try:
            payload_hash = self.state_outbox.put(key, method, args)
        except (OSError, StoreError) as exc:
            failure = (classify_os_error(exc) if isinstance(exc, OSError)
                       else None)
            if failure is not None:
                return {**failure.result(), "durable_pending": False}
            return exc.result()
        with self._lock:
            self._pending_state_count = self.state_outbox.count()
        try:
            result = self._write_store_method(method, *args)
        except StoreError as exc:
            waiting_for_profile = (
                exc.code == "profile_not_found" and method != "ensure_profile"
                and bool(args) and self.state_outbox.has_key(
                    f"profile:{args[0]}"))
            if not exc.retryable and not waiting_for_profile and exc.code not in {
                    "database_unavailable", "schema_repair_required"}:
                self.state_outbox.remove_if_current(key, payload_hash)
                with self._lock:
                    self._pending_state_count = self.state_outbox.count()
                return exc.result()
            return {**exc.result(), "durable_pending": True,
                    "pending_preserved": True}
        except sqlite3.Error as exc:
            failure = classify_sqlite_error(exc)
            return {**failure.result(), "durable_pending": True,
                    "pending_preserved": True}
        except OSError as exc:
            failure = classify_os_error(exc)
            return {**failure.result(), "durable_pending": True,
                    "pending_preserved": True}
        self.state_outbox.remove_if_current(key, payload_hash)
        with self._lock:
            self._pending_state_count = self.state_outbox.count()
        return result

    def _read_store_method(self, method: str, *args):
        if self.store is None and not self._try_reopen_store():
            raise StoreError(
                "database_unavailable", "本机数据暂时不可读", 503, True)
        return self._read(getattr(self.store, method), *args)

    def ensure_profile_async(self, display_name: str,
                             profile_id: Optional[str] = None) -> Future:
        self._ensure_open()
        profile_id = profile_id or uuid.uuid4().hex
        return self._worker.submit(
            self._durable_state_write, f"profile:{profile_id}",
            "ensure_profile", display_name, profile_id)

    def set_setting_async(self, profile_id: str, key: str, value) -> Future:
        self._ensure_open()
        return self._worker.submit(
            self._durable_state_write, f"setting:{profile_id}:{key}",
            "set_setting", profile_id, key, value)

    def set_progress_async(self, profile_id: str, game_id: str,
                           key: str, value,
                           ruleset_version: Optional[str] = None) -> Future:
        self._ensure_open()
        return self._worker.submit(
            self._durable_state_write,
            f"progress:{profile_id}:{game_id}:{ruleset_version or 'current'}:{key}",
            "set_progress", profile_id, game_id, key, value, ruleset_version)

    def merge_progress_async(self, profile_id: str, game_id: str,
                             key: str, value,
                             ruleset_version: Optional[str] = None) -> Future:
        self._ensure_open()
        return self._worker.submit(
            self._durable_state_write,
            f"progress:{profile_id}:{game_id}:{ruleset_version or 'current'}:{key}",
            "merge_progress", profile_id, game_id, key, value, ruleset_version)

    def get_progress_async(self, profile_id: str, game_id: str,
                           key: str, default=None,
                           ruleset_version: Optional[str] = None) -> Future:
        self._ensure_open()
        return self._read_worker.submit(
            self._read_store_method, "get_progress", profile_id, game_id,
            key, default, ruleset_version)

    def save_slot_async(self, profile_id: str, game_id: str,
                        slot_id: str, state) -> Future:
        self._ensure_open()
        return self._worker.submit(
            self._durable_state_write,
            f"slot:{profile_id}:{game_id}:{slot_id}",
            "save_slot", profile_id, game_id, slot_id, state)

    def load_slot_async(self, profile_id: str, game_id: str,
                        slot_id: str) -> Future:
        self._ensure_open()
        return self._read_worker.submit(
            self._read_store_method, "load_slot", profile_id, game_id,
            slot_id)

    def ensure_profile_and_load_slot_async(
            self, display_name: str, profile_id: str,
            game_id: str, slot_id: str) -> Future:
        """Serialize standalone profile creation before its first slot read."""
        self._ensure_open()

        def ensure_then_load():
            result = self._durable_state_write(
                f"profile:{profile_id}", "ensure_profile",
                display_name, profile_id)
            if isinstance(result, dict) and result.get("ok") is False:
                return None
            return self._read_store_method(
                "load_slot", profile_id, game_id, slot_id)

        return self._worker.submit(ensure_then_load)

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

    def get_save_status(self, request_id: str) -> Optional[SaveEvent]:
        with self._lock:
            event = self._save_status.get(request_id)
            if event is not None:
                return event
            pending = self._pending_envelopes.get(request_id)
            non_durable = self._non_durable.get(request_id)
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

    def _save_mutation(self, mutation: ScoreMutation,
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
        return self._worker.submit(
            self._save_mutation, normalized, False, occurred_at)

    submit_score_reliable_async = submit_score_async

    def failed_save_count(self) -> int:
        with self._lock:
            return (len(set(self._pending_envelopes) | set(self._non_durable))
                    + self._pending_state_count)

    def _replay_state_entries(self) -> tuple[int, bool, bool]:
        completed = 0
        blocked = False
        repair_blocked = False
        entries = self.state_outbox.list_entries()
        priority = {"ensure_profile": 0, "set_setting": 1,
                    "set_progress": 2, "merge_progress": 2,
                    "save_slot": 3}
        entries.sort(key=lambda entry: (
            priority.get(entry["method"], 99), entry["updated_at"]))
        entries = entries[:REPLAY_BATCH_SIZE]
        for entry in entries:
            try:
                self._write_store_method(entry["method"], *entry["args"])
            except StoreError as exc:
                if not exc.retryable and exc.code not in {
                        "database_unavailable", "schema_repair_required"}:
                    self.state_outbox.remove_if_current(
                        entry["key"], entry["payload_hash"])
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
        with self._lock:
            self._pending_state_count = self.state_outbox.count()
        return completed, blocked, repair_blocked

    def _retry_all(self) -> int:
        with self._lock:
            pending = list(self._pending_envelopes.values())[:REPLAY_BATCH_SIZE]
            non_durable = list(self._non_durable.values())
        completed, blocked, repair_blocked = self._replay_state_entries()
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
        discovered = {
            envelope.request_id: (envelope, mutation)
            for envelope, mutation in self.outbox.list_envelopes()
        }
        notice = self.outbox.recovery_notice
        if notice and notice not in (self.recovery_notice or ""):
            self.recovery_notice = "；".join(
                item for item in (self.recovery_notice, notice) if item)
        with self._lock:
            self._pending_envelopes.update(discovered)
            candidates = set(self._pending_envelopes) | set(self._non_durable)
            candidates.difference_update(self._retrying)
            state_count = self.state_outbox.count()
            self._pending_state_count = state_count
            if not candidates and not state_count:
                return 0
            self._retrying.update(candidates)
        future = self._worker.submit(self._retry_all)

        def finish(_future: Future) -> None:
            with self._lock:
                self._retrying.difference_update(candidates)

        future.add_done_callback(finish)
        return len(candidates) + state_count

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
        self._read_worker.close(cancel_pending=True)
        self._worker.close()
