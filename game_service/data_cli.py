"""Inspect, export, preview, and restore local Classic Games Hub data."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from .mutation import canonical_json
from .store import LocalGameStore, StoreError, default_database_path

ARCHIVE_VERSION = 1
EXPORT_TABLES = (
    "profiles", "attempts", "settings", "progress", "save_slots",
    "invalid_attempts", "invalid_local_state",
)
IMPORT_TABLES = (
    "profiles", "attempts", "settings", "progress", "save_slots",
    "invalid_attempts", "invalid_local_state",
)
NATURAL_KEYS = {
    "profiles": ("profile_id",),
    "attempts": ("attempt_uuid",),
    "settings": ("profile_id", "key"),
    "progress": ("profile_id", "game_id", "ruleset_version", "key"),
    "save_slots": ("profile_id", "game_id", "slot_id"),
    "invalid_attempts": ("id",),
    "invalid_local_state": ("id",),
}
MAX_RECOVERY_FILE_BYTES = 8 * 1024 * 1024


def _existing_store(database: Path) -> LocalGameStore:
    if not database.is_file():
        raise StoreError("database_not_found", "local database does not exist", 404)
    return LocalGameStore(database, initialize=False)


def _rows(connection: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def _recovery_paths(database: Path) -> list[Path]:
    parent = database.parent
    prefixes = (
        f"{database.name}.backup-", f"{database.name}.corrupt-",
        "pending-quarantine", "pending-state-quarantine",
        "pending-migration-backup", "pending-state-migration-backup",
    )
    paths: list[Path] = []
    try:
        entries = list(parent.iterdir())
    except OSError:
        return paths
    for entry in entries:
        if any(entry.name.startswith(prefix) for prefix in prefixes):
            paths.append(entry)
    return sorted(paths, key=lambda path: path.name)


def inspect_data(database: Path) -> dict:
    store = _existing_store(database)
    with store.connection() as connection:
        counts = {
            table: int(connection.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in EXPORT_TABLES
        }
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
    recovery = []
    for path in _recovery_paths(database):
        try:
            size = (path.stat().st_size if path.is_file()
                    else sum(item.stat().st_size for item in path.iterdir()
                             if item.is_file()))
        except OSError:
            size = None
        recovery.append({"path": str(path), "kind": (
            "directory" if path.is_dir() else "file"), "size": size})
    return {
        "ok": integrity == "ok", "database": str(database),
        "schema_version": store.schema_version(), "quick_check": integrity,
        "counts": counts, "recovery": recovery,
    }


def export_data(database: Path, output: Path,
                include_recovery: bool = False) -> dict:
    store = _existing_store(database)
    with store.connection() as connection:
        tables = {table: _rows(connection, table) for table in EXPORT_TABLES}
    recovery_files = []
    if include_recovery:
        for root in _recovery_paths(database):
            candidates = [root] if root.is_file() else [
                path for path in root.iterdir() if path.is_file()]
            for path in candidates:
                try:
                    raw = path.read_bytes()
                except OSError:
                    continue
                if len(raw) > MAX_RECOVERY_FILE_BYTES:
                    recovery_files.append({
                        "path": str(path), "size": len(raw),
                        "omitted": "file_too_large"})
                    continue
                recovery_files.append({
                    "path": str(path), "size": len(raw),
                    "content_base64": base64.b64encode(raw).decode("ascii")})
    archive = {
        "archive_version": ARCHIVE_VERSION,
        "schema_version": store.schema_version(),
        "exported_at": time.time(),
        "tables": tables,
        "recovery_files": recovery_files,
    }
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    encoded = canonical_json(archive).encode("utf-8")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {"ok": True, "output": str(output), "bytes": len(encoded),
            "counts": {table: len(rows) for table, rows in tables.items()},
            "recovery_files": len(recovery_files)}


def _load_archive(path: Path) -> dict:
    try:
        archive = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise StoreError("invalid_archive", "archive is not valid JSON") from exc
    if (not isinstance(archive, dict)
            or archive.get("archive_version") != ARCHIVE_VERSION
            or not isinstance(archive.get("tables"), dict)):
        raise StoreError("invalid_archive", "unsupported archive format")
    for table in IMPORT_TABLES:
        rows = archive["tables"].get(table, [])
        if (not isinstance(rows, list)
                or any(not isinstance(row, dict) for row in rows)):
            raise StoreError("invalid_archive", f"invalid {table} rows")
    return archive


def preview_import(database: Path, archive_path: Path) -> dict:
    store = _existing_store(database)
    archive = _load_archive(archive_path)
    tables = archive["tables"]
    result = {}
    with store.connection() as connection:
        for table in IMPORT_TABLES:
            allowed = store._table_columns(connection, table)
            keys = NATURAL_KEYS[table]
            new = conflicts = invalid = 0
            for row in tables.get(table, []):
                if not set(row) <= allowed or any(key not in row for key in keys):
                    invalid += 1
                    continue
                where = " AND ".join(f"{key}=?" for key in keys)
                exists = connection.execute(
                    f"SELECT 1 FROM {table} WHERE {where} LIMIT 1",
                    tuple(row[key] for key in keys)).fetchone()
                if exists is None:
                    new += 1
                else:
                    conflicts += 1
            result[table] = {
                "new": new, "conflicts": conflicts, "invalid": invalid}
    return {"ok": not any(item["invalid"] for item in result.values()),
            "archive": str(archive_path), "tables": result,
            "policy": "existing rows win; new rows are inserted atomically"}


def import_data(database: Path, archive_path: Path) -> dict:
    preview = preview_import(database, archive_path)
    if not preview["ok"]:
        raise StoreError(
            "invalid_archive", "archive contains incompatible table rows")
    archive = _load_archive(archive_path)
    store = LocalGameStore(database)
    backup = store._backup_database(store.schema_version())
    inserted = {}
    try:
        with store.connection(timeout_ms=5000) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for table in IMPORT_TABLES:
                before = connection.total_changes
                allowed = store._table_columns(connection, table)
                for row in archive["tables"].get(table, []):
                    columns = [column for column in row if column in allowed]
                    placeholders = ",".join("?" for _ in columns)
                    connection.execute(
                        f"INSERT OR IGNORE INTO {table} "
                        f"({','.join(columns)}) VALUES({placeholders})",
                        tuple(row[column] for column in columns))
                inserted[table] = connection.total_changes - before
            store._seed_state_baselines(connection)
            violation = connection.execute(
                "PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise StoreError(
                    "invalid_archive", "archive violates data relationships")
            connection.commit()
    except Exception:
        raise
    return {"ok": True, "backup": str(backup), "inserted": inserted,
            "preview": preview}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classic Games Hub local data maintenance")
    parser.add_argument("--database", type=Path, default=default_database_path())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show integrity, row, and recovery counts")
    export = commands.add_parser("export", help="create a portable JSON archive")
    export.add_argument("output", type=Path)
    export.add_argument("--include-recovery", action="store_true")
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
    database = args.database.expanduser().resolve()
    try:
        if args.command == "status":
            result = inspect_data(database)
        elif args.command == "export":
            result = export_data(
                database, args.output, args.include_recovery)
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
