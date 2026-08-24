"""Non-blocking desktop facade over the local SQLite records store."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .catalog import public_games
from .mutation import (MutationError, ScoreMutation, canonical_json,
                       normalize_score_mutation)
from .service import DataResult, StorageStatus
from .store import LocalGameStore, StoreError, default_database_path

SPOOL_SCHEMA_VERSION = 1
SPOOL_FIELDS = frozenset({
    "schema_version", "request_id", "payload_hash", "attempt_uuid",
    "revision", "created_at", "attempt_count", "payload",
})


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
        if value.get("schema_version") != SPOOL_SCHEMA_VERSION:
            raise StoreError("unsupported_spool_version",
                             "pending save uses an unsupported version")
        if (type(value.get("created_at")) not in (int, float)
                or not math.isfinite(float(value["created_at"]))
                or type(value.get("attempt_count")) is not int
                or value["attempt_count"] < 0
                or not isinstance(value.get("payload"), dict)):
            raise StoreError("invalid_spool_envelope",
                             "pending save envelope has invalid field types")
        try:
            mutation = normalize_score_mutation(**value["payload"])
        except (MutationError, TypeError) as exc:
            message = (exc.message if isinstance(exc, MutationError)
                       else "pending save payload has unknown fields")
            raise StoreError("invalid_spool_payload", message) from exc
        if (value.get("request_id") != mutation.request_id
                or value.get("attempt_uuid") != mutation.attempt_uuid
                or value.get("revision") != mutation.revision
                or value.get("payload_hash") != mutation.payload_hash):
            raise StoreError("spool_hash_mismatch",
                             "pending save metadata does not match its payload")
        return cls(**value), mutation


class PersistentSaveOutbox:
    """Cross-process-safe spool with one immutable file per request."""

    def __init__(self, path: Path | str,
                 legacy_path: Optional[Path | str] = None):
        selected = Path(path)
        if selected.suffix.lower() == ".json":
            legacy_path = selected if legacy_path is None else Path(legacy_path)
            selected = selected.with_suffix("")
        self.path = selected
        self.legacy_path = Path(legacy_path) if legacy_path is not None else None
        self.quarantine_path = self.path.with_name(f"{self.path.name}-quarantine")
        self._lock = threading.RLock()
        self.quarantined_count = 0
        self.recovery_notice: Optional[str] = None
        self._migrate_legacy_file()

    def _target(self, request_id: str) -> Path:
        return self.path / f"{request_id}.json"

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

    def _quarantine(self, path: Path, reason: str) -> None:
        try:
            self.quarantine_path.mkdir(parents=True, exist_ok=True)
            target = self.quarantine_path / (
                f"{path.name}.{reason}-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
            os.replace(path, target)
        except OSError:
            return
        self.quarantined_count += 1

    def _quarantine_value(self, value, reason: str) -> None:
        try:
            self.quarantine_path.mkdir(parents=True, exist_ok=True)
            target = self.quarantine_path / (
                f"legacy-item.{reason}-{time.time_ns()}-{uuid.uuid4().hex[:8]}.json")
            target.write_text(
                canonical_json({"reason": reason, "value": value}),
                encoding="utf-8")
        except (OSError, StoreError):
            return
        self.quarantined_count += 1

    def _migrate_legacy_file(self) -> None:
        legacy = self.legacy_path
        if legacy is None or not legacy.is_file():
            return
        try:
            value = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._quarantine(legacy, "corrupt")
            self._update_notice()
            return
        if not isinstance(value, list):
            self._quarantine(legacy, "invalid-root")
            self._update_notice()
            return
        for item in value:
            try:
                if not isinstance(item, dict):
                    raise TypeError("item is not an object")
                mutation = normalize_score_mutation(**item)
                self.add_mutation(mutation)
            except (MutationError, StoreError, TypeError, OSError):
                self._quarantine_value(item, "invalid-item")
        migrated = legacy.with_name(
            f"{legacy.name}.migrated-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
        try:
            os.replace(legacy, migrated)
        except OSError:
            pass
        self._update_notice()

    def _update_notice(self) -> None:
        if self.quarantined_count:
            self.recovery_notice = (
                f"已隔离 {self.quarantined_count} 条无法恢复的待保存记录")

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

    def add_mutation(self, mutation: ScoreMutation) -> PendingSaveEnvelope:
        envelope = PendingSaveEnvelope.from_mutation(mutation)
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
                        raise StoreError(
                            "request_id_conflict",
                            "request_id already has a different pending payload", 409)
                    return existing
                except OSError as exc:
                    # Some filesystems do not support hard links. Exclusive
                    # target creation retains conflict safety; a crash can at
                    # worst leave one quarantinable partial file.
                    if target.exists():
                        existing, _ = self._read_file(target)
                        if existing.payload_hash != envelope.payload_hash:
                            raise StoreError(
                                "request_id_conflict",
                                "request_id already has a different pending payload", 409)
                        return existing
                    try:
                        self._write_bytes(target, encoded)
                    except FileExistsError:
                        existing, _ = self._read_file(target)
                        if existing.payload_hash != envelope.payload_hash:
                            raise StoreError(
                                "request_id_conflict",
                                "request_id already has a different pending payload", 409)
                        return existing
                    except OSError:
                        raise exc
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
        return envelope

    @staticmethod
    def _read_file(path: Path) -> tuple[PendingSaveEnvelope, ScoreMutation]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError("corrupt_spool_file",
                             "pending save is not valid JSON") from exc
        return PendingSaveEnvelope.parse(value)

    def list_envelopes(self) -> list[tuple[PendingSaveEnvelope, ScoreMutation]]:
        with self._lock:
            if not self.path.is_dir():
                return []
            result = []
            for path in sorted(self.path.glob("*.json")):
                try:
                    result.append(self._read_file(path))
                except StoreError as exc:
                    self._quarantine(path, exc.code)
            self._update_notice()
            return result

    def list(self) -> list[dict]:
        return [mutation.transport_payload()
                for _envelope, mutation in self.list_envelopes()]

    def remove(self, request_id: str) -> None:
        try:
            self._target(request_id).unlink()
        except FileNotFoundError:
            pass

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


class LocalWriteWorker:
    """One background worker; pygame never waits for SQLite or spool I/O."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="games-local-store")
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

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)


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
                 outbox_path: Optional[Path | str] = None):
        if store is not None and not isinstance(store, LocalGameStore):
            if db_path is not None:
                raise TypeError("database path was provided twice")
            db_path = store
            store = None
        self.initialization_error: Optional[str] = None
        self.recovery_notice: Optional[str] = None
        selected = (Path(db_path) if db_path is not None
                    else (store.db_path if store is not None
                          else default_database_path()))
        if store is None:
            if (legacy_db_path is None and db_path is None
                    and not os.environ.get("GAMES_DB")):
                legacy_db_path = (Path(__file__).resolve().parents[1]
                                  / "data" / "scores.db")
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
                    self.initialization_error = "本机成绩库版本高于当前程序，未作修改"
                else:
                    self.initialization_error = "本机成绩库迁移失败，原库未被覆盖"
            except OSError:
                store = None
                self.initialization_error = "本机成绩目录暂时不可用"
        self.store = store

        if outbox_path is None:
            spool_path = selected.with_name("pending")
            legacy_outbox = selected.with_name("pending_saves.json")
        else:
            supplied = Path(outbox_path)
            spool_path = supplied
            legacy_outbox = supplied if supplied.suffix.lower() == ".json" else None
        self.outbox = PersistentSaveOutbox(
            spool_path, legacy_path=legacy_outbox)
        loaded_envelopes = self.outbox.list_envelopes()
        notices = [self.recovery_notice,
                   getattr(store, "migration_notice", None),
                   self.outbox.recovery_notice]
        self.recovery_notice = "；".join(item for item in notices if item) or None

        self._worker = LocalWriteWorker()
        self._closed = False
        self._lock = threading.RLock()
        self._pending_envelopes: dict[str, tuple[PendingSaveEnvelope,
                                                 ScoreMutation]] = {
            envelope.request_id: (envelope, mutation)
            for envelope, mutation in loaded_envelopes
        }
        self._non_durable: dict[str, ScoreMutation] = {}
        self._retrying: set[str] = set()
        self._outbox_writable = self.outbox.probe_writable()
        self.last_read_error: Optional[str] = None
        if self._pending_envelopes:
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
        return self._worker.submit(self.health)

    def storage_status(self) -> StorageStatus:
        self._outbox_writable = self.outbox.probe_writable()
        if self.store is None:
            return StorageStatus(
                ok=False, readable=False, writable=False,
                outbox_writable=self._outbox_writable,
                error_code="database_unavailable", retryable=True,
                recovery_notice=self.recovery_notice)
        return self.store.storage_status(
            self._outbox_writable, self.recovery_notice)

    def storage_status_async(self) -> Future:
        return self._worker.submit(self.storage_status)

    def list_games(self) -> list[dict]:
        return public_games()

    def list_games_async(self) -> Future:
        return completed_future(self.list_games())

    def leaderboard(self, game_id: str, limit: int = 10, **dimensions) -> list[dict]:
        return self._read(self.store.leaderboard, game_id, limit,
                          **dimensions) if self.store else self._read(lambda: [])

    def leaderboard_result(self, game_id: str, limit: int = 10,
                           **dimensions) -> DataResult:
        try:
            data = self.leaderboard(game_id, limit, **dimensions)
        except StoreError as exc:
            return DataResult(False, None, exc.code, exc.message, exc.retryable)
        return DataResult(True, data)

    def leaderboard_async(self, game_id: str, limit: int = 10,
                          **dimensions) -> Future:
        return self._worker.submit(
            self.leaderboard, game_id, limit, **dimensions)

    def recent(self, limit: int = 20, **dimensions) -> list[dict]:
        return self._read(self.store.recent, limit,
                          **dimensions) if self.store else self._read(lambda: [])

    def recent_async(self, limit: int = 20, **dimensions) -> Future:
        return self._worker.submit(self.recent, limit, **dimensions)

    def recent_result(self, limit: int = 20, **dimensions) -> DataResult:
        try:
            data = self.recent(limit, **dimensions)
        except StoreError as exc:
            return DataResult(False, None, exc.code, exc.message, exc.retryable)
        return DataResult(True, data)

    def stats(self, game_id: str, **dimensions) -> dict:
        return self._read(self.store.stats, game_id,
                          **dimensions) if self.store else self._read(lambda: {})

    def stats_result(self, game_id: str, **dimensions) -> DataResult:
        try:
            data = self.stats(game_id, **dimensions)
        except StoreError as exc:
            return DataResult(False, None, exc.code, exc.message, exc.retryable)
        return DataResult(True, data)

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

    def _save_mutation(self, mutation: ScoreMutation,
                       already_spooled: bool = False) -> dict:
        spool_error: Optional[Exception] = None
        if not already_spooled:
            try:
                envelope = self.outbox.add_mutation(mutation)
                self._mark_spooled(envelope, mutation)
                self._outbox_writable = True
            except (OSError, StoreError) as exc:
                spool_error = exc
                self._outbox_writable = False
        try:
            if self.store is None:
                raise sqlite3.OperationalError("local store unavailable")
            result = self.store.record_mutation(mutation)
        except StoreError as exc:
            result = exc.result()
        except sqlite3.Error:
            result = {"ok": False, "code": "database_unavailable",
                      "error": "本机成绩库暂时不可写", "retryable": True}
        except OSError:
            result = {"ok": False, "code": "storage_unavailable",
                      "error": "本机成绩目录暂时不可写", "retryable": True}
        else:
            try:
                self.outbox.remove(mutation.request_id)
            except OSError:
                pass
            self._mark_committed(mutation.request_id)
            return result

        if spool_error is not None:
            if isinstance(spool_error, StoreError):
                # A payload/hash conflict is permanent. Keep the original
                # immutable spool file, report the new mutation as rejected,
                # and do not advertise a retry that can never succeed.
                return spool_error.result()
            with self._lock:
                self._non_durable[mutation.request_id] = mutation
            result["error"] += "，且无法写入待保存队列"
        result["durable_pending"] = (
            spool_error is None and result.get("retryable", False))
        if not result.get("retryable", False):
            try:
                self.outbox.remove(mutation.request_id)
            except OSError:
                pass
            self._mark_committed(mutation.request_id)
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
                     status: str = "completed", **_ignored) -> dict:
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
        return self._worker.submit(self._save_mutation, normalized)

    submit_score_reliable_async = submit_score_async

    def failed_save_count(self) -> int:
        with self._lock:
            return len(set(self._pending_envelopes) | set(self._non_durable))

    def _retry_all(self) -> int:
        discovered = {
            envelope.request_id: (envelope, mutation)
            for envelope, mutation in self.outbox.list_envelopes()
        }
        with self._lock:
            self._pending_envelopes.update(discovered)
            pending = list(self._pending_envelopes.values())
            non_durable = list(self._non_durable.values())
        completed = 0
        for _envelope, mutation in pending:
            result = self._save_mutation(mutation, already_spooled=True)
            completed += int(result.get("ok") is True)
            if result.get("retryable") and result.get("durable_pending"):
                break
        for mutation in non_durable:
            result = self._save_mutation(mutation)
            completed += int(result.get("ok") is True)
        return completed

    def retry_failed_saves(self) -> int:
        self._ensure_open()
        with self._lock:
            candidates = set(self._pending_envelopes) | set(self._non_durable)
            if candidates <= self._retrying:
                return 0
            self._retrying.update(candidates)
        future = self._worker.submit(self._retry_all)

        def finish(_future: Future) -> None:
            with self._lock:
                self._retrying.difference_update(candidates)

        future.add_done_callback(finish)
        return len(candidates)

    def drain(self, timeout: Optional[float] = None) -> bool:
        return self._worker.drain(timeout)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._worker.close()
