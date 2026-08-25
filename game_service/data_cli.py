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
import sys
import time
import uuid
from pathlib import Path

from .catalog import VALID_GAME_IDS
from .local_backend import (PendingSaveEnvelope, PersistentSaveOutbox,
                            PersistentStateOutbox)
from .maintenance import MaintenanceBusyError, maintenance_lock
from .mutation import MutationError, canonical_json
from .profile import ProfileIdentity, ProfileIdentityError
from .progress import ProgressPolicyError, validate_progress
from .store import LocalGameStore, StoreError, default_database_path

ARCHIVE_VERSION = 2
SUPPORTED_ARCHIVE_VERSIONS = frozenset({1, ARCHIVE_VERSION})
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
        "imported-recovery",
    )
    try:
        entries = list(parent.iterdir())
    except OSError:
        return []
    return sorted(
        (entry for entry in entries
         if any(entry.name.startswith(prefix) for prefix in prefixes)),
        key=lambda path: path.name)


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
    }
    protected_directories = [
        database.with_name("pending"),
        database.with_name("pending-state"),
        *_recovery_paths(database),
    ]
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


def _pending_paths(database: Path) -> tuple[Path, Path]:
    return database.with_name("pending"), database.with_name("pending-state")


def _pending_file_count(path: Path) -> int:
    count = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_file() and entry.name.endswith(".json"):
                    count += 1
                    if count >= MAX_TABLE_ROWS:
                        break
    except (FileNotFoundError, NotADirectoryError, OSError):
        return 0
    return count


def inspect_data(database: Path) -> dict:
    store = _existing_store(database)
    with store.connection() as connection:
        counts = {
            table: int(connection.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in EXPORT_TABLES
        }
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
    score_path, state_path = _pending_paths(database)
    recovery = []
    for path in _recovery_paths(database):
        try:
            size = (path.stat().st_size if path.is_file()
                    else sum(item.stat().st_size for item in path.rglob("*")
                             if item.is_file()))
        except OSError:
            size = None
        recovery.append({
            "path": path.name,
            "kind": "directory" if path.is_dir() else "file",
            "size": size,
        })
    return {
        "ok": integrity == "ok", "database": str(database),
        "schema_version": store.schema_version(), "quick_check": integrity,
        "counts": counts,
        "pending": {
            "scores": _pending_file_count(score_path),
            "state": _pending_file_count(state_path),
        },
        "recovery": recovery,
    }


def _export_recovery(database: Path) -> list[dict]:
    result: list[dict] = []
    total = 0
    visited = 0
    for root in _recovery_paths(database):
        candidates = [root] if root.is_file() else sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: str(path))
        for path in candidates:
            visited += 1
            if visited > MAX_RECOVERY_FILES:
                result.append({
                    "path": "_remaining", "omitted": "file_count_limit"})
                return result
            relative = (root.name if root.is_file() else
                        str(Path(root.name) / path.relative_to(root)))
            try:
                size = path.stat().st_size
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
                raw = path.read_bytes()
            except OSError:
                result.append({"path": relative, "omitted": "unreadable"})
                continue
            total += len(raw)
            result.append({
                "path": relative, "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content_base64": base64.b64encode(raw).decode("ascii"),
            })
    return result


def _fsync_directory(path: Path) -> None:
    if os.name != "posix" or not path.is_dir():
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def export_data(database: Path, output: Path,
                include_recovery: bool = False, *, force: bool = False) -> dict:
    database = database.expanduser().resolve(strict=False)
    store = _existing_store(database)
    output = _guard_export_target(database, output, force=force)
    score_path, state_path = _pending_paths(database)
    try:
        with maintenance_lock(database, exclusive=True, timeout=2.0):
            with store.connection() as connection:
                connection.execute("BEGIN")
                tables = {
                    table: _bounded_rows(connection, table)
                    for table in EXPORT_TABLES
                }
                connection.commit()
            score_pending = [
                envelope.to_dict()
                for envelope, _mutation
                in PersistentSaveOutbox(score_path).list_envelopes()
            ]
            state_pending = PersistentStateOutbox(
                state_path).list_entries(MAX_TABLE_ROWS)
            recovery_evidence = (
                _export_recovery(database) if include_recovery else [])
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc

    payload = {
        "archive_version": ARCHIVE_VERSION,
        "schema_version": store.schema_version(),
        "exported_at": time.time(),
        "manifest": {
            "table_counts": {
                table: len(rows) for table, rows in tables.items()},
            "pending": {
                "score_count": len(score_pending),
                "state_count": len(state_pending),
                "semantics": "restorable-active-journals",
            },
            "recovery": {
                "count": len(recovery_evidence),
                "semantics": "evidence-only; restored outside active paths",
            },
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
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        _fsync_directory(output.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "ok": True, "output": str(output), "bytes": len(encoded),
        "manifest_hash": archive_hash,
        "counts": {table: len(rows) for table, rows in tables.items()},
        "pending_scores": len(score_pending),
        "pending_state": len(state_pending),
        "recovery_files": len(recovery_evidence),
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
    if version == ARCHIVE_VERSION:
        manifest_hash = archive.get("manifest_hash")
        payload = {
            key: value for key, value in archive.items()
            if key != "manifest_hash"}
        expected = hashlib.sha256(
            canonical_json(payload).encode("utf-8")).hexdigest()
        if manifest_hash != expected:
            raise StoreError(
                "archive_hash_mismatch", "archive content does not match its manifest")
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


def _insert_columns(table: str, row: dict) -> list[str]:
    return [key for key in row if not (table in AUTO_ID_TABLES and key == "id")]


def _insert_row(connection: sqlite3.Connection, table: str, row: dict) -> None:
    columns = _insert_columns(table, row)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES({placeholders})",
        tuple(row[column] for column in columns))


def _plan_import(database: Path, archive: dict) -> tuple[dict, dict]:
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
    }
    return summary, plan


def preview_import(database: Path, archive_path: Path) -> dict:
    database = database.expanduser().resolve(strict=False)
    archive = _load_archive(archive_path)
    try:
        with maintenance_lock(database, exclusive=True, timeout=2.0):
            result, _plan = _plan_import(database, archive)
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc
    return {**result, "archive": str(archive_path),
            "archive_version": archive["archive_version"]}


def _restore_pending(database: Path, archive: dict) -> dict:
    score_path, state_path = _pending_paths(database)
    score_outbox = PersistentSaveOutbox(score_path)
    state_outbox = PersistentStateOutbox(state_path)
    restored_scores = 0
    for value in archive["pending_scores"]:
        envelope, mutation = PendingSaveEnvelope.parse(value)
        current = score_outbox.add_mutation(
            mutation, created_at=envelope.created_at)
        for _ in range(max(0, envelope.attempt_count - current.attempt_count)):
            score_outbox.increment_attempt(mutation.request_id)
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
    path = Path(raw)
    if path.is_absolute() or not path.parts or any(
            part in {"", ".", ".."} for part in path.parts):
        raise StoreError(
            "invalid_archive", "recovery evidence path is unsafe")
    return Path(*path.parts)


def _validated_recovery_items(archive: dict) -> list[tuple[Path, bytes]]:
    items = archive["recovery_evidence"]
    if len(items) > MAX_RECOVERY_FILES:
        raise StoreError(
            "invalid_archive", "recovery evidence exceeds the file-count limit")
    prepared: list[tuple[Path, bytes]] = []
    total = 0
    for item in items:
        if not isinstance(item, dict):
            raise StoreError(
                "invalid_archive", "recovery evidence entry is not an object")
        if "content_base64" not in item:
            continue
        relative = _safe_evidence_relative(str(item.get("path", "")))
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
        if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
            raise StoreError(
                "archive_hash_mismatch", "recovery evidence hash mismatch")
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


def import_data(database: Path, archive_path: Path) -> dict:
    database = database.expanduser().resolve(strict=False)
    archive = _load_archive(archive_path)
    store = LocalGameStore(database)
    try:
        with maintenance_lock(database, exclusive=True, timeout=2.0):
            preview, plan = _plan_import(database, archive)
            if not preview["ok"]:
                raise StoreError(
                    "invalid_archive",
                    "archive preview contains invalid rows or conflicts",
                    details={"preview": preview})
            backup = store._backup_database(store.schema_version())
            inserted = {}
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
                        "invalid_archive", "archive violates data relationships")
                connection.commit()
            pending = _restore_pending(database, archive)
            evidence = _restore_recovery_evidence(database, archive)
    except MaintenanceBusyError as exc:
        raise StoreError(
            "maintenance_busy", str(exc), 409, retryable=True) from exc
    return {
        "ok": True, "backup": str(backup), "inserted": inserted,
        "pending_restored": pending, "recovery_evidence": evidence,
        "preview": preview,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classic Games Hub local data maintenance")
    parser.add_argument("--database", type=Path, default=default_database_path())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show integrity, row, and recovery counts")
    export = commands.add_parser("export", help="create a portable JSON archive")
    export.add_argument("output", type=Path)
    export.add_argument("--include-recovery", action="store_true")
    export.add_argument(
        "--force", action="store_true",
        help="replace an existing ordinary archive file")
    preview = commands.add_parser(
        "preview-import", help="validate an archive without changing data")
    preview.add_argument("archive", type=Path)
    restore = commands.add_parser(
        "import", help="insert new archive rows after an explicit confirmation")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--apply", action="store_true",
                         help="required before any database change")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = args.database.expanduser().resolve(strict=False)
    try:
        if args.command == "status":
            result = inspect_data(database)
        elif args.command == "export":
            result = export_data(
                database, args.output, args.include_recovery,
                force=args.force)
        elif args.command == "preview-import":
            result = preview_import(database, args.archive)
        elif not args.apply:
            result = {"ok": False, "code": "confirmation_required",
                      "error": "run import again with --apply after preview"}
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
