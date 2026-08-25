"""Crash-recoverable filesystem side of a local data import."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .store import StoreError

MAX_TRANSACTION_FILE_BYTES = 16 * 1024 * 1024
MAX_TRANSACTION_TOTAL_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class FileOperation:
    target: Path
    data: bytes | None


def _fsync_directory(path: Path) -> None:
    if os.name != "posix" or not path.is_dir():
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_file(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _ensure_safe_target(database: Path, target: Path) -> Path:
    parent = database.parent.resolve(strict=False)
    candidate = target.resolve(strict=False)
    try:
        candidate.relative_to(parent)
    except ValueError as exc:
        raise StoreError(
            "unsafe_import_target", "import file target escapes the data directory"
        ) from exc
    if candidate == database.resolve(strict=False):
        raise StoreError(
            "unsafe_import_target", "database cannot be a staged file target")
    # Publish to the resolved candidate. Ancestor aliases such as macOS's
    # /var -> /private/var must not be mistaken for a journal-owned symlink.
    target = candidate
    cursor = target
    while cursor != database.parent and cursor != cursor.parent:
        if cursor.exists() and cursor.is_symlink():
            raise StoreError(
                "unsafe_import_target", "import target contains a symbolic link")
        cursor = cursor.parent
    if target.exists() and not target.is_file():
        raise StoreError(
            "unsafe_import_target", "import target is not an ordinary file")
    return candidate


class ImportTransaction:
    """Prepared files plus a phase journal and database rollback image."""

    def __init__(self, database: Path, root: Path, journal: dict):
        self.database = database
        self.root = root
        self.journal = journal

    @classmethod
    def prepare(cls, database: Path, operations: list[FileOperation]):
        database = database.resolve(strict=False)
        root = database.parent / (
            f".{database.name}.import-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
        root.mkdir(mode=0o700)
        _fsync_directory(root.parent)
        records = []
        transaction_bytes = 0
        try:
            source = sqlite3.connect(str(database))
            rollback = sqlite3.connect(str(root / "database-before.sqlite"))
            try:
                source.backup(rollback)
                rollback.commit()
            finally:
                rollback.close()
                source.close()
            with (root / "database-before.sqlite").open("rb") as handle:
                os.fsync(handle.fileno())
            for index, operation in enumerate(operations):
                target = _ensure_safe_target(database, operation.target)
                relative = target.relative_to(database.parent.resolve(strict=False))
                staged_name = None
                if operation.data is not None:
                    transaction_bytes += len(operation.data)
                    if (len(operation.data) > MAX_TRANSACTION_FILE_BYTES
                            or transaction_bytes > MAX_TRANSACTION_TOTAL_BYTES):
                        raise StoreError(
                            "import_staging_too_large",
                            "staged import files exceed rollback limits")
                    staged_name = f"staged-{index}.bin"
                    _write_file(root / staged_name, operation.data)
                before_name = None
                had_before = target.is_file()
                if had_before:
                    before_name = f"before-{index}.bin"
                    size = target.stat().st_size
                    transaction_bytes += size
                    if (size > MAX_TRANSACTION_FILE_BYTES
                            or transaction_bytes > MAX_TRANSACTION_TOTAL_BYTES):
                        raise StoreError(
                            "import_target_too_large",
                            "existing import target exceeds rollback limits")
                    _write_file(root / before_name, target.read_bytes())
                records.append({
                    "target": relative.as_posix(),
                    "staged": staged_name,
                    "before": before_name,
                    "had_before": had_before,
                })
            journal = {
                "version": 1, "phase": "PREPARED",
                "database": database.name, "operations": records,
            }
            transaction = cls(database, root, journal)
            transaction._write_journal()
            return transaction
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    @classmethod
    def open(cls, database: Path, root: Path):
        try:
            journal = json.loads(
                (root / "journal.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StoreError(
                "import_recovery_required", "import phase journal is unreadable"
            ) from exc
        if (not isinstance(journal, dict) or journal.get("version") != 1
                or journal.get("database") != database.name
                or not isinstance(journal.get("operations"), list)):
            raise StoreError(
                "import_recovery_required", "import phase journal is invalid")
        supplied_hash = journal.get("journal_hash")
        expected_hash = hashlib.sha256(json.dumps(
            {key: value for key, value in journal.items()
             if key != "journal_hash"},
            ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        allowed_phases = {
            "PREPARED", "DB_APPLIED", "FILES_PUBLISHED",
            "COMPLETED", "ROLLED_BACK"}
        if supplied_hash != expected_hash or journal.get("phase") not in allowed_phases:
            raise StoreError(
                "import_recovery_required", "import phase journal hash is invalid")
        for record in journal["operations"]:
            if (not isinstance(record, dict)
                    or set(record) != {
                        "target", "staged", "before", "had_before"}
                    or not isinstance(record["target"], str)
                    or type(record["had_before"]) is not bool
                    or record["staged"] is not None
                    and (not isinstance(record["staged"], str)
                         or Path(record["staged"]).name != record["staged"])
                    or record["before"] is not None
                    and (not isinstance(record["before"], str)
                         or Path(record["before"]).name != record["before"])
                    or record["had_before"] != (record["before"] is not None)):
                raise StoreError(
                    "import_recovery_required",
                    "import phase journal operations are invalid")
        return cls(database.resolve(strict=False), root, journal)

    def _write_journal(self) -> None:
        payload = {
            key: value for key, value in self.journal.items()
            if key != "journal_hash"}
        self.journal["journal_hash"] = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        encoded = json.dumps(
            self.journal, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")
        _write_file(self.root / "journal.json", encoded)

    def mark(self, phase: str) -> None:
        if phase not in {"PREPARED", "DB_APPLIED", "FILES_PUBLISHED",
                         "COMPLETED", "ROLLED_BACK"}:
            raise ValueError("invalid import transaction phase")
        self.journal["phase"] = phase
        self._write_journal()

    def publish_files(self) -> None:
        for record in self.journal["operations"]:
            target = _ensure_safe_target(
                self.database, self.database.parent / record["target"])
            if record["staged"] is None:
                try:
                    target.unlink()
                    _fsync_directory(target.parent)
                except FileNotFoundError:
                    pass
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _ensure_safe_target(self.database, target)
            _write_file(target, (self.root / record["staged"]).read_bytes())
        self.mark("FILES_PUBLISHED")

    def rollback(self) -> None:
        try:
            backup = self.root / "database-before.sqlite"
            if not backup.is_file() or backup.stat().st_size == 0:
                raise StoreError(
                    "import_recovery_required",
                    "import database rollback image is missing")
            source = sqlite3.connect(str(backup))
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                source.close()
                raise StoreError(
                    "import_recovery_required",
                    "import database rollback image is corrupt")
            target = sqlite3.connect(str(self.database))
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()
                source.close()
            for suffix in ("-wal", "-shm"):
                try:
                    Path(f"{self.database}{suffix}").unlink()
                except FileNotFoundError:
                    pass
            for record in reversed(self.journal["operations"]):
                target_path = _ensure_safe_target(
                    self.database, self.database.parent / record["target"])
                if record["had_before"]:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    _write_file(
                        target_path, (self.root / record["before"]).read_bytes())
                else:
                    try:
                        target_path.unlink()
                        _fsync_directory(target_path.parent)
                    except FileNotFoundError:
                        pass
            self.mark("ROLLED_BACK")
        except (OSError, sqlite3.Error, StoreError) as exc:
            raise StoreError(
                "import_recovery_required",
                f"automatic import rollback failed; preserve {self.root.name}",
                500,
            ) from exc

    def finish(self) -> None:
        self.mark("COMPLETED")
        shutil.rmtree(self.root, ignore_errors=True)
        _fsync_directory(self.root.parent)


def validate_file_operations(database: Path,
                             operations: list[FileOperation]) -> None:
    """Run the exact path policy used by prepare, without writing staging."""
    database = database.resolve(strict=False)
    for operation in operations:
        _ensure_safe_target(database, operation.target)


def recover_import_transactions(database: Path) -> list[str]:
    """Roll back every unfinished transaction before new maintenance work."""
    database = database.resolve(strict=False)
    recovered = []
    pattern = f".{database.name}.import-*"
    for root in sorted(database.parent.glob(pattern)):
        if not root.is_dir() or root.is_symlink():
            continue
        if not (root / "journal.json").is_file():
            # The phase journal is the final prepare step. Without it no
            # caller was allowed to start the database transaction.
            shutil.rmtree(root, ignore_errors=True)
            continue
        transaction = ImportTransaction.open(database, root)
        phase = transaction.journal.get("phase")
        if phase in {"COMPLETED", "ROLLED_BACK"}:
            shutil.rmtree(root, ignore_errors=True)
            continue
        transaction.rollback()
        recovered.append(root.name)
        shutil.rmtree(root, ignore_errors=True)
    if recovered:
        _fsync_directory(database.parent)
    return recovered
