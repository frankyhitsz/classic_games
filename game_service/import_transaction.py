"""Crash-recoverable filesystem side of a local data import."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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


def _file_digest(path: Path) -> tuple[int, str]:
    try:
        metadata = os.lstat(path)
        if (not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink > 1):
            raise OSError("not a regular transaction file")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        digest = hashlib.sha256()
        size = 0
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink > 1
                    or (metadata.st_dev, metadata.st_ino)
                    != (opened.st_dev, opened.st_ino)):
                raise OSError("transaction file changed while opening")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                while chunk := handle.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise StoreError(
            "import_recovery_required",
            f"import transaction file is missing or unsafe: {path.name}",
        ) from exc
    return size, digest.hexdigest()


def _verify_file(path: Path, size: int, digest: str) -> None:
    actual_size, actual_digest = _file_digest(path)
    if actual_size != size or actual_digest != digest:
        raise StoreError(
            "import_recovery_required",
            f"import transaction content hash mismatch: {path.name}",
        )


def _allowed_relative_target(relative: Path) -> bool:
    parts = relative.parts
    if (len(parts) == 2 and parts[0] == "pending"
            and parts[1].endswith(".json")):
        return len(parts[1]) <= 160
    if len(parts) == 2 and parts[0] == "pending-state":
        name = parts[1]
        return (name.endswith(".json") and len(name) <= 160
                or name.startswith(".reject-")
                and name.endswith((".txn", ".tmp"))
                or name.startswith(".") and name.endswith(".restore"))
    if parts == ("pending_saves.json",):
        return True
    return (len(parts) >= 3 and parts[0] == "imported-recovery"
            and len(parts[1]) == 24
            and all(char in "0123456789abcdef" for char in parts[1])
            and all(part not in {"", ".", ".."} for part in parts))


def _write_allowed_relative(relative: Path) -> bool:
    parts = relative.parts
    if len(parts) == 2 and parts[0] == "pending":
        stem = parts[1][:-5] if parts[1].endswith(".json") else ""
        return (1 <= len(stem) <= 128 and all(
            char.isascii() and (char.isalnum() or char in "-_")
            for char in stem))
    if len(parts) == 2 and parts[0] == "pending-state":
        stem = parts[1][:-5] if parts[1].endswith(".json") else ""
        return (len(stem) == 64 and all(
            char in "0123456789abcdef" for char in stem))
    return len(parts) >= 3 and parts[0] == "imported-recovery"


def _deletion_only_relative(relative: Path) -> bool:
    parts = relative.parts
    return (parts == ("pending_saves.json",)
            or (len(parts) == 2 and parts[0] == "pending-state"
                and ((parts[1].startswith(".reject-")
                      and parts[1].endswith((".txn", ".tmp")))
                     or (parts[1].startswith(".")
                         and parts[1].endswith(".restore")))))


def _ensure_safe_target(database: Path, target: Path) -> Path:
    database = database.expanduser().resolve(strict=False)
    parent = database.parent
    expanded = target.expanduser()
    candidate = Path(os.path.abspath(expanded))
    try:
        relative = candidate.relative_to(parent)
    except ValueError as exc:
        raise StoreError(
            "unsafe_import_target", "import file target escapes the data directory"
        ) from exc
    if candidate == database:
        raise StoreError(
            "unsafe_import_target", "database cannot be a staged file target")
    if not _allowed_relative_target(relative):
        raise StoreError(
            "unsafe_import_target", "import target is outside active data namespaces")
    cursor = parent
    for part in relative.parts:
        cursor /= part
        try:
            metadata = os.lstat(cursor)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise StoreError(
                "unsafe_import_target", "import target metadata is unreadable"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise StoreError(
                "unsafe_import_target", "import target contains a symbolic link")
        if cursor != candidate and not stat.S_ISDIR(metadata.st_mode):
            raise StoreError(
                "unsafe_import_target", "import target parent is not a directory")
        if cursor == candidate and not stat.S_ISREG(metadata.st_mode):
            raise StoreError(
                "unsafe_import_target", "import target is not an ordinary file")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(parent)
    except ValueError as exc:
        raise StoreError(
            "unsafe_import_target", "import target resolves outside the data directory"
        ) from exc
    return candidate


def _normalized_operations(database: Path,
                           operations: list[FileOperation]) -> list[FileOperation]:
    normalized: list[FileOperation] = []
    seen: dict[Path, bytes | None] = {}
    for operation in operations:
        target = _ensure_safe_target(database, operation.target)
        relative = target.relative_to(database.parent)
        if _deletion_only_relative(relative) and operation.data is not None:
            raise StoreError(
                "unsafe_import_target",
                "import control artifacts may only be removed")
        if (operation.data is not None
                and not _write_allowed_relative(relative)):
            raise StoreError(
                "unsafe_import_target",
                "import target is not a canonical writable data path")
        if target in seen:
            if seen[target] != operation.data:
                raise StoreError(
                    "duplicate_import_target",
                    "the import plan assigns conflicting data to one target",
                    409,
                )
            continue
        seen[target] = operation.data
        normalized.append(FileOperation(target, operation.data))
    return normalized


def _translate_parent_alias(database: Path,
                            operations: list[FileOperation]
                            ) -> tuple[Path, list[FileOperation]]:
    """Map a platform ancestor alias while preserving in-directory components."""
    lexical_database = Path(os.path.abspath(database.expanduser()))
    resolved_database = lexical_database.resolve(strict=False)
    translated = []
    for operation in operations:
        lexical_target = Path(os.path.abspath(operation.target.expanduser()))
        try:
            relative = lexical_target.relative_to(lexical_database.parent)
        except ValueError:
            target = lexical_target
        else:
            target = resolved_database.parent / relative
        translated.append(FileOperation(target, operation.data))
    return resolved_database, translated


class ImportTransaction:
    """Prepared files plus a phase journal and database rollback image."""

    def __init__(self, database: Path, root: Path, journal: dict):
        self.database = database
        self.root = root
        self.journal = journal

    @classmethod
    def prepare(cls, database: Path, operations: list[FileOperation]):
        database, operations = _translate_parent_alias(database, operations)
        operations = _normalized_operations(database, operations)
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
            # Windows rejects fsync on a read-only CRT descriptor (EBADF).
            # Open update-capable without changing the rollback image.
            with (root / "database-before.sqlite").open("rb+") as handle:
                os.fsync(handle.fileno())
            rollback_size, rollback_hash = _file_digest(
                root / "database-before.sqlite")
            for index, operation in enumerate(operations):
                target = _ensure_safe_target(database, operation.target)
                relative = target.relative_to(database.parent.resolve(strict=False))
                staged_name = None
                staged_size = None
                staged_hash = None
                if operation.data is not None:
                    transaction_bytes += len(operation.data)
                    if (len(operation.data) > MAX_TRANSACTION_FILE_BYTES
                            or transaction_bytes > MAX_TRANSACTION_TOTAL_BYTES):
                        raise StoreError(
                            "import_staging_too_large",
                            "staged import files exceed rollback limits")
                    staged_name = f"staged-{index}.bin"
                    _write_file(root / staged_name, operation.data)
                    staged_size = len(operation.data)
                    staged_hash = hashlib.sha256(operation.data).hexdigest()
                before_name = None
                before_size = None
                before_hash = None
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
                    before_data = target.read_bytes()
                    _write_file(root / before_name, before_data)
                    before_size = len(before_data)
                    before_hash = hashlib.sha256(before_data).hexdigest()
                records.append({
                    "target": relative.as_posix(),
                    "staged": staged_name,
                    "staged_size": staged_size,
                    "staged_sha256": staged_hash,
                    "before": before_name,
                    "before_size": before_size,
                    "before_sha256": before_hash,
                    "had_before": had_before,
                })
            journal = {
                "version": 2, "phase": "PREPARED",
                "database": database.name, "operations": records,
                "database_before": {
                    "path": "database-before.sqlite",
                    "size": rollback_size,
                    "sha256": rollback_hash,
                },
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
            journal_path = root / "journal.json"
            metadata = os.lstat(journal_path)
            if (not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink > 1
                    or metadata.st_size > MAX_TRANSACTION_FILE_BYTES):
                raise OSError("unsafe import journal")
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(journal_path, flags)
            try:
                opened = os.fstat(descriptor)
                if ((metadata.st_dev, metadata.st_ino)
                        != (opened.st_dev, opened.st_ino)):
                    raise OSError("import journal changed while opening")
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    raw = handle.read(MAX_TRANSACTION_FILE_BYTES + 1)
                if len(raw) > MAX_TRANSACTION_FILE_BYTES:
                    raise OSError("import journal grew while reading")
            finally:
                os.close(descriptor)
            journal = json.loads(raw.decode("utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StoreError(
                "import_recovery_required", "import phase journal is unreadable"
            ) from exc
        version = journal.get("version") if isinstance(journal, dict) else None
        if (not isinstance(journal, dict) or version not in {1, 2}
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
        targets = set()
        for record in journal["operations"]:
            expected_fields = ({"target", "staged", "before", "had_before"}
                               if version == 1 else {
                                   "target", "staged", "staged_size",
                                   "staged_sha256", "before", "before_size",
                                   "before_sha256", "had_before"})
            if (not isinstance(record, dict)
                    or set(record) != expected_fields
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
            target = _ensure_safe_target(
                database, database.parent / Path(*PurePosixPath(
                    record["target"]).parts))
            relative = target.relative_to(database.resolve(strict=False).parent)
            if (_deletion_only_relative(relative)
                    and record["staged"] is not None):
                raise StoreError(
                    "import_recovery_required",
                    "import journal writes an active control artifact")
            if (record["staged"] is not None
                    and not _write_allowed_relative(relative)):
                raise StoreError(
                    "import_recovery_required",
                    "import journal writes a non-canonical data path")
            if target in targets:
                raise StoreError(
                    "import_recovery_required",
                    "import phase journal contains duplicate targets")
            targets.add(target)
            if version == 2:
                for prefix in ("staged", "before"):
                    name = record[prefix]
                    size = record[f"{prefix}_size"]
                    digest = record[f"{prefix}_sha256"]
                    if name is None:
                        if size is not None or digest is not None:
                            raise StoreError(
                                "import_recovery_required",
                                "import phase journal file metadata is invalid")
                    elif (type(size) is not int or size < 0
                          or not isinstance(digest, str) or len(digest) != 64):
                        raise StoreError(
                            "import_recovery_required",
                            "import phase journal file metadata is invalid")
            # Verify all phase inputs before an automatic recovery changes
            # the database or any published file.
        if version == 2:
            database_before = journal.get("database_before")
            if (not isinstance(database_before, dict)
                    or set(database_before) != {"path", "size", "sha256"}
                    or database_before.get("path") != "database-before.sqlite"
                    or type(database_before.get("size")) is not int
                    or database_before["size"] <= 0
                    or not isinstance(database_before.get("sha256"), str)
                    or len(database_before["sha256"]) != 64):
                raise StoreError(
                    "import_recovery_required",
                    "import database rollback metadata is invalid")
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
        if self.journal["version"] == 2:
            for record in self.journal["operations"]:
                if record["staged"] is not None:
                    _verify_file(
                        self.root / record["staged"],
                        record["staged_size"], record["staged_sha256"])
                target = _ensure_safe_target(
                    self.database, self.database.parent / record["target"])
                if record["had_before"]:
                    try:
                        size, digest = _file_digest(target)
                    except StoreError as exc:
                        raise StoreError(
                            "import_target_changed",
                            "an import target disappeared after preparation",
                            409,
                        ) from exc
                    if (size != record["before_size"]
                            or digest != record["before_sha256"]):
                        raise StoreError(
                            "import_target_changed",
                            "an import target changed after preparation",
                            409,
                        )
                elif target.exists() or target.is_symlink():
                    raise StoreError(
                        "import_target_changed",
                        "an import target appeared after preparation",
                        409,
                    )
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
            if self.journal["version"] == 2:
                database_before = self.journal["database_before"]
                _verify_file(
                    backup, database_before["size"],
                    database_before["sha256"])
                for record in self.journal["operations"]:
                    if record["before"] is not None:
                        _verify_file(
                            self.root / record["before"],
                            record["before_size"], record["before_sha256"])
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
    database, operations = _translate_parent_alias(database, operations)
    _normalized_operations(database, operations)


def recover_import_transactions(database: Path) -> list[str]:
    """Roll back every unfinished transaction before new maintenance work."""
    database = database.resolve(strict=False)
    recovered = []
    pattern = f".{database.name}.import-*"
    for root in sorted(database.parent.glob(pattern)):
        if root.is_symlink():
            raise StoreError(
                "import_recovery_required",
                f"import transaction root is a symbolic link: {root.name}")
        if not root.is_dir():
            continue
        journal_path = root / "journal.json"
        try:
            os.lstat(journal_path)
        except FileNotFoundError:
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
