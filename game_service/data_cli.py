"""Inspect, export, preview, and restore local Classic Games Hub data."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath

from .catalog import GAME_BY_ID, VALID_GAME_IDS
from .import_transaction import (FileOperation, ImportTransaction,
                                 MAX_TRANSACTION_FILE_BYTES,
                                 MAX_TRANSACTION_TOTAL_BYTES,
                                 recover_import_transactions,
                                 validate_file_operations)
from .local_backend import (MAX_SPOOL_FILE_BYTES, PendingSaveEnvelope,
                            PersistentSaveOutbox, PersistentStateOutbox)
from .maintenance import (MaintenanceBusyError, inactive_application_lock,
                          maintenance_lock, application_lock_path, lock_path)
from .mutation import MutationError, canonical_json
from .profile import ProfileIdentity, ProfileIdentityError
from .progress import ProgressPolicyError, validate_progress
from .save_slot_validation import validate_save_slot_payload
from .store import (SCHEMA_VERSION as STORE_SCHEMA_VERSION, LocalGameStore,
                    StoreError, default_database_path)
from .version import __version__ as PACKAGE_VERSION

ARCHIVE_VERSION = 3
MANIFEST_FORMAT_VERSION = 3
SUPPORTED_ARCHIVE_VERSIONS = frozenset({1, 2, ARCHIVE_VERSION})
STRICT_EVIDENCE_MANIFESTS = frozenset({2, MANIFEST_FORMAT_VERSION})
ARCHIVE_CAPABILITIES = (
    "complete-active-journal-inventory",
    "strict-recovery-evidence",
    "historical-ruleset-rows",
)
EXPORT_TABLES = (
    "profiles", "attempts", "settings", "progress", "save_slots",
    "invalid_attempts", "invalid_local_state",
)
IMPORT_TABLES = EXPORT_TABLES
NATURAL_KEYS = {
    "profiles": ("profile_id",),
    "attempts": ("attempt_uuid",),
    "settings": ("profile_id", "key"),
    "progress": ("profile_id", "game_id", "ruleset_version", "key"),
    "save_slots": ("profile_id", "game_id", "slot_id"),
}
AUTO_ID_TABLES = frozenset({
    "attempts", "invalid_attempts", "invalid_local_state"})
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_TABLE_BYTES = 64 * 1024 * 1024
MAX_TABLE_ROWS = 1_000_000
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 250_000
# Recovery evidence is base64 encoded, so an 8 MiB file needs roughly
# 10.7 MiB of JSON text. The limit remains finite while accepting our own
# maximum-size export.
MAX_JSON_STRING = 12 * 1024 * 1024
MAX_RECOVERY_FILE_BYTES = 8 * 1024 * 1024
MAX_RECOVERY_TOTAL_BYTES = 64 * 1024 * 1024
MAX_RECOVERY_FILES = 2_000


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant: {value}")


def _validate_json_shape(value) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise StoreError(
                "archive_too_complex", "archive nesting or item count is too large")
        if isinstance(item, str) and len(item) > MAX_JSON_STRING:
            raise StoreError(
                "archive_string_too_large", "archive contains an oversized string")
        if isinstance(item, float) and not math.isfinite(item):
            raise StoreError(
                "invalid_archive", "archive contains a non-finite number")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _existing_store(database: Path) -> LocalGameStore:
    if not database.is_file():
        raise StoreError(
            "database_not_found", "local database does not exist", 404)
    return LocalGameStore(database, initialize=False)


def _bounded_rows(connection: sqlite3.Connection, table: str) -> list[dict]:
    rows: list[dict] = []
    encoded_bytes = 0
    for index, row in enumerate(connection.execute(f"SELECT * FROM {table}")):
        if index >= MAX_TABLE_ROWS:
            raise StoreError(
                "export_too_large", f"{table} exceeds the export row limit")
        value = dict(row)
        encoded_bytes += len(canonical_json(value).encode("utf-8"))
        if encoded_bytes > MAX_TABLE_BYTES:
            raise StoreError(
                "export_too_large", f"{table} exceeds the export memory limit")
        rows.append(value)
    return rows


def _recovery_paths(database: Path) -> list[Path]:
    parent = database.parent
    prefixes = (
        f"{database.name}.backup-", f"{database.name}.corrupt-",
        "pending-quarantine", "pending-state-quarantine",
        "pending-migration-backup", "pending-state-migration-backup",
        "pending_saves.json.migrated-",
        "imported-recovery", f".{database.name}.import-",
    )
    try:
        entries = list(parent.iterdir())
    except OSError:
        return []
    return sorted((entry for entry in entries
                   if any(entry.name.startswith(prefix) for prefix in prefixes)),
                  key=lambda path: path.name)


def _recovery_source(database: Path, name: str) -> str:
    mappings = (
        (f".{database.name}.import-", "import_transaction"),
        (f"{database.name}.backup-", "database_backup"),
        (f"{database.name}.corrupt-", "corrupt_database"),
        ("pending-quarantine", "score_quarantine"),
        ("pending-state-quarantine", "state_quarantine"),
        ("pending-migration-backup", "score_migration"),
        ("pending-state-migration-backup", "state_migration"),
        ("pending_saves.json.migrated-", "legacy_score_migration"),
        ("imported-recovery", "imported_evidence"),
    )
    return next((source for prefix, source in mappings
                 if name.startswith(prefix)), "unknown")


def _canonical_path(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _guard_export_target(database: Path, requested: Path, *, force: bool) -> Path:
    if requested.expanduser().is_symlink():
        raise StoreError(
            "unsafe_export_target", "export target cannot be a symbolic link")
    output = requested.expanduser().resolve(strict=False)
    database = database.expanduser().resolve(strict=False)
    protected_files = {
        _canonical_path(database),
        *(_canonical_path(Path(f"{database}{suffix}"))
          for suffix in ("-wal", "-shm", "-journal")),
        _canonical_path(lock_path(database)),
        _canonical_path(application_lock_path(database)),
        _canonical_path(database.with_name("pending_saves.json")),
    }
    protected_directories = [
        database.with_name("pending"),
        database.with_name("pending-state"),
        *_recovery_paths(database),
    ]
    if output.parent == database.parent and output.name.startswith(
            f".{database.name}.import-"):
        raise StoreError(
            "unsafe_export_target", "export target overlaps an import transaction")
    if (_canonical_path(output) in protected_files
            or any(_path_within(output, path)
                   for path in protected_directories)):
        raise StoreError(
            "unsafe_export_target",
            "export target overlaps the database or recovery journals")
    if output.exists():
        if output.is_dir():
            raise StoreError(
                "export_target_is_directory", "export target is a directory")
        if not force:
            raise StoreError(
                "export_target_exists",
                "export target already exists; pass --force to replace it", 409)
    return output


def _active_protocol_report(database: Path) -> dict:
    """List unresolved active journals that a complete snapshot cannot omit."""
    _score_path, state_path = _pending_paths(database)
    candidates = [database.with_name("pending_saves.json")]
    try:
        with os.scandir(state_path) as entries:
            candidates.extend(
                Path(entry.path) for entry in entries
                if (entry.name.startswith(".reject-")
                    and entry.name.endswith((".txn", ".tmp")))
                or (entry.name.startswith(".")
                    and entry.name.endswith(".restore")))
    except (FileNotFoundError, NotADirectoryError, OSError):
        pass
    unresolved = []
    for path in candidates:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            unresolved.append({"path": path.name, "reason": "unreadable"})
            continue
        unresolved.append({
            "path": (path.name if path.parent == database.parent else
                     f"pending-state/{path.name}"),
            "reason": ("unsafe_type" if not stat.S_ISREG(metadata.st_mode)
                       else "unfinished_transaction"),
            "size": metadata.st_size,
        })
    return {"complete": not unresolved, "unresolved": unresolved,
            "unresolved_count": len(unresolved)}


def _pending_paths(database: Path) -> tuple[Path, Path]:
    return database.with_name("pending"), database.with_name("pending-state")


def _pending_file_count(path: Path) -> int:
    count = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if (entry.is_file(follow_symlinks=False)
                        and entry.name.endswith(".json")):
                    count += 1
                    if count >= MAX_TABLE_ROWS:
                        break
    except (FileNotFoundError, NotADirectoryError, OSError):
        return 0
    return count


def _read_regular_nofollow(path: Path, limit: int) -> bytes:
    """Read a regular, single-link file without a symlink swap window."""
    try:
        before = os.lstat(path)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink > 1
                or before.st_size > limit):
            raise OSError("unsafe or oversized file")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            after = os.fstat(descriptor)
            if (not stat.S_ISREG(after.st_mode) or after.st_nlink > 1
                    or after.st_size > limit
                    or (before.st_dev, before.st_ino)
                    != (after.st_dev, after.st_ino)):
                raise OSError("file changed while opening")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(limit + 1)
            if len(raw) > limit:
                raise OSError("file grew while reading")
            return raw
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise StoreError(
            "unsafe_snapshot_file", "snapshot file is unsafe or unreadable"
        ) from exc


def inspect_data(database: Path) -> dict:
    if not database.is_file():
        raise StoreError(
            "database_not_found", "local database does not exist", 404)
    connection = None
    try:
        connection = sqlite3.connect(str(database), timeout=2.0)
        connection.row_factory = sqlite3.Row
        with connection:
            tables = {
                row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            schema_version = 0
            if "schema_meta" in tables:
                row = connection.execute(
                    "SELECT value FROM schema_meta WHERE key='version'").fetchone()
                if row is not None:
                    schema_version = int(row["value"])
            counts = {
                table: (int(connection.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    if table in tables else None)
                for table in EXPORT_TABLES
            }
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise StoreError(
            "database_unavailable", "local database status is unreadable") from exc
    finally:
        if connection is not None:
            connection.close()
    score_path, state_path = _pending_paths(database)
    recovery = []
    for path in _recovery_paths(database):
        modified_at = None
        try:
            metadata = os.lstat(path)
            modified_at = metadata.st_mtime
            if stat.S_ISLNK(metadata.st_mode):
                kind, size = "symlink", None
            elif stat.S_ISREG(metadata.st_mode):
                kind, size = "file", metadata.st_size
            elif stat.S_ISDIR(metadata.st_mode):
                kind, size = "directory", 0
                visited = 0
                for current, directories, files in os.walk(
                        path, topdown=True, followlinks=False):
                    directories[:] = [name for name in directories
                                      if not (Path(current) / name).is_symlink()]
                    for name in files:
                        visited += 1
                        if visited > MAX_RECOVERY_FILES:
                            size = None
                            break
                        child = Path(current) / name
                        child_metadata = os.lstat(child)
                        if (stat.S_ISREG(child_metadata.st_mode)
                                and child_metadata.st_nlink <= 1):
                            size += child_metadata.st_size
                    if size is None:
                        break
            else:
                kind, size = "special", None
        except OSError:
            kind, size = "unreadable", None
        recovery.append({
            "path": path.name,
            "source": _recovery_source(database, path.name),
            "kind": kind,
            "size": size,
            "modified_at": modified_at,
        })
    return {
        "ok": integrity == "ok", "database": str(database),
        "schema_version": schema_version,
        "supported_schema_version": STORE_SCHEMA_VERSION,
        "migration_needed": schema_version != STORE_SCHEMA_VERSION,
        "missing_tables": [
            table for table, count in counts.items() if count is None],
        "quick_check": integrity,
        "counts": counts,
        "pending": {
            "scores": _pending_file_count(score_path),
            "state": _pending_file_count(state_path),
        },
        "recovery": recovery,
        "unfinished_import_transactions": len([
            path for path in database.parent.glob(
                f".{database.name}.import-*")
            if path.is_dir() and not path.is_symlink()
        ]),
    }


def inspect_transactions(database: Path) -> dict:
    """Describe import journals without applying or deleting them."""
    database = database.expanduser().resolve(strict=False)
    transactions = []
    for root in sorted(database.parent.glob(f".{database.name}.import-*")):
        record = {"path": root.name, "phase": None, "valid": False,
                  "error": None}
        try:
            if root.is_symlink() or not root.is_dir():
                raise StoreError(
                    "import_recovery_required", "transaction root is unsafe")
            transaction = ImportTransaction.open(database, root)
            record.update({
                "phase": transaction.journal["phase"], "valid": True,
                "version": transaction.journal["version"],
                "operation_count": len(transaction.journal["operations"]),
                "modified_at": os.lstat(root / "journal.json").st_mtime,
            })
        except (OSError, StoreError) as exc:
            record["error"] = (
                exc.message if isinstance(exc, StoreError) else str(exc))
        transactions.append(record)
    return {"ok": True, "database": str(database),
            "transactions": transactions, "count": len(transactions),
            "requires_recovery": any(not item["valid"] or item["phase"] not in {
                "COMPLETED", "ROLLED_BACK"} for item in transactions)}


def recover_transactions_data(database: Path, *,
                              allow_legacy_v1: bool = False) -> dict:
    database = database.expanduser().resolve(strict=False)
    try:
        with (inactive_application_lock(database, timeout=2.0),
              maintenance_lock(database, exclusive=True, timeout=2.0)):
            recovered = recover_import_transactions(
                database, allow_legacy_v1=allow_legacy_v1)
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc
    return {"ok": True, "database": str(database), "recovered": recovered,
            "recovered_count": len(recovered)}


def export_transaction_data(database: Path, transaction_name: str,
                            output: Path, *, force: bool = False) -> dict:
    """Export one transaction directory as bounded, no-follow evidence."""
    database = database.expanduser().resolve(strict=False)
    if (not isinstance(transaction_name, str)
            or Path(transaction_name).name != transaction_name
            or not transaction_name.startswith(f".{database.name}.import-")):
        raise StoreError(
            "invalid_transaction_name", "transaction name is not valid")
    root = database.parent / transaction_name
    output = _guard_export_target(database, output, force=force)
    try:
        with (inactive_application_lock(database, timeout=2.0),
              maintenance_lock(database, exclusive=True, timeout=2.0)):
            root_metadata = os.lstat(root)
            if (not stat.S_ISDIR(root_metadata.st_mode)
                    or stat.S_ISLNK(root_metadata.st_mode)):
                raise StoreError(
                    "unsafe_transaction_root", "transaction root is unsafe")
            files = []
            total = 0
            with os.scandir(root) as entries:
                candidates = sorted(entries, key=lambda entry: entry.name)
            if len(candidates) > MAX_RECOVERY_FILES:
                raise StoreError(
                    "transaction_too_large", "transaction has too many files")
            for entry in candidates:
                metadata = entry.stat(follow_symlinks=False)
                if (not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink > 1):
                    files.append({"path": entry.name,
                                  "omitted": "unsafe_file_type"})
                    continue
                total += metadata.st_size
                if (metadata.st_size > MAX_TRANSACTION_FILE_BYTES
                        or total > MAX_TRANSACTION_TOTAL_BYTES):
                    files.append({"path": entry.name, "size": metadata.st_size,
                                  "omitted": "size_limit"})
                    continue
                raw = _read_regular_nofollow(
                    Path(entry.path), MAX_TRANSACTION_FILE_BYTES)
                files.append({
                    "path": entry.name, "size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                })
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc
    payload = {
        "format": "classic-games-import-transaction-evidence-v1",
        "database": database.name, "transaction": transaction_name,
        "exported_at": time.time(), "files": files,
        "complete": not any("omitted" in item for item in files),
    }
    encoded = canonical_json(payload).encode("utf-8")
    if len(encoded) > MAX_ARCHIVE_BYTES:
        raise StoreError(
            "transaction_too_large", "transaction evidence is too large")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(
        f".{output.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _guard_export_target(database, output, force=force)
        if force:
            os.replace(temporary, output)
        else:
            try:
                os.link(temporary, output)
            except FileExistsError as exc:
                raise StoreError(
                    "export_target_exists", "transaction export target appeared",
                    409,
                ) from exc
            temporary.unlink()
        _fsync_directory(output.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {"ok": True, "output": str(output),
            "transaction": transaction_name, "files": len(files),
            "bytes": len(encoded), "complete": payload["complete"]}


def cleanup_recovery_data(database: Path, *, older_than_days: int,
                          archive_path: Path | None = None,
                          apply: bool = False) -> dict:
    """Plan or remove recovery roots only when a complete archive proves them."""
    if type(older_than_days) is not int or older_than_days < 0:
        raise StoreError(
            "invalid_retention", "older-than-days must be a non-negative integer")
    database = database.expanduser().resolve(strict=False)
    archive = None
    evidence_hashes: dict[str, str] = {}
    if archive_path is not None:
        archive = _load_archive(archive_path)
        manifest = archive.get("manifest", {})
        if (manifest.get("format_version") != MANIFEST_FORMAT_VERSION
                or manifest.get("complete") is not True
                or manifest.get("recovery", {}).get("complete") is not True):
            raise StoreError(
                "incomplete_archive",
                "cleanup proof must be a complete current-format archive")
        evidence_hashes = {
            PurePosixPath(*relative.parts).as_posix():
                hashlib.sha256(raw).hexdigest()
            for relative, raw in _validated_recovery_items(archive)
        }
    if apply and archive is None:
        raise StoreError(
            "cleanup_archive_required",
            "cleanup --apply requires a complete --archive proof")
    cutoff = time.time() - older_than_days * 86400
    try:
        with (inactive_application_lock(database, timeout=2.0),
              maintenance_lock(database, exclusive=True, timeout=2.0)):
            candidates = []
            for root in _recovery_paths(database):
                item = {
                    "path": root.name,
                    "source": _recovery_source(database, root.name),
                    "eligible": False, "reason": None,
                }
                try:
                    metadata = os.lstat(root)
                except OSError:
                    item["reason"] = "unreadable"
                    candidates.append(item)
                    continue
                item["modified_at"] = metadata.st_mtime
                if root.name.startswith(f".{database.name}.import-"):
                    item["reason"] = "use_recover_transactions"
                    candidates.append(item)
                    continue
                if metadata.st_mtime > cutoff:
                    item["reason"] = "newer_than_cutoff"
                    candidates.append(item)
                    continue
                paths = []
                if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink <= 1:
                    paths.append((root, root.name))
                elif stat.S_ISDIR(metadata.st_mode):
                    unsafe = False
                    for current, directories, files in os.walk(
                            root, topdown=True, followlinks=False):
                        safe_directories = []
                        for name in directories:
                            child = Path(current) / name
                            child_metadata = os.lstat(child)
                            if stat.S_ISDIR(child_metadata.st_mode):
                                safe_directories.append(name)
                            else:
                                unsafe = True
                        directories[:] = safe_directories
                        for name in files:
                            child = Path(current) / name
                            child_metadata = os.lstat(child)
                            if (not stat.S_ISREG(child_metadata.st_mode)
                                    or child_metadata.st_nlink > 1):
                                unsafe = True
                                continue
                            relative = (PurePosixPath(root.name)
                                        / PurePosixPath(
                                            *child.relative_to(root).parts))
                            paths.append((child, relative.as_posix()))
                    if unsafe:
                        item["reason"] = "unsafe_file_type"
                        candidates.append(item)
                        continue
                else:
                    item["reason"] = "unsafe_file_type"
                    candidates.append(item)
                    continue
                if len(paths) > MAX_RECOVERY_FILES:
                    item["reason"] = "file_count_limit"
                    candidates.append(item)
                    continue
                fingerprints = {}
                fingerprint_bytes = 0
                try:
                    for path, relative in paths:
                        raw = _read_regular_nofollow(
                            path, MAX_RECOVERY_FILE_BYTES)
                        fingerprint_bytes += len(raw)
                        if fingerprint_bytes > MAX_RECOVERY_TOTAL_BYTES:
                            raise StoreError(
                                "recovery_too_large",
                                "recovery cleanup proof exceeds size limit")
                        fingerprints[relative] = hashlib.sha256(raw).hexdigest()
                except StoreError:
                    item["reason"] = "unreadable_or_oversized"
                    candidates.append(item)
                    continue
                item["files"] = len(fingerprints)
                item["bytes"] = fingerprint_bytes
                item["fingerprints"] = fingerprints
                if archive is None:
                    item["reason"] = "archive_proof_not_supplied"
                elif any(evidence_hashes.get(path) != digest
                         for path, digest in fingerprints.items()):
                    item["reason"] = "not_preserved_by_archive"
                else:
                    item["eligible"] = True
                candidates.append(item)
            removed = []
            if apply:
                for item in candidates:
                    if not item["eligible"]:
                        continue
                    target = database.parent / item["path"]
                    metadata = os.lstat(target)
                    current_files: dict[str, Path] = {}
                    directories: list[Path] = []
                    if stat.S_ISDIR(metadata.st_mode):
                        for current, child_directories, files in os.walk(
                                target, topdown=True, followlinks=False):
                            current_path = Path(current)
                            directories.append(current_path)
                            safe_children = []
                            for name in child_directories:
                                child = current_path / name
                                child_metadata = os.lstat(child)
                                if (not stat.S_ISDIR(child_metadata.st_mode)
                                        or stat.S_ISLNK(child_metadata.st_mode)):
                                    raise StoreError(
                                        "cleanup_target_changed",
                                        "cleanup directory gained an unsafe entry",
                                        409)
                                safe_children.append(name)
                            child_directories[:] = safe_children
                            for name in files:
                                evidence_path = current_path / name
                                relative = (PurePosixPath(item["path"])
                                            / PurePosixPath(
                                                *evidence_path.relative_to(
                                                    target).parts)).as_posix()
                                current_files[relative] = evidence_path
                    elif stat.S_ISREG(metadata.st_mode):
                        current_files[item["path"]] = target
                    else:
                        raise StoreError(
                            "cleanup_target_changed",
                            "cleanup candidate changed type", 409)
                    if set(current_files) != set(item["fingerprints"]):
                        raise StoreError(
                            "cleanup_target_changed",
                            "cleanup candidate contents changed after verification",
                            409)
                    for relative, expected_hash in item["fingerprints"].items():
                        evidence_path = current_files[relative]
                        raw = _read_regular_nofollow(
                            evidence_path, MAX_RECOVERY_FILE_BYTES)
                        if hashlib.sha256(raw).hexdigest() != expected_hash:
                            raise StoreError(
                                "cleanup_target_changed",
                                "cleanup candidate changed after verification", 409)
                    if stat.S_ISDIR(metadata.st_mode):
                        for evidence_path in current_files.values():
                            evidence_path.unlink()
                        for directory in sorted(
                                directories, key=lambda path: len(path.parts),
                                reverse=True):
                            directory.rmdir()
                    else:
                        target.unlink()
                    removed.append(item["path"])
                if removed:
                    _fsync_directory(database.parent)
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc
    return {
        "ok": True, "database": str(database), "applied": apply,
        "older_than_days": older_than_days,
        "archive": archive.get("manifest_hash") if archive else None,
        "candidates": candidates, "removed": removed,
        "removed_count": len(removed),
    }


def _export_recovery(database: Path) -> tuple[list[dict], dict]:
    result: list[dict] = []
    total = 0
    visited = 0
    for root in _recovery_paths(database):
        try:
            root_metadata = os.lstat(root)
        except OSError:
            result.append({"path": root.name, "omitted": "unreadable"})
            visited += 1
            continue
        candidates: list[tuple[Path, str]] = []
        if stat.S_ISREG(root_metadata.st_mode):
            candidates.append((root, root.name))
        elif stat.S_ISDIR(root_metadata.st_mode):
            for current, directories, files in os.walk(
                    root, topdown=True, followlinks=False):
                safe_directories = []
                for name in sorted(directories):
                    child = Path(current) / name
                    relative = (PurePosixPath(root.name)
                                / PurePosixPath(*child.relative_to(root).parts))
                    try:
                        metadata = os.lstat(child)
                    except OSError:
                        result.append({"path": relative.as_posix(),
                                       "omitted": "unreadable"})
                        visited += 1
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        safe_directories.append(name)
                    else:
                        result.append({"path": relative.as_posix(),
                                       "omitted": "unsafe_file_type"})
                        visited += 1
                directories[:] = safe_directories
                for name in sorted(files):
                    child = Path(current) / name
                    relative = (PurePosixPath(root.name)
                                / PurePosixPath(*child.relative_to(root).parts))
                    candidates.append((child, relative.as_posix()))
        else:
            result.append({"path": root.name, "omitted": "unsafe_file_type"})
            visited += 1
            continue
        for path, relative in candidates:
            visited += 1
            if visited > MAX_RECOVERY_FILES:
                result.append({
                    "path": "_remaining", "omitted": "file_count_limit"})
                break
            try:
                metadata = os.lstat(path)
                if (not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink > 1):
                    raise ValueError("unsafe file type")
                size = metadata.st_size
            except ValueError:
                result.append({"path": relative,
                               "omitted": "unsafe_file_type"})
                continue
            except OSError:
                result.append({"path": relative, "omitted": "unreadable"})
                continue
            if size > MAX_RECOVERY_FILE_BYTES:
                result.append({
                    "path": relative, "size": size,
                    "omitted": "file_too_large"})
                continue
            if total + size > MAX_RECOVERY_TOTAL_BYTES:
                result.append({
                    "path": relative, "size": size,
                    "omitted": "total_size_limit"})
                continue
            try:
                raw = _read_regular_nofollow(
                    path, MAX_RECOVERY_FILE_BYTES)
            except StoreError:
                result.append({"path": relative,
                               "omitted": "unsafe_file_type"})
                continue
            total += len(raw)
            result.append({
                "path": relative, "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content_base64": base64.b64encode(raw).decode("ascii"),
            })
        if visited > MAX_RECOVERY_FILES:
            break
    omitted = sum("omitted" in item for item in result)
    return result, {
        "complete": omitted == 0,
        "source_count": visited,
        "included_count": len(result) - omitted,
        "omitted_count": omitted,
        "omitted_reasons": sorted({
            item["omitted"] for item in result if "omitted" in item}),
    }


def _fsync_directory(path: Path) -> None:
    if os.name != "posix" or not path.is_dir():
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_output(database: Path, output: Path, encoded: bytes, *,
                    force: bool) -> None:
    """Publish bounded bytes without clobbering, even without hard links."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(
        f".{output.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _guard_export_target(database, output, force=force)
        if force:
            os.replace(temporary, output)
        else:
            try:
                os.link(temporary, output)
            except FileExistsError as exc:
                raise StoreError(
                    "export_target_exists",
                    "export target appeared during publication; nothing was overwritten",
                    409,
                ) from exc
            except OSError:
                # FAT, SMB, and some sandbox filesystems do not support hard
                # links. O_EXCL preserves no-clobber semantics; a process
                # crash can leave only a hash-invalid partial archive, never
                # overwrite an existing user file.
                try:
                    final_descriptor = os.open(
                        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError as exc:
                    raise StoreError(
                        "export_target_exists",
                        "export target appeared during publication; nothing was overwritten",
                        409,
                    ) from exc
                try:
                    with os.fdopen(final_descriptor, "wb") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    try:
                        output.unlink()
                    except FileNotFoundError:
                        pass
                    raise
            else:
                temporary.unlink()
        _fsync_directory(output.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def export_data(database: Path, output: Path,
                include_recovery: bool = False, *, force: bool = False,
                allow_partial: bool = False) -> dict:
    database = database.expanduser().resolve(strict=False)
    output = _guard_export_target(database, output, force=force)
    score_path, state_path = _pending_paths(database)
    try:
        with (inactive_application_lock(database, timeout=2.0),
              maintenance_lock(database, exclusive=True, timeout=2.0)):
            recovered_imports = recover_import_transactions(database)
            store = _existing_store(database)
            score_outbox = PersistentSaveOutbox(
                score_path,
                legacy_path=database.with_name("pending_saves.json"))
            state_outbox = PersistentStateOutbox(state_path, recover=True)
            active_report = _active_protocol_report(database)
            with store.connection() as connection:
                connection.execute("BEGIN")
                schema_row = connection.execute(
                    "SELECT value FROM schema_meta WHERE key='version'"
                ).fetchone()
                if schema_row is None:
                    raise StoreError(
                        "database_unavailable", "database schema version is missing")
                schema_version = int(schema_row[0])
                tables = {
                    table: _bounded_rows(connection, table)
                    for table in EXPORT_TABLES
                }
                connection.commit()
            score_snapshot = score_outbox.snapshot_envelopes()
            state_snapshot = state_outbox.snapshot_entries()
            if (not allow_partial
                    and (not score_snapshot.complete
                         or not state_snapshot.complete
                         or not active_report["complete"])):
                raise StoreError(
                    "incomplete_pending_snapshot",
                    "active pending journals could not be exported completely",
                    details={
                        "scores": score_snapshot.omitted_reasons,
                        "state": state_snapshot.omitted_reasons,
                        "transactions": active_report,
                    })
            score_pending = [
                envelope.to_dict()
                for envelope, _mutation
                in score_snapshot.entries
            ]
            state_pending = list(state_snapshot.entries)
            if include_recovery:
                recovery_evidence, recovery_report = _export_recovery(database)
            else:
                recovery_paths = _recovery_paths(database)
                recovery_evidence = [
                    {"path": path.name, "omitted": "not_selected"}
                    for path in recovery_paths]
                recovery_report = {
                    "complete": not recovery_paths,
                    "source_count": len(recovery_paths),
                    "included_count": 0,
                    "omitted_count": len(recovery_paths),
                    "omitted_reasons": (
                        ["not_selected"] if recovery_paths else []),
                }
            if not allow_partial and not recovery_report["complete"]:
                raise StoreError(
                    "incomplete_recovery_snapshot",
                    "recovery evidence could not be exported completely",
                    details=recovery_report)
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc

    payload = {
        "archive_version": ARCHIVE_VERSION,
        "schema_version": schema_version,
        "exported_at": time.time(),
        "manifest": {
            "format_version": MANIFEST_FORMAT_VERSION,
            "table_counts": {
                table: len(rows) for table, rows in tables.items()},
            "pending": {
                "score_count": len(score_pending),
                "state_count": len(state_pending),
                "score": {
                    "complete": score_snapshot.complete,
                    "source_count": score_snapshot.source_count,
                    "included_count": score_snapshot.included_count,
                    "omitted_count": score_snapshot.omitted_count,
                    "omitted_reasons": score_snapshot.omitted_reasons,
                },
                "state": {
                    "complete": state_snapshot.complete,
                    "source_count": state_snapshot.source_count,
                    "included_count": state_snapshot.included_count,
                    "omitted_count": state_snapshot.omitted_count,
                    "omitted_reasons": state_snapshot.omitted_reasons,
                },
                "semantics": "restorable-active-journals",
                "transactions": active_report,
            },
            "recovery": {
                "count": len(recovery_evidence),
                **recovery_report,
                "semantics": "evidence-only; restored outside active paths",
            },
            "complete": (
                score_snapshot.complete and state_snapshot.complete
                and active_report["complete"]
                and recovery_report["complete"]),
            "snapshot_scope": (
                "persisted data only; close the game before export so worker "
                "queues are empty"),
            "application": {
                "id": "classic-games-hub",
                "version": PACKAGE_VERSION,
                "rulesets": {
                    game_id: GAME_BY_ID[game_id].ruleset_version
                    for game_id in sorted(VALID_GAME_IDS)},
            },
            "reader": {
                "min_version": MANIFEST_FORMAT_VERSION,
                "max_version": MANIFEST_FORMAT_VERSION,
                "required_capabilities": list(ARCHIVE_CAPABILITIES),
            },
            "recovered_imports_before_snapshot": recovered_imports,
            "privacy": (
                "Contains local profile names, gameplay history, settings, "
                "save states, and any selected recovery evidence."),
        },
        "tables": tables,
        "pending_scores": score_pending,
        "pending_state": state_pending,
        "recovery_evidence": recovery_evidence,
    }
    archive_hash = hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()
    archive = {**payload, "manifest_hash": archive_hash}
    encoded = canonical_json(archive).encode("utf-8")
    if len(encoded) > MAX_ARCHIVE_BYTES:
        raise StoreError(
            "export_too_large", "archive exceeds the 128 MiB limit")
    _publish_output(database, output, encoded, force=force)
    return {
        "ok": True, "output": str(output), "bytes": len(encoded),
        "manifest_hash": archive_hash,
        "counts": {table: len(rows) for table, rows in tables.items()},
        "pending_scores": len(score_pending),
        "pending_state": len(state_pending),
        "recovery_files": len(recovery_evidence),
        "complete": payload["manifest"]["complete"],
        "recovered_imports": recovered_imports,
    }


def _load_archive(path: Path) -> dict:
    try:
        size = path.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            raise StoreError(
                "archive_too_large", "archive exceeds the 128 MiB limit")
        raw = path.read_bytes()
        archive = json.loads(
            raw.decode("utf-8"), parse_constant=_reject_json_constant)
        _validate_json_shape(archive)
    except StoreError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError,
            RecursionError, MemoryError) as exc:
        raise StoreError(
            "invalid_archive", "archive is not valid bounded JSON") from exc
    if (not isinstance(archive, dict)
            or archive.get("archive_version")
            not in SUPPORTED_ARCHIVE_VERSIONS
            or not isinstance(archive.get("tables"), dict)):
        raise StoreError("invalid_archive", "unsupported archive format")
    version = archive["archive_version"]
    if version in {2, ARCHIVE_VERSION}:
        for table in IMPORT_TABLES:
            rows = archive["tables"].get(table, [])
            if (not isinstance(rows, list)
                    or any(not isinstance(row, dict) for row in rows)):
                raise StoreError("invalid_archive", f"invalid {table} rows")
        for field in ("pending_scores", "pending_state", "recovery_evidence"):
            if not isinstance(archive.get(field), list):
                raise StoreError("invalid_archive", f"invalid {field}")
        manifest_hash = archive.get("manifest_hash")
        payload = {
            key: value for key, value in archive.items()
            if key != "manifest_hash"}
        expected = hashlib.sha256(
            canonical_json(payload).encode("utf-8")).hexdigest()
        if manifest_hash != expected:
            raise StoreError(
                "archive_hash_mismatch", "archive content does not match its manifest")
        database_schema = archive.get("schema_version")
        if (type(database_schema) is not int
                or not 1 <= database_schema <= STORE_SCHEMA_VERSION):
            raise StoreError(
                "unsupported_archive_schema",
                "archive database schema is newer than this reader")
        manifest = archive.get("manifest")
        if not isinstance(manifest, dict):
            raise StoreError("invalid_archive", "archive manifest is missing")
        manifest_format = manifest.get("format_version")
        if version == ARCHIVE_VERSION:
            if manifest_format != MANIFEST_FORMAT_VERSION:
                raise StoreError(
                    "invalid_archive", "archive v3 requires manifest format 3")
            _validate_current_manifest(archive)
        elif manifest_format == 2:
            _validate_current_manifest(archive)
        elif "format_version" in manifest:
            raise StoreError(
                "invalid_archive", "archive manifest format is unsupported")
        else:
            _validate_legacy_v2_manifest(archive)
            # A format-less v2 archive omitted active reject/restore
            # inventory. Its own bytes cannot prove those files were absent,
            # so it remains a merge source even after an explicit upgrade.
            archive = dict(archive)
            archive["manifest"] = {
                **manifest, "legacy_manifest": True, "complete": False}
    else:
        legacy_evidence = []
        for index, item in enumerate(archive.get("recovery_files", [])):
            if not isinstance(item, dict):
                legacy_evidence.append(item)
                continue
            legacy_item = dict(item)
            raw_path = str(legacy_item.get("path", ""))
            basename = raw_path.replace("\\", "/").rsplit("/", 1)[-1]
            legacy_item["path"] = basename or f"legacy-evidence-{index}"
            legacy_evidence.append(legacy_item)
        archive = {
            **archive,
            "pending_scores": [], "pending_state": [],
            "recovery_evidence": legacy_evidence,
            "manifest": {
                "legacy_archive": True,
                "recovery": {"semantics": "evidence-only"},
            },
        }
    for table in IMPORT_TABLES:
        rows = archive["tables"].get(table, [])
        if (not isinstance(rows, list)
                or any(not isinstance(row, dict) for row in rows)):
            raise StoreError("invalid_archive", f"invalid {table} rows")
    for field in ("pending_scores", "pending_state", "recovery_evidence"):
        if not isinstance(archive.get(field), list):
            raise StoreError("invalid_archive", f"invalid {field}")
    return archive


def _valid_ruleset_catalog(value) -> bool:
    return (isinstance(value, dict)
            and set(value) == set(VALID_GAME_IDS)
            and all(isinstance(item, str) and 1 <= len(item) <= 32
                    and all(char.isascii()
                            and (char.isalnum() or char in "-_.")
                            for char in item)
                    for item in value.values()))


def _validate_current_manifest(archive: dict) -> None:
    manifest = archive["manifest"]
    format_version = manifest.get("format_version")
    if format_version not in STRICT_EVIDENCE_MANIFESTS:
        raise StoreError(
            "invalid_archive", "archive manifest format is unsupported")
    table_counts = manifest.get("table_counts")
    if (not isinstance(table_counts, dict)
            or set(table_counts) != set(EXPORT_TABLES)
            or any(type(table_counts[table]) is not int
                   or table_counts[table] != len(archive["tables"].get(table, []))
                   for table in EXPORT_TABLES)):
        raise StoreError(
            "invalid_archive", "archive table counts do not match its contents")
    application = manifest.get("application")
    if (not isinstance(application, dict)
            or application.get("id") != "classic-games-hub"
            or not _valid_ruleset_catalog(application.get("rulesets"))):
        raise StoreError(
            "invalid_archive", "archive application or rulesets are inconsistent")
    if format_version == MANIFEST_FORMAT_VERSION:
        reader = manifest.get("reader")
        if (not isinstance(application.get("version"), str)
                or not application["version"]
                or not isinstance(reader, dict)
                or type(reader.get("min_version")) is not int
                or type(reader.get("max_version")) is not int
                or not reader["min_version"] <= MANIFEST_FORMAT_VERSION
                <= reader["max_version"]
                or not isinstance(reader.get("required_capabilities"), list)
                or any(not isinstance(capability, str)
                       or capability not in ARCHIVE_CAPABILITIES
                       for capability in reader["required_capabilities"])):
            raise StoreError(
                "unsupported_archive_reader",
                "archive reader compatibility requirements are unsupported")
    pending = manifest.get("pending")
    if (not isinstance(pending, dict)
            or pending.get("score_count") != len(archive.get("pending_scores", []))
            or pending.get("state_count") != len(archive.get("pending_state", []))):
        raise StoreError(
            "invalid_archive", "archive pending counts do not match its contents")
    for name, count_key, values_key in (
            ("score", "score_count", "pending_scores"),
            ("state", "state_count", "pending_state")):
        report = pending.get(name)
        if (not isinstance(report, dict)
                or any(type(report.get(key)) is not int
                       for key in ("source_count", "included_count",
                                   "omitted_count"))
                or report["included_count"] != len(archive[values_key])
                or report["source_count"] != (
                    report["included_count"] + report["omitted_count"])
                or report.get("complete") is not (report["omitted_count"] == 0)
                or pending[count_key] != report["included_count"]):
            raise StoreError(
                "invalid_archive", f"archive {name} report is inconsistent")
    recovery = manifest.get("recovery")
    evidence = archive.get("recovery_evidence", [])
    included = sum(isinstance(item, dict) and "content_base64" in item
                   for item in evidence)
    omitted = sum(isinstance(item, dict) and "omitted" in item
                  for item in evidence)
    if (not isinstance(recovery, dict)
            or any(type(recovery.get(key)) is not int
                   for key in ("count", "source_count", "included_count",
                               "omitted_count"))
            or recovery["count"] != len(evidence)
            or recovery["included_count"] != included
            or recovery["omitted_count"] != omitted
            or recovery["source_count"] != included + omitted
            or recovery.get("complete") is not (omitted == 0)):
        raise StoreError(
            "invalid_archive", "archive recovery report is inconsistent")
    transactions = pending.get("transactions")
    active_complete = (isinstance(transactions, dict)
                       and transactions.get("complete") is True
                       and transactions.get("unresolved_count") == 0)
    expected_complete = (
        pending["score"]["complete"] and pending["state"]["complete"]
        and active_complete and recovery["complete"])
    if manifest.get("complete") is not expected_complete:
        raise StoreError(
            "invalid_archive", "archive completeness flags are inconsistent")


def _validate_legacy_v2_manifest(archive: dict) -> None:
    manifest = archive["manifest"]
    pending = manifest.get("pending")
    recovery = manifest.get("recovery")
    if (not isinstance(pending, dict) or not isinstance(recovery, dict)
            or not isinstance(manifest.get("application"), dict)):
        raise StoreError(
            "invalid_archive", "legacy archive manifest is incomplete")
    normalized = {
        **archive,
        "manifest": {
            **manifest,
            "format_version": 2,
            "pending": {
                **pending,
                "transactions": {
                    "complete": False, "unresolved_count": 1,
                    "legacy_inventory_unavailable": True,
                },
            },
            "complete": False,
        },
    }
    _validate_current_manifest(normalized)


def upgrade_archive(database: Path, source: Path, output: Path, *,
                    force: bool = False) -> dict:
    """Rewrite a verified v2 archive under the explicit v3 reader contract."""
    database = database.expanduser().resolve(strict=False)
    output = _guard_export_target(database, output, force=force)
    archive = _load_archive(source)
    if archive.get("archive_version") != 2:
        raise StoreError(
            "archive_upgrade_not_required",
            "only version-2 archives require this compatibility upgrade")
    old_manifest = archive["manifest"]
    old_format = old_manifest.get("format_version")
    replace_eligible = (
        old_format == 2 and old_manifest.get("complete") is True)
    pending = dict(old_manifest.get("pending", {}))
    if not isinstance(pending.get("transactions"), dict):
        pending["transactions"] = {
            "complete": False,
            "unresolved_count": 1,
            "legacy_inventory_unavailable": True,
        }
        replace_eligible = False
    application = dict(old_manifest.get("application", {}))
    application["version"] = (
        application.get("version")
        or ("0.6.0" if old_format == 2 else "legacy-unknown"))
    manifest = {
        **old_manifest,
        "format_version": MANIFEST_FORMAT_VERSION,
        "application": application,
        "pending": pending,
        "reader": {
            "min_version": MANIFEST_FORMAT_VERSION,
            "max_version": MANIFEST_FORMAT_VERSION,
            "required_capabilities": list(ARCHIVE_CAPABILITIES),
        },
        "complete": replace_eligible,
        "upgraded_from": {
            "archive_version": 2,
            "manifest_format": old_format,
            "source_manifest_hash": archive.get("manifest_hash"),
            "replace_eligibility_proven": replace_eligible,
        },
    }
    payload = {
        key: value for key, value in archive.items()
        if key not in {"manifest_hash", "archive_version", "manifest"}
    }
    payload.update({
        "archive_version": ARCHIVE_VERSION,
        "manifest": manifest,
    })
    _validate_current_manifest(payload)
    _validated_recovery_items(payload)
    digest = hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()
    upgraded = {**payload, "manifest_hash": digest}
    encoded = canonical_json(upgraded).encode("utf-8")
    if len(encoded) > MAX_ARCHIVE_BYTES:
        raise StoreError(
            "export_too_large", "upgraded archive exceeds the 128 MiB limit")
    _publish_output(database, output, encoded, force=force)
    return {
        "ok": True,
        "source": str(source),
        "output": str(output),
        "archive_version": ARCHIVE_VERSION,
        "manifest_hash": digest,
        "replace_eligible": replace_eligible,
        "unproven": ([] if replace_eligible else [
            "legacy archive did not inventory active reject/restore artifacts"]),
    }


def _row_identity(row: dict, *, omit_id: bool = False) -> str:
    value = {key: child for key, child in row.items()
             if not omit_id or key != "id"}
    return canonical_json(value)


def _existing_attempt_collision(
        connection: sqlite3.Connection, row: dict) -> sqlite3.Row | None:
    clauses = []
    params = []
    for key in ("attempt_uuid", "request_id", "source_key"):
        value = row.get(key)
        if value is not None:
            clauses.append(f"{key}=?")
            params.append(value)
    if not clauses:
        return None
    return connection.execute(
        "SELECT * FROM attempts WHERE " + " OR ".join(clauses)
        + " LIMIT 1", params).fetchone()


def _semantic_row_check(table: str, row: dict) -> None:
    for key, value in row.items():
        if key.endswith("_at") and (type(value) not in (int, float)
                                    or not math.isfinite(float(value))
                                    or float(value) < 0):
            raise ValueError(f"invalid timestamp in {key}")
    if table == "profiles":
        ProfileIdentity.validate_uuid(row["profile_id"])
        ProfileIdentity.normalize_display_name(row["display_name"])
    json_fields = {
        "attempts": ("extra_json",),
        "settings": ("value_json",),
        "progress": ("value_json",),
        "save_slots": ("state_json",),
        "invalid_attempts": ("row_json",),
    }.get(table, ())
    decoded = {}
    for field in json_fields:
        raw = row.get(field)
        if table == "attempts" and field == "extra_json" and raw is None:
            decoded[field] = None
            continue
        if not isinstance(raw, str):
            raise ValueError(f"{field} is not JSON text")
        decoded[field] = json.loads(
            raw, parse_constant=_reject_json_constant)
        _validate_json_shape(decoded[field])
    if table == "progress":
        validate_progress(row["game_id"], row["key"], decoded["value_json"])
    if table == "save_slots":
        if row.get("game_id") not in VALID_GAME_IDS:
            raise ValueError("unknown save-slot game")
        state = decoded["state_json"]
        expected_version = (
            state.get("version", 1) if isinstance(state, dict) else 1)
        if expected_version != row.get("state_version"):
            raise ValueError("save state version metadata does not match")
        validate_save_slot_payload(row["game_id"], state)


def _insert_columns(table: str, row: dict) -> list[str]:
    return [key for key in row if not (table in AUTO_ID_TABLES and key == "id")]


def _insert_row(connection: sqlite3.Connection, table: str, row: dict) -> None:
    columns = _insert_columns(table, row)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES({placeholders})",
        tuple(row[column] for column in columns))


def _plan_import(database: Path, archive: dict) -> tuple[dict, dict, list[FileOperation]]:
    store = _existing_store(database)
    plan = {table: [] for table in IMPORT_TABLES}
    result = {
        table: {"new": 0, "exact_duplicates": 0,
                "conflicts": 0, "invalid": 0}
        for table in IMPORT_TABLES
    }
    archive_seen: dict[str, dict[tuple, str]] = {
        table: {} for table in IMPORT_TABLES}
    attempt_seen: dict[tuple[str, object], str] = {}
    errors: list[str] = []
    with store.connection() as connection:
        for table in IMPORT_TABLES:
            allowed = store._table_columns(connection, table)
            natural = NATURAL_KEYS.get(table)
            for index, source_row in enumerate(archive["tables"].get(table, [])):
                row = dict(source_row)
                if not set(row) <= allowed:
                    result[table]["invalid"] += 1
                    errors.append(f"{table}[{index}] has unknown columns")
                    continue
                try:
                    _semantic_row_check(table, row)
                except (KeyError, TypeError, ValueError, ProfileIdentityError,
                        ProgressPolicyError, StoreError, json.JSONDecodeError):
                    result[table]["invalid"] += 1
                    errors.append(f"{table}[{index}] fails semantic validation")
                    continue
                identity = _row_identity(
                    row, omit_id=table in AUTO_ID_TABLES)
                if table == "attempts":
                    duplicate_in_archive = False
                    conflict_in_archive = False
                    for key in ("attempt_uuid", "request_id", "source_key"):
                        value = row.get(key)
                        if value is None:
                            continue
                        prior = attempt_seen.get((key, value))
                        if prior is not None:
                            duplicate_in_archive |= prior == identity
                            conflict_in_archive |= prior != identity
                        else:
                            attempt_seen[(key, value)] = identity
                    if conflict_in_archive:
                        result[table]["conflicts"] += 1
                        errors.append(
                            f"attempts[{index}] collides inside archive")
                        continue
                    if duplicate_in_archive:
                        result[table]["exact_duplicates"] += 1
                        continue
                    existing = _existing_attempt_collision(connection, row)
                elif natural is not None:
                    if any(key not in row for key in natural):
                        result[table]["invalid"] += 1
                        errors.append(f"{table}[{index}] lacks its natural key")
                        continue
                    key_value = tuple(row[key] for key in natural)
                    prior = archive_seen[table].get(key_value)
                    if prior is not None:
                        if prior == identity:
                            result[table]["exact_duplicates"] += 1
                        else:
                            result[table]["conflicts"] += 1
                            errors.append(
                                f"{table}[{index}] conflicts inside archive")
                        continue
                    archive_seen[table][key_value] = identity
                    where = " AND ".join(f"{key}=?" for key in natural)
                    existing = connection.execute(
                        f"SELECT * FROM {table} WHERE {where} LIMIT 1",
                        key_value).fetchone()
                else:
                    columns = _insert_columns(table, row)
                    where = " AND ".join(f"{column} IS ?" for column in columns)
                    existing = connection.execute(
                        f"SELECT * FROM {table} WHERE {where} LIMIT 1",
                        tuple(row[column] for column in columns)).fetchone()
                if existing is not None:
                    existing_identity = _row_identity(
                        dict(existing), omit_id=table in AUTO_ID_TABLES)
                    if existing_identity == identity:
                        result[table]["exact_duplicates"] += 1
                    else:
                        result[table]["conflicts"] += 1
                        errors.append(f"{table}[{index}] conflicts with target")
                    continue
                plan[table].append(row)
                result[table]["new"] += 1

        # Validate exactly the rows the apply phase will insert, against an
        # in-memory copy so preview cannot alter the target file or WAL.
        scratch = sqlite3.connect(":memory:")
        try:
            connection.backup(scratch)
            scratch.execute("PRAGMA foreign_keys=ON")
            scratch.execute("BEGIN")
            for table in IMPORT_TABLES:
                for row in plan[table]:
                    _insert_row(scratch, table, row)
            violation = scratch.execute("PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise sqlite3.IntegrityError(
                    "archive violates data relationships")
            scratch.rollback()
        except (sqlite3.Error, MutationError) as exc:
            errors.append(f"transactional validation failed: {exc}")
        finally:
            scratch.close()

    # Pending entries are validated with the same parsers used on restore.
    for index, value in enumerate(archive["pending_scores"]):
        try:
            envelope, _mutation = PendingSaveEnvelope.parse(value)
            target = _pending_paths(database)[0] / f"{envelope.request_id}.json"
            if target.is_file():
                current_value = json.loads(
                    target.read_text(encoding="utf-8"),
                    parse_constant=_reject_json_constant)
                current, _current_mutation = PendingSaveEnvelope.parse(
                    current_value)
                if current.payload_hash != envelope.payload_hash:
                    raise StoreError(
                        "request_id_conflict",
                        "target pending request ID has another payload")
        except (StoreError, TypeError, ValueError, MutationError):
            errors.append(f"pending_scores[{index}] is invalid")
    for index, value in enumerate(archive["pending_state"]):
        try:
            operation = PersistentStateOutbox._parse(
                canonical_json(value).encode("utf-8"))
            state_path = _pending_paths(database)[1]
            target = state_path / (
                f"{hashlib.sha256(operation['key'].encode('utf-8')).hexdigest()}"
                ".json")
            if target.is_file():
                existing = PersistentStateOutbox._parse(target.read_bytes())
                if operation["kind"] == existing["kind"] == "progress":
                    PersistentStateOutbox._merge_progress_operations(
                        existing, operation)
        except (StoreError, TypeError, ValueError, MutationError,
                RecursionError, OSError, UnicodeError, json.JSONDecodeError):
            errors.append(f"pending_state[{index}] is invalid")
    try:
        _validated_recovery_items(archive)
    except StoreError as exc:
        errors.append(f"recovery evidence is invalid: {exc.message}")
    file_operations: list[FileOperation] = []
    try:
        file_operations = _planned_file_operations(database, archive)
        with store.connection() as connection:
            _preview_pending_against_target(
                database, connection, plan, file_operations)
    except (OSError, sqlite3.Error, StoreError, MutationError, ValueError,
            TypeError, RecursionError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"pending transactional validation failed: {exc}")
    plan_fingerprint = hashlib.sha256(canonical_json({
        "archive": archive.get("manifest_hash"),
        "tables": plan,
        "files": [{
            "target": str(operation.target.relative_to(database.parent)),
            "sha256": hashlib.sha256(operation.data).hexdigest(),
        } for operation in file_operations],
    }).encode("utf-8")).hexdigest()
    summary = {
        "ok": not errors, "tables": result,
        "errors": errors[:50],
        "policy": (
            "exact duplicates are skipped; semantic/identity conflicts "
            "refuse the whole import; new rows are inserted atomically"),
        "pending": {
            "scores": len(archive["pending_scores"]),
            "state": len(archive["pending_state"]),
        },
        "plan_fingerprint": plan_fingerprint,
        "archive_completeness": {
            "complete": archive.get("manifest", {}).get("complete"),
            "recovery_omitted_count": sum(
                isinstance(item, dict) and "omitted" in item
                for item in archive["recovery_evidence"]),
        },
    }
    return summary, plan, file_operations


def preview_import(database: Path, archive_path: Path) -> dict:
    database = database.expanduser().resolve(strict=False)
    archive = _load_archive(archive_path)
    try:
        with (inactive_application_lock(database, timeout=2.0),
              maintenance_lock(database, exclusive=True, timeout=2.0)):
            recovered = recover_import_transactions(database)
            result, _plan, _files = _plan_import(database, archive)
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc
    return {**result, "archive": str(archive_path),
            "archive_version": archive["archive_version"],
            "recovered_imports": recovered}


def _restore_pending(database: Path, archive: dict) -> dict:
    score_path, state_path = _pending_paths(database)
    score_outbox = PersistentSaveOutbox(score_path)
    state_outbox = PersistentStateOutbox(state_path)
    restored_scores = 0
    for value in archive["pending_scores"]:
        envelope, mutation = PendingSaveEnvelope.parse(value)
        current = score_outbox.add_mutation(
            mutation, created_at=envelope.created_at)
        score_outbox.set_attempt_count_max(
            mutation.request_id,
            max(current.attempt_count, envelope.attempt_count))
        restored_scores += 1
    restored_state = 0
    for value in archive["pending_state"]:
        operation = PersistentStateOutbox._parse(
            canonical_json(value).encode("utf-8"))
        state_outbox.put(
            operation["key"], operation["method"], tuple(operation["args"]),
            logical_revision=operation["logical_revision"],
            operation_id=operation["operation_id"],
            components=operation.get("components"),
            updated_at=operation["updated_at"])
        restored_state += 1
    return {"scores": restored_scores, "state": restored_state}


def _safe_evidence_relative(raw: str) -> Path:
    if (not isinstance(raw, str) or not raw or len(raw) > 1024
            or "\\" in raw or ":" in raw
            or any(ord(char) < 32 for char in raw)):
        raise StoreError(
            "invalid_archive", "recovery evidence path is unsafe")
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}
    if (posix.is_absolute() or windows.drive or windows.root
            or not posix.parts or any(
                part in {"", ".", ".."}
                or part.rstrip(" .") != part
                or part.split(".", 1)[0].casefold() in reserved
                for part in posix.parts)):
        raise StoreError(
            "invalid_archive", "recovery evidence path is unsafe")
    return Path(*posix.parts)


def _validated_recovery_items(archive: dict) -> list[tuple[Path, bytes]]:
    items = archive["recovery_evidence"]
    if len(items) > MAX_RECOVERY_FILES:
        raise StoreError(
            "invalid_archive", "recovery evidence exceeds the file-count limit")
    prepared: list[tuple[Path, bytes]] = []
    seen: dict[str, tuple[str, object]] = {}
    total = 0
    strict = archive.get("manifest", {}).get(
        "format_version") in STRICT_EVIDENCE_MANIFESTS
    for item in items:
        if not isinstance(item, dict):
            raise StoreError(
                "invalid_archive", "recovery evidence entry is not an object")
        relative = _safe_evidence_relative(str(item.get("path", "")))
        identity = PurePosixPath(*relative.parts).as_posix()
        if "content_base64" not in item:
            if strict and ("omitted" not in item
                           or not set(item) <= {"path", "size", "omitted"}):
                raise StoreError(
                    "invalid_archive", "recovery omission metadata is incomplete")
            value = ("omitted", canonical_json(item))
            previous = seen.get(identity)
            if previous is not None and previous != value:
                raise StoreError(
                    "invalid_archive", "recovery evidence path is duplicated")
            seen[identity] = value
            continue
        if strict and set(item) != {
                "path", "size", "sha256", "content_base64"}:
            raise StoreError(
                "invalid_archive", "recovery evidence metadata is incomplete")
        try:
            raw = base64.b64decode(item["content_base64"], validate=True)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise StoreError(
                "invalid_archive", "recovery evidence is not valid base64") from exc
        total += len(raw)
        if (len(raw) > MAX_RECOVERY_FILE_BYTES
                or total > MAX_RECOVERY_TOTAL_BYTES):
            raise StoreError(
                "invalid_archive", "recovery evidence exceeds restore limits")
        expected = item.get("sha256")
        if (strict and type(item.get("size")) is not int
                or strict and item.get("size") != len(raw)
                or strict and (not isinstance(expected, str)
                               or len(expected) != 64)
                or expected is not None
                and hashlib.sha256(raw).hexdigest() != expected):
            raise StoreError(
                "archive_hash_mismatch", "recovery evidence hash mismatch")
        value = ("content", hashlib.sha256(raw).hexdigest())
        previous = seen.get(identity)
        if previous is not None:
            if previous != value:
                raise StoreError(
                    "invalid_archive", "recovery evidence path is duplicated")
            total -= len(raw)
            continue
        seen[identity] = value
        prepared.append((relative, raw))
    return prepared


def _restore_recovery_evidence(database: Path, archive: dict) -> dict:
    prepared = _validated_recovery_items(archive)
    if not prepared:
        return {"restored": 0, "directory": None}
    archive_id = archive.get("manifest_hash") or uuid.uuid4().hex
    root = database.parent / "imported-recovery" / archive_id[:24]
    restored = 0
    for relative, raw in prepared:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(
                f"{target.name}.{time.time_ns()}-{uuid.uuid4().hex[:8]}")
        descriptor = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(target.parent)
        restored += 1
    if restored:
        _fsync_directory(root)
        _fsync_directory(root.parent)
    return {"restored": restored,
            "directory": str(root) if restored else None}


def _read_bounded_target(path: Path, limit: int) -> bytes:
    try:
        return _read_regular_nofollow(path, limit)
    except StoreError as exc:
        raise StoreError(
            "import_target_unreadable", "existing import target is unreadable"
        ) from exc


def _planned_file_operations(database: Path, archive: dict) -> list[FileOperation]:
    """Build final pending/evidence bytes without changing the target."""
    score_path, state_path = _pending_paths(database)
    score_values: dict[Path, PendingSaveEnvelope] = {}
    for value in archive["pending_scores"]:
        envelope, _mutation = PendingSaveEnvelope.parse(value)
        target = score_path / f"{envelope.request_id}.json"
        validate_file_operations(database, [FileOperation(target, b"")])
        current = score_values.get(target)
        if current is None and target.is_file():
            raw = json.loads(
                _read_bounded_target(
                    target, MAX_SPOOL_FILE_BYTES).decode("utf-8"),
                parse_constant=_reject_json_constant)
            current, _current_mutation = PendingSaveEnvelope.parse(raw)
        if current is not None:
            if current.payload_hash != envelope.payload_hash:
                raise StoreError(
                    "request_id_conflict",
                    "target pending request ID has another payload")
            envelope = replace(
                current,
                attempt_count=max(
                    current.attempt_count, envelope.attempt_count),
                created_at=min(current.created_at, envelope.created_at))
        score_values[target] = envelope

    state_values: dict[Path, dict] = {}
    for value in archive["pending_state"]:
        operation = PersistentStateOutbox._parse(
            canonical_json(value).encode("utf-8"))
        LocalGameStore.validate_state_operation(operation)
        if operation["method"] == "save_slot":
            validate_save_slot_payload(
                operation["args"][1], operation["args"][3])
        target = state_path / (
            f"{hashlib.sha256(operation['key'].encode('utf-8')).hexdigest()}"
            ".json")
        validate_file_operations(database, [FileOperation(target, b"")])
        current = state_values.get(target)
        if current is None and target.is_file():
            current = PersistentStateOutbox._parse(
                _read_bounded_target(target, MAX_SPOOL_FILE_BYTES))
        if current is not None:
            if operation["kind"] == current["kind"] == "progress":
                operation = PersistentStateOutbox._merge_progress_operations(
                    current, operation)
            elif (PersistentStateOutbox._order(operation)
                  <= PersistentStateOutbox._order(current)):
                operation = current
        state_values[target] = operation

    operations = [
        FileOperation(target, canonical_json(envelope.to_dict()).encode("utf-8"))
        for target, envelope in sorted(
            score_values.items(), key=lambda item: str(item[0]))
    ]
    operations.extend(
        FileOperation(target, canonical_json(operation).encode("utf-8"))
        for target, operation in sorted(
            state_values.items(), key=lambda item: str(item[0])))

    prepared = _validated_recovery_items(archive)
    if prepared:
        archive_id = archive.get("manifest_hash") or hashlib.sha256(
            canonical_json(archive).encode("utf-8")).hexdigest()
        root = database.parent / "imported-recovery" / archive_id[:24]
        for relative, raw in prepared:
            target = root / relative
            validate_file_operations(database, [FileOperation(target, b"")])
            if (target.is_file()
                    and _read_bounded_target(
                        target, MAX_RECOVERY_FILE_BYTES) != raw):
                raise StoreError(
                    "recovery_evidence_conflict",
                    "existing recovery evidence differs from the archive")
            operations.append(FileOperation(target, raw))
    validate_file_operations(database, operations)
    return operations


def _preview_pending_against_target(
        database: Path, connection: sqlite3.Connection, plan: dict,
        operations: list[FileOperation]) -> None:
    """Replay planned pending operations in an isolated target copy."""
    with tempfile.TemporaryDirectory(prefix="classic-games-import-plan-") as root:
        scratch_path = Path(root) / "target.sqlite"
        scratch_connection = sqlite3.connect(str(scratch_path))
        try:
            connection.backup(scratch_connection)
            scratch_connection.row_factory = sqlite3.Row
            scratch_connection.execute("PRAGMA foreign_keys=ON")
            scratch_connection.execute("BEGIN IMMEDIATE")
            for table in IMPORT_TABLES:
                for row in plan[table]:
                    _insert_row(scratch_connection, table, row)
            scratch_store = LocalGameStore(scratch_path, initialize=False)
            scratch_store._seed_state_baselines(
                scratch_connection, missing_only=True)
            scratch_connection.commit()
        finally:
            scratch_connection.close()
        scratch_store = LocalGameStore(scratch_path, initialize=False)
        score_parent, state_parent = _pending_paths(database)
        state_operations = []
        for operation in operations:
            if operation.target.parent == score_parent:
                envelope, mutation = PendingSaveEnvelope.parse(json.loads(
                    operation.data.decode("utf-8"),
                    parse_constant=_reject_json_constant))
                scratch_store.record_mutation(
                    mutation, occurred_at=envelope.created_at)
            elif operation.target.parent == state_parent:
                value = PersistentStateOutbox._parse(operation.data)
                state_operations.append(value)
        priority = {"ensure_profile": 0, "set_setting": 1,
                    "set_progress": 2, "merge_progress": 2, "save_slot": 3}
        state_operations.sort(key=lambda value: (
            priority.get(value["method"], 99), value["updated_at"]))
        for operation in state_operations:
            scratch_store.apply_state_operation(operation)


def import_data(database: Path, archive_path: Path) -> dict:
    database = database.expanduser().resolve(strict=False)
    archive = _load_archive(archive_path)
    try:
        with (inactive_application_lock(database, timeout=2.0),
              maintenance_lock(database, exclusive=True, timeout=2.0)):
            recovered = recover_import_transactions(database)
            # Initialization and migration belong inside the exclusive gate.
            store = LocalGameStore(database)
            preview, plan, file_operations = _plan_import(database, archive)
            if not preview["ok"]:
                raise StoreError(
                    "invalid_archive",
                    "archive preview contains invalid rows or conflicts",
                    details={"preview": preview})
            backup = store._backup_database(store.schema_version())
            transaction = ImportTransaction.prepare(
                database, file_operations)
            inserted = {}
            try:
                with store.connection(timeout_ms=5000) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    for table in IMPORT_TABLES:
                        before = connection.total_changes
                        for row in plan[table]:
                            _insert_row(connection, table, row)
                        inserted[table] = connection.total_changes - before
                    # Imported state receives a baseline only where no receipt
                    # exists. Existing winners are never rewritten or down-rated.
                    store._seed_state_baselines(connection, missing_only=True)
                    violation = connection.execute(
                        "PRAGMA foreign_key_check").fetchone()
                    if violation is not None:
                        raise StoreError(
                            "invalid_archive",
                            "archive violates data relationships")
                    connection.commit()
                transaction.mark("DB_APPLIED")
                transaction.publish_files()
            except Exception as exc:
                transaction.rollback()
                transaction.finish()
                if isinstance(exc, StoreError):
                    raise
                raise StoreError(
                    "import_rolled_back",
                    "import failed after preparation and was rolled back") from exc
            transaction.finish()
            pending = {
                "scores": len(archive["pending_scores"]),
                "state": len(archive["pending_state"]),
            }
            restored_evidence = len(_validated_recovery_items(archive))
            evidence_root = (
                database.parent / "imported-recovery"
                / str(archive.get("manifest_hash") or "")[:24])
            evidence = {
                "restored": restored_evidence,
                "omitted": sum(
                    isinstance(item, dict) and "omitted" in item
                    for item in archive["recovery_evidence"]),
                "complete": archive.get("manifest", {}).get(
                    "recovery", {}).get("complete"),
                "directory": (
                    str(evidence_root) if restored_evidence else None),
            }
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc
    return {
        "ok": True, "backup": str(backup), "inserted": inserted,
        "pending_restored": pending, "recovery_evidence": evidence,
        "preview": preview, "recovered_imports": recovered,
    }


def _replacement_plan(database: Path, archive: dict):
    """Plan an archive against an empty current-schema database."""
    with tempfile.TemporaryDirectory(
            prefix=f".{database.name}.replace-plan-",
            dir=database.parent) as directory:
        empty_database = Path(directory) / "empty.sqlite"
        LocalGameStore(empty_database)
        connection = sqlite3.connect(str(empty_database))
        try:
            expected_schema = {
                (row[0], row[1]): (row[2], row[3])
                for row in connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE type IN ('table','index','trigger','view') "
                    "AND name NOT LIKE 'sqlite_%'")
            }
        finally:
            connection.close()
        preview, plan, temporary_files = _plan_import(empty_database, archive)
        files = [
            FileOperation(
                database.parent
                / operation.target.relative_to(empty_database.parent),
                operation.data)
            for operation in temporary_files
        ]
    planned_targets = {operation.target for operation in files}
    for pending_path in _pending_paths(database):
        try:
            with os.scandir(pending_path) as iterator:
                current = [
                    Path(entry.path) for entry in iterator
                    if (entry.name.endswith(".json")
                        or pending_path.name == "pending-state"
                        and ((entry.name.startswith(".reject-")
                              and entry.name.endswith((".txn", ".tmp")))
                             or (entry.name.startswith(".")
                                 and entry.name.endswith(".restore"))))]
        except (FileNotFoundError, NotADirectoryError):
            current = []
        for target in current:
            if target not in planned_targets:
                files.append(FileOperation(target, None))
    legacy_pending = database.with_name("pending_saves.json")
    if legacy_pending.exists() or legacy_pending.is_symlink():
        files.append(FileOperation(legacy_pending, None))
    for migrated in database.parent.glob("pending_saves.json.migrated-*"):
        files.append(FileOperation(migrated, None))
    validate_file_operations(database, files)
    preview["policy"] = (
        "a complete archive replaces committed tables and active pending "
        "journals after a rollback snapshot")
    return preview, plan, files, expected_schema


def restore_replace_data(database: Path, archive_path: Path) -> dict:
    """Replace active local data from a complete archive, with rollback."""
    database = database.expanduser().resolve(strict=False)
    archive = _load_archive(archive_path)
    if (archive.get("archive_version") != ARCHIVE_VERSION
            or archive.get("manifest", {}).get("complete") is not True):
        raise StoreError(
            "incomplete_archive",
            "replace restore requires a complete current-format archive")
    try:
        with (inactive_application_lock(database, timeout=2.0),
              maintenance_lock(database, exclusive=True, timeout=2.0)):
            recovered = recover_import_transactions(database)
            store = LocalGameStore(database)
            preview, plan, file_operations, expected_schema = _replacement_plan(
                database, archive)
            if not preview["ok"]:
                raise StoreError(
                    "invalid_archive",
                    "archive cannot be restored into an empty current database",
                    details={"preview": preview})
            backup = store._backup_database(store.schema_version())
            transaction = ImportTransaction.prepare(database, file_operations)
            inserted = {}
            try:
                with store.connection(timeout_ms=5000) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    known_tables = {
                        "schema_meta", "attempts", "save_requests",
                        "invalid_attempts", "profiles", "invalid_local_state",
                        "settings", "progress", "save_slots", "state_receipts",
                        "state_merge_receipts", "sqlite_sequence",
                    }
                    explicit_objects = list(connection.execute(
                        "SELECT type,name FROM sqlite_master WHERE "
                        "type IN ('index','trigger','view') "
                        "AND name NOT LIKE 'sqlite_%'"))
                    for object_type, name in explicit_objects:
                        quoted = name.replace('"', '""')
                        connection.execute(
                            f'DROP {object_type.upper()} "{quoted}"')
                    unknown_tables = [
                        row[0] for row in connection.execute(
                            "SELECT name FROM sqlite_master "
                            "WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                        if row[0] not in known_tables]
                    for table in unknown_tables:
                        quoted = table.replace('"', '""')
                        connection.execute(f'DROP TABLE "{quoted}"')
                    for table in (
                            "state_merge_receipts", "state_receipts",
                            "save_requests", *reversed(IMPORT_TABLES)):
                        connection.execute(f"DELETE FROM {table}")
                    connection.execute("DELETE FROM sqlite_sequence")
                    connection.execute(
                        "DELETE FROM schema_meta WHERE key != 'version'")
                    for table in IMPORT_TABLES:
                        before = connection.total_changes
                        for row in plan[table]:
                            _insert_row(connection, table, row)
                        inserted[table] = connection.total_changes - before
                    store._seed_state_baselines(connection, missing_only=True)
                    for (object_type, _name), (_table, sql) in sorted(
                            expected_schema.items()):
                        if object_type != "table" and sql is not None:
                            connection.execute(sql)
                    violation = connection.execute(
                        "PRAGMA foreign_key_check").fetchone()
                    if violation is not None:
                        raise StoreError(
                            "invalid_archive",
                            "archive violates data relationships")
                    actual_schema = {
                        (row[0], row[1]): (row[2], row[3])
                        for row in connection.execute(
                            "SELECT type,name,tbl_name,sql FROM sqlite_master "
                            "WHERE type IN ('table','index','trigger','view') "
                            "AND name NOT LIKE 'sqlite_%'")
                    }
                    if actual_schema != expected_schema:
                        raise StoreError(
                            "schema_replacement_failed",
                            "replace restore did not produce the current schema")
                    connection.commit()
                transaction.mark("DB_APPLIED")
                transaction.publish_files()
            except Exception as exc:
                transaction.rollback()
                transaction.finish()
                if isinstance(exc, StoreError):
                    raise
                raise StoreError(
                    "restore_rolled_back",
                    "replace restore failed and was rolled back") from exc
            transaction.finish()
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc
    return {
        "ok": True, "mode": "replace", "backup": str(backup),
        "inserted": inserted, "preview": preview,
        "pending_restored": {
            "scores": len(archive["pending_scores"]),
            "state": len(archive["pending_state"]),
        },
        "recovered_imports": recovered,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classic Games Hub local data maintenance")
    parser.add_argument("--database", type=Path, default=default_database_path())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show integrity, row, and recovery counts")
    commands.add_parser(
        "transactions", help="list unfinished import transactions")
    recover = commands.add_parser(
        "recover-transactions", help="roll back unfinished import transactions")
    recover.add_argument(
        "--apply", action="store_true",
        help="required before changing transaction or database files")
    recover.add_argument(
        "--allow-legacy-v1", action="store_true",
        help=("after exporting evidence, explicitly permit rollback from an "
              "unauthenticated version-1 transaction"))
    transaction_export = commands.add_parser(
        "export-transaction",
        help="preserve one import transaction as no-follow JSON evidence")
    transaction_export.add_argument("transaction")
    transaction_export.add_argument("output", type=Path)
    transaction_export.add_argument(
        "--force", action="store_true",
        help="replace an existing ordinary evidence file")
    cleanup = commands.add_parser(
        "cleanup-recovery",
        help="plan or remove recovery roots preserved by a complete archive")
    cleanup.add_argument("--older-than-days", type=int, default=30)
    cleanup.add_argument("--archive", type=Path)
    cleanup.add_argument(
        "--apply", action="store_true",
        help="remove only eligible roots verified against --archive")
    export = commands.add_parser("export", help="create a portable JSON archive")
    export.add_argument("output", type=Path)
    export.add_argument("--include-recovery", action="store_true")
    export.add_argument(
        "--allow-partial", action="store_true",
        help="export readable journals and report omissions instead of failing")
    export.add_argument(
        "--force", action="store_true",
        help="replace an existing ordinary archive file")
    upgrade = commands.add_parser(
        "upgrade-archive",
        help="rewrite a verified v2 archive with the v3 reader contract")
    upgrade.add_argument("source", type=Path)
    upgrade.add_argument("output", type=Path)
    upgrade.add_argument(
        "--force", action="store_true",
        help="replace an existing ordinary upgraded archive")
    preview = commands.add_parser(
        "preview-import", help="validate an archive without changing data")
    preview.add_argument("archive", type=Path)
    restore = commands.add_parser(
        "import", help="insert new archive rows after an explicit confirmation")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--apply", action="store_true",
                         help="required before any database change")
    replace_restore = commands.add_parser(
        "restore-replace",
        help="replace local data from a complete archive after confirmation")
    replace_restore.add_argument("archive", type=Path)
    replace_restore.add_argument(
        "--apply", action="store_true",
        help="required before replacing the current database and journals")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = args.database.expanduser().resolve(strict=False)
    try:
        if args.command == "status":
            result = inspect_data(database)
        elif args.command == "transactions":
            result = inspect_transactions(database)
        elif args.command == "recover-transactions" and not args.apply:
            result = {"ok": False, "code": "confirmation_required",
                      "error": "run again with --apply to recover transactions"}
        elif args.command == "recover-transactions":
            result = recover_transactions_data(
                database, allow_legacy_v1=args.allow_legacy_v1)
        elif args.command == "export-transaction":
            result = export_transaction_data(
                database, args.transaction, args.output, force=args.force)
        elif args.command == "cleanup-recovery":
            result = cleanup_recovery_data(
                database, older_than_days=args.older_than_days,
                archive_path=args.archive, apply=args.apply)
        elif args.command == "export":
            result = export_data(
                database, args.output, args.include_recovery,
                force=args.force, allow_partial=args.allow_partial)
        elif args.command == "upgrade-archive":
            result = upgrade_archive(
                database, args.source, args.output, force=args.force)
        elif args.command == "preview-import":
            result = preview_import(database, args.archive)
        elif args.command in {"import", "restore-replace"} and not args.apply:
            result = {"ok": False, "code": "confirmation_required",
                      "error": "run the command again with --apply after preview"}
        elif args.command == "restore-replace":
            result = restore_replace_data(database, args.archive)
        else:
            result = import_data(database, args.archive)
    except (StoreError, sqlite3.Error, OSError) as exc:
        if isinstance(exc, StoreError):
            result = exc.result()
        else:
            result = {"ok": False, "code": "data_operation_failed",
                      "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
