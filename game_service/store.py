"""SQLite records repository shared by pygame and the optional API."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import platform
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .catalog import GAME_BY_ID, VALID_GAME_IDS
from .mutation import (ATTEMPT_STATUSES, MutationError, ScoreMutation,
                       canonical_json, normalize_score_mutation)
from .service import StorageStatus

SCHEMA_VERSION = 3
# The render thread never waits on this budget. A quarter second gives the
# optional Flask adapter and direct maintenance callers room to serialize
# ordinary bursts while still falling back far sooner than the old 5 s wait.
DEFAULT_BUSY_TIMEOUT_MS = 250
RECEIPT_RETENTION_DAYS = 180
LEGACY_RULESET_VERSION = "legacy-v1"
LEGACY_REQUIRED_COLUMNS = {
    "id", "game_id", "player", "score", "extra", "created_at"
}


class StoreError(Exception):
    """Stable error contract for local callers and the Flask adapter."""

    def __init__(self, code: str, message: str, status: int = 400,
                 retryable: bool = False, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.details = details or {}

    def result(self) -> dict:
        return {"ok": False, "code": self.code, "error": self.message,
                "retryable": self.retryable, **self.details}

    @classmethod
    def from_mutation(cls, exc: MutationError) -> "StoreError":
        return cls(exc.code, exc.message, exc.status, retryable=False)


def default_data_dir() -> Path:
    override = os.environ.get("GAMES_DATA_DIR")
    if override:
        return Path(override).expanduser()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ClassicGamesHub"
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / "ClassicGamesHub" if base else Path.home() / "ClassicGamesHub"
    xdg = os.environ.get("XDG_DATA_HOME")
    return ((Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share")
            / "classic-games")


def default_database_path() -> Path:
    override = os.environ.get("GAMES_DB")
    return Path(override).expanduser() if override else default_data_dir() / "games.db"


def _unique_sibling(path: Path, label: str) -> Path:
    return path.with_name(
        f"{path.name}.{label}-{time.time_ns()}-{uuid.uuid4().hex[:8]}")


class LocalGameStore:
    """Short-transaction repository for attempts and personal bests."""

    def __init__(self, db_path: Optional[Path | str] = None,
                 legacy_db_path: Optional[Path | str] = None,
                 initialize: bool = True,
                 busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS):
        self.db_path = Path(db_path) if db_path is not None else default_database_path()
        self.legacy_db_path = (Path(legacy_db_path)
                               if legacy_db_path is not None else None)
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self.migration_backup: Optional[Path] = None
        self.migration_notice: Optional[str] = None
        self._migration_messages: list[str] = []
        self._legacy_summaries: dict[str, dict[str, int]] = {}
        if initialize:
            self.initialize()

    def _connect(self, timeout_ms: Optional[int] = None) -> sqlite3.Connection:
        budget = self.busy_timeout_ms if timeout_ms is None else timeout_ms
        conn = sqlite3.connect(str(self.db_path), timeout=budget / 1000.0)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {max(0, int(budget))}")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connection(self, timeout_ms: Optional[int] = None) -> Iterator[sqlite3.Connection]:
        conn = self._connect(timeout_ms)
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        existing_version = self._existing_schema_version()
        if existing_version > SCHEMA_VERSION:
            raise StoreError(
                "unsupported_schema",
                f"database schema {existing_version} is newer than supported "
                f"version {SCHEMA_VERSION}", 409)
        if (existing_version == SCHEMA_VERSION
                and self._schema_is_current()):
            return
        if (self.db_path.is_file() and self.db_path.stat().st_size > 0
                and existing_version <= SCHEMA_VERSION):
            self.migration_backup = self._backup_database(existing_version)

        with self.connection(timeout_ms=max(250, self.busy_timeout_ms)) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_meta ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS attempts ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "attempt_uuid TEXT UNIQUE, request_id TEXT NOT NULL UNIQUE, "
                    "profile_id TEXT, game_id TEXT NOT NULL, player TEXT NOT NULL, "
                    "mode TEXT NOT NULL DEFAULT 'classic', "
                    "ruleset_version TEXT NOT NULL DEFAULT 'legacy-v1', "
                    "status TEXT NOT NULL DEFAULT 'completed', "
                    "revision INTEGER NOT NULL DEFAULT 1, score INTEGER NOT NULL, "
                    "extra_json TEXT, started_at REAL, finished_at REAL, "
                    "score_achieved_at REAL, created_at REAL NOT NULL, "
                    "updated_at REAL NOT NULL, source_key TEXT UNIQUE)")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS save_requests ("
                    "request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, "
                    "attempt_uuid TEXT, revision INTEGER, "
                    "response_json TEXT NOT NULL, created_at REAL NOT NULL, "
                    "expires_at REAL)")
                self._ensure_v2_columns(conn)
                self._migrate_attempt_rows(conn)
                self._migrate_rulesets_v3(conn)
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_request_id "
                    "ON attempts(request_id)")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_uuid "
                    "ON attempts(attempt_uuid)")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_source_key "
                    "ON attempts(source_key) WHERE source_key IS NOT NULL")
                conn.execute(
                    "DELETE FROM save_requests WHERE request_id IN ("
                    "SELECT request_id FROM save_requests GROUP BY request_id "
                    "HAVING COUNT(*) > 1)")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "idx_save_requests_request_id ON save_requests(request_id)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_attempts_best "
                    "ON attempts(profile_id, game_id, mode, "
                    "ruleset_version, status, score DESC)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_attempts_recent "
                    "ON attempts(profile_id, updated_at DESC)")
                self._archive_unused_tables(conn)
                self._import_embedded_legacy_scores(conn)
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SCHEMA_VERSION),),
                )
                cutoff = time.time()
                conn.execute(
                    "UPDATE save_requests SET expires_at=created_at + ? "
                    "WHERE expires_at IS NULL",
                    (RECEIPT_RETENTION_DAYS * 86400,))
                conn.execute(
                    "DELETE FROM save_requests WHERE expires_at IS NOT NULL "
                    "AND expires_at < ?", (cutoff,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self._import_legacy_scores()
        if self._migration_messages:
            self.migration_notice = "；".join(self._migration_messages)

    def _schema_is_current(self) -> bool:
        required_attempts = {
            "id", "attempt_uuid", "request_id", "profile_id", "game_id",
            "player", "mode", "ruleset_version", "status", "revision",
            "score", "extra_json", "started_at", "finished_at",
            "score_achieved_at", "created_at", "updated_at", "source_key",
        }
        required_receipts = {
            "request_id", "payload_hash", "attempt_uuid", "revision",
            "response_json", "created_at", "expires_at",
        }
        with self.connection() as conn:
            if not required_attempts <= self._table_columns(conn, "attempts"):
                return False
            if not required_receipts <= self._table_columns(conn, "save_requests"):
                return False
            required_indexes = {
                "idx_attempts_request_id": (True, ("request_id",)),
                "idx_attempts_uuid": (True, ("attempt_uuid",)),
                "idx_attempts_source_key": (True, ("source_key",)),
                "idx_attempts_best": (False, (
                    "profile_id", "game_id", "mode", "ruleset_version",
                    "status", "score")),
                "idx_attempts_recent": (False, ("profile_id", "updated_at")),
            }
            indexes = self._table_indexes(conn, "attempts")
            if any(indexes.get(name) != expected
                   for name, expected in required_indexes.items()):
                return False
            receipt_indexes = self._table_indexes(conn, "save_requests")
            if receipt_indexes.get("idx_save_requests_request_id") != (
                    True, ("request_id",)):
                return False
            embedded = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scores'"
            ).fetchone()
            if embedded is not None:
                marker = conn.execute(
                    "SELECT 1 FROM schema_meta "
                    "WHERE key='embedded_legacy_scores_v3'"
                ).fetchone()
                if marker is None:
                    return False
            if self.legacy_db_path is not None and self.legacy_db_path.is_file():
                marker_key, fingerprint = self._legacy_marker(self.legacy_db_path)
                marker = conn.execute(
                    "SELECT value FROM schema_meta WHERE key=?", (marker_key,)
                ).fetchone()
                if marker is None or not self._legacy_marker_matches(
                        marker["value"], fingerprint):
                    return False
        return True

    def maintenance(self) -> None:
        """Bounded housekeeping suitable for the background local worker."""
        cutoff = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM save_requests WHERE expires_at IS NOT NULL "
                "AND expires_at < ?", (cutoff,))
            conn.commit()

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in
                conn.execute(f"PRAGMA table_info({table})").fetchall()}

    @staticmethod
    def _table_indexes(conn: sqlite3.Connection, table: str) -> dict:
        result = {}
        for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
            name = row["name"]
            columns = tuple(item["name"] for item in
                            conn.execute(f"PRAGMA index_info({name})").fetchall())
            result[name] = (bool(row["unique"]), columns)
        return result

    def _ensure_v2_columns(self, conn: sqlite3.Connection) -> None:
        attempt_columns = self._table_columns(conn, "attempts")
        additions = {
            "attempt_uuid": "TEXT",
            "profile_id": "TEXT",
            "mode": "TEXT NOT NULL DEFAULT 'classic'",
            "ruleset_version": "TEXT NOT NULL DEFAULT 'legacy-v1'",
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "started_at": "REAL",
            "finished_at": "REAL",
            "score_achieved_at": "REAL",
            "source_key": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in attempt_columns:
                conn.execute(f"ALTER TABLE attempts ADD COLUMN {name} {declaration}")

        receipt_columns = self._table_columns(conn, "save_requests")
        receipt_additions = {
            "attempt_uuid": "TEXT",
            "revision": "INTEGER",
            "expires_at": "REAL",
        }
        for name, declaration in receipt_additions.items():
            if name not in receipt_columns:
                conn.execute(
                    f"ALTER TABLE save_requests ADD COLUMN {name} {declaration}")

    def _migrate_attempt_rows(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT id, player, game_id, attempt_uuid FROM attempts "
            "WHERE attempt_uuid IS NULL OR profile_id IS NULL "
            "OR started_at IS NULL OR finished_at IS NULL "
            "OR score_achieved_at IS NULL"
        ).fetchall()
        for row in rows:
            attempt_uuid = row["attempt_uuid"] or uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"classic-games-v1:{self.db_path}:{row['id']}").hex
            player = row["player"] or "anonymous"
            conn.execute(
                "UPDATE attempts SET attempt_uuid=?, "
                "profile_id=COALESCE(profile_id, ?), "
                "ruleset_version=COALESCE(ruleset_version, ?), "
                "revision=COALESCE(revision, 1), "
                "started_at=COALESCE(started_at, created_at), "
                "finished_at=COALESCE(finished_at, updated_at), "
                "score_achieved_at=COALESCE(score_achieved_at, created_at) "
                "WHERE id=?",
                (attempt_uuid, player, LEGACY_RULESET_VERSION, row["id"]),
            )

    @staticmethod
    def _migrate_rulesets_v3(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE attempts SET ruleset_version=? WHERE source_key IS NOT NULL",
            (LEGACY_RULESET_VERSION,))
        for game_id, descriptor in GAME_BY_ID.items():
            conn.execute(
                "UPDATE attempts SET ruleset_version=? WHERE game_id=? "
                "AND source_key IS NULL AND ruleset_version IN ('1', 'legacy-v1')",
                (descriptor.ruleset_version, game_id))

    @staticmethod
    def _archive_unused_tables(conn: sqlite3.Connection) -> None:
        for table in ("progress", "save_slots", "settings"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            if exists is None:
                continue
            count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if count == 0:
                conn.execute(f"DROP TABLE {table}")
                continue
            suffix = int(time.time())
            archived = f"legacy_{table}_v3_{suffix}"
            while conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (archived,)
            ).fetchone() is not None:
                suffix += 1
                archived = f"legacy_{table}_v3_{suffix}"
            conn.execute(f"ALTER TABLE {table} RENAME TO {archived}")

    def _existing_schema_version(self) -> int:
        if not self.db_path.is_file() or self.db_path.stat().st_size == 0:
            return 0
        with self.connection(timeout_ms=5000) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='schema_meta'"
            ).fetchone()
            if table is None:
                return 0
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
        try:
            return int(row["value"]) if row else 0
        except (TypeError, ValueError) as exc:
            raise StoreError("invalid_schema", "invalid schema version") from exc

    def _backup_database(self, version: int) -> Path:
        backup = _unique_sibling(self.db_path, f"backup-v{version}")
        source = sqlite3.connect(str(self.db_path), timeout=5.0)
        target = sqlite3.connect(str(backup))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return backup

    def schema_version(self) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
        return int(row["value"]) if row else 0

    def health(self) -> bool:
        with self.connection() as conn:
            conn.execute("SELECT 1 FROM attempts LIMIT 1").fetchone()
        return True

    def storage_status(self, outbox_writable: bool = True,
                       recovery_notice: Optional[str] = None) -> StorageStatus:
        readable = writable = False
        code = None
        try:
            with self.connection() as conn:
                conn.execute("SELECT 1 FROM attempts LIMIT 1").fetchone()
            readable = True
        except sqlite3.Error:
            code = "database_unreadable"
        if readable:
            try:
                with self.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.rollback()
                writable = True
            except sqlite3.Error:
                code = "database_unwritable"
        ok = readable and (writable or outbox_writable)
        return StorageStatus(
            ok=ok, readable=readable, writable=writable,
            outbox_writable=outbox_writable, error_code=code,
            retryable=bool(code), recovery_notice=recovery_notice)

    @staticmethod
    def payload_hash(payload: dict) -> str:
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def record_score(self, game_id: str, player: str, score: int,
                     extra=None, replace: bool = False,
                     submission_id: Optional[int] = None,
                     request_id: Optional[str] = None,
                     attempt_uuid: Optional[str] = None,
                     revision: Optional[int] = None,
                     profile_id: Optional[str] = None,
                     mode: str = "classic",
                     ruleset_version: Optional[str] = None,
                     status: str = "completed") -> dict:
        try:
            mutation = normalize_score_mutation(
                game_id, player, score, extra, replace, submission_id,
                request_id, attempt_uuid, revision, profile_id, mode,
                ruleset_version, status)
        except MutationError as exc:
            raise StoreError.from_mutation(exc) from exc
        return self.record_mutation(mutation)

    @staticmethod
    def _identity_matches(row: sqlite3.Row, mutation: ScoreMutation) -> bool:
        return all((
            row["game_id"] == mutation.game_id,
            row["player"] == mutation.player,
            row["profile_id"] == mutation.profile_id,
            row["mode"] == mutation.mode,
            row["ruleset_version"] == mutation.ruleset_version,
        ))

    @staticmethod
    def _best_filter(mutation: ScoreMutation) -> tuple:
        return (mutation.game_id, mutation.mode, mutation.ruleset_version,
                "completed")

    def _personal_best(self, conn: sqlite3.Connection,
                       mutation: ScoreMutation) -> Optional[int]:
        row = conn.execute(
            "SELECT MAX(score) AS best FROM attempts WHERE profile_id=? "
            "AND game_id=? AND mode=? AND ruleset_version=? AND status=?",
            (mutation.profile_id, *self._best_filter(mutation)),
        ).fetchone()
        return row["best"] if row else None

    def _personal_best_rank(self, conn: sqlite3.Connection,
                            mutation: ScoreMutation,
                            personal_best: Optional[int]) -> Optional[int]:
        if personal_best is None or mutation.status != "completed":
            return None
        return int(conn.execute(
            "SELECT COUNT(*) + 1 FROM ("
            "SELECT profile_id, MAX(score) AS best FROM attempts "
            "WHERE game_id=? AND mode=? AND ruleset_version=? AND status=? "
            "GROUP BY profile_id) WHERE best > ?",
            (*self._best_filter(mutation), personal_best),
        ).fetchone()[0])

    def record_mutation(self, mutation: ScoreMutation) -> dict:
        now = time.time()
        payload_hash = mutation.payload_hash
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT payload_hash, response_json FROM save_requests "
                "WHERE request_id=?", (mutation.request_id,),
            ).fetchone()
            if prior is not None:
                if prior["payload_hash"] != payload_hash:
                    conn.rollback()
                    raise StoreError(
                        "request_id_conflict",
                        "request_id was already used for another score", 409)
                try:
                    response = json.loads(prior["response_json"])
                    if not isinstance(response, dict) or response.get("ok") is not True:
                        raise ValueError("invalid receipt response")
                except (TypeError, ValueError, json.JSONDecodeError):
                    # The attempt row is authoritative. A damaged response
                    # cache must not make an otherwise valid retry fail.
                    conn.execute(
                        "DELETE FROM save_requests WHERE request_id=?",
                        (mutation.request_id,))
                else:
                    response["duplicate_request"] = True
                    conn.commit()
                    return response

            best_before = self._personal_best(conn, mutation)
            attempt = conn.execute(
                "SELECT * FROM attempts WHERE attempt_uuid=?",
                (mutation.attempt_uuid,),
            ).fetchone()

            if mutation.submission_id is not None:
                by_id = conn.execute(
                    "SELECT * FROM attempts WHERE id=?",
                    (mutation.submission_id,),
                ).fetchone()
                if by_id is None:
                    conn.rollback()
                    raise StoreError(
                        "submission_not_found", "submission_id does not exist", 404)
                if not self._identity_matches(by_id, mutation):
                    conn.rollback()
                    raise StoreError(
                        "submission_mismatch",
                        "submission_id belongs to another attempt", 409)
                if (mutation.attempt_uuid_provided
                        and by_id["attempt_uuid"] != mutation.attempt_uuid):
                    conn.rollback()
                    raise StoreError(
                        "submission_mismatch",
                        "submission_id and attempt_uuid identify different attempts", 409)
                if attempt is not None and attempt["id"] != by_id["id"]:
                    conn.rollback()
                    raise StoreError(
                        "submission_mismatch",
                        "submission_id and attempt_uuid identify different attempts", 409)
                attempt = by_id

            updated = False
            no_op = False
            stale_revision = False
            legacy_update = (mutation.submission_id is not None
                             and not mutation.attempt_uuid_provided
                             and not mutation.revision_provided)

            if attempt is None:
                cur = conn.execute(
                    "INSERT INTO attempts "
                    "(attempt_uuid, request_id, profile_id, game_id, player, "
                    "mode, ruleset_version, status, revision, score, "
                    "extra_json, started_at, finished_at, score_achieved_at, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (mutation.attempt_uuid, mutation.request_id,
                     mutation.profile_id, mutation.game_id, mutation.player,
                     mutation.mode, mutation.ruleset_version, mutation.status,
                     mutation.revision, mutation.score, mutation.extra_json,
                     now, now, now, now, now),
                )
                attempt_id = int(cur.lastrowid)
                attempt_uuid = mutation.attempt_uuid
                stored_score = mutation.score
                stored_revision = mutation.revision
                stored_status = mutation.status
            else:
                if not self._identity_matches(attempt, mutation):
                    conn.rollback()
                    raise StoreError(
                        "attempt_identity_conflict",
                        "attempt_uuid belongs to another game or profile", 409)
                attempt_id = int(attempt["id"])
                attempt_uuid = attempt["attempt_uuid"]
                stored_score = int(attempt["score"])
                stored_revision = int(attempt["revision"])
                stored_status = attempt["status"]
                if mutation.status != stored_status:
                    conn.rollback()
                    raise StoreError(
                        "attempt_status_conflict",
                        "an attempt cannot change between practice and completed",
                        409)
                policy = GAME_BY_ID[mutation.game_id].score_policy

                if legacy_update:
                    previous_score = stored_score
                    if policy == "final_only":
                        conn.rollback()
                        raise StoreError(
                            "attempt_finalized",
                            "this game records one final score per attempt", 409)
                    if mutation.score < stored_score:
                        conn.rollback()
                        raise StoreError(
                            "score_regression",
                            "a later attempt revision cannot lower its score", 409)
                    effective = (mutation.score > stored_score
                                 or (mutation.score == stored_score
                                     and mutation.extra_json != attempt["extra_json"]))
                    if effective:
                        conn.execute(
                            "UPDATE attempts SET score=?, extra_json=?, "
                            "score_achieved_at=CASE WHEN ? > ? THEN ? "
                            "ELSE score_achieved_at END, "
                            "updated_at=?, finished_at=? WHERE id=?",
                            (mutation.score, mutation.extra_json,
                             mutation.score, previous_score, now, now, now,
                             attempt_id),
                        )
                        stored_score = mutation.score
                        updated = True
                    else:
                        no_op = True
                elif mutation.revision < stored_revision:
                    no_op = True
                    stale_revision = True
                elif mutation.revision == stored_revision:
                    same = (mutation.score == stored_score
                            and mutation.extra_json == attempt["extra_json"]
                            and mutation.status == stored_status)
                    if not same:
                        conn.rollback()
                        raise StoreError(
                            "revision_conflict",
                            "revision was already used for another attempt state", 409)
                    no_op = True
                else:
                    if policy == "final_only":
                        conn.rollback()
                        raise StoreError(
                            "attempt_finalized",
                            "this game records one final score per attempt", 409)
                    if mutation.score < stored_score:
                        conn.rollback()
                        raise StoreError(
                            "score_regression",
                            "a later attempt revision cannot lower its score", 409)
                    previous_score = stored_score
                    stored_score = mutation.score
                    stored_revision = mutation.revision
                    conn.execute(
                        "UPDATE attempts SET score=?, extra_json=?, "
                        "revision=?, score_achieved_at=CASE WHEN ? > ? THEN ? "
                        "ELSE score_achieved_at END, updated_at=?, "
                        "finished_at=? WHERE id=?",
                        (stored_score, mutation.extra_json,
                         stored_revision, mutation.score, previous_score, now,
                         now, now, attempt_id),
                    )
                    updated = True

            best_after = self._personal_best(conn, mutation)
            personal_best = best_after or 0
            new_personal_best = (mutation.status == "completed"
                                 and (best_before is None
                                      or personal_best > best_before))
            personal_best_rank = self._personal_best_rank(
                conn, mutation, best_after)
            response = {
                "ok": True,
                "id": attempt_id,
                "attempt_uuid": attempt_uuid,
                "revision": stored_revision,
                "status": stored_status,
                "request_id": mutation.request_id,
                "rank": personal_best_rank,
                "personal_best_rank": personal_best_rank,
                "score": stored_score,
                "attempt_recorded": True,
                "new_personal_best": new_personal_best,
                "personal_best": personal_best,
                "updated": updated,
                "no_op": no_op,
                "stale_revision": stale_revision,
                "preserved": False,
                "replaced": 0,
                "duplicate_request": False,
            }
            expires_at = now + RECEIPT_RETENTION_DAYS * 86400
            conn.execute(
                "INSERT INTO save_requests "
                "(request_id, payload_hash, attempt_uuid, revision, "
                "response_json, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mutation.request_id, payload_hash, attempt_uuid,
                 mutation.revision, canonical_json(response), now, expires_at),
            )
            conn.commit()
            return response

    @staticmethod
    def _query_dimensions(game_id: str, profile_id: Optional[str],
                          mode: str, ruleset_version: Optional[str],
                          status: str) -> tuple[str, tuple]:
        if game_id not in VALID_GAME_IDS:
            raise StoreError("unknown_game", f"unknown game_id: {game_id}", 404)
        profile_id = LocalGameStore._query_identifier(
            profile_id, "profile_id", 64, optional=True)
        mode = LocalGameStore._query_identifier(mode, "mode", 32)
        ruleset_version = LocalGameStore._query_identifier(
            ruleset_version, "ruleset_version", 32, optional=True)
        status = LocalGameStore._query_identifier(status, "status", 16)
        if status not in ATTEMPT_STATUSES:
            raise StoreError(
                "invalid_status",
                f"status must be one of: {', '.join(sorted(ATTEMPT_STATUSES))}")
        ruleset_version = ruleset_version or GAME_BY_ID[game_id].ruleset_version
        clauses = ["game_id=?", "mode=?", "ruleset_version=?", "status=?"]
        params: list = [game_id, mode, ruleset_version, status]
        if profile_id is not None:
            clauses.append("profile_id=?")
            params.append(profile_id)
        return " AND ".join(clauses), tuple(params)

    @staticmethod
    def _query_identifier(value, field: str, maximum: int,
                          optional: bool = False) -> Optional[str]:
        if value is None and optional:
            return None
        if not isinstance(value, str):
            raise StoreError(f"invalid_{field}", f"{field} must be a string")
        value = value.strip()
        if not 1 <= len(value) <= maximum or any(ord(ch) < 32 for ch in value):
            raise StoreError(
                f"invalid_{field}",
                f"{field} must contain 1-{maximum} visible characters")
        return value

    def leaderboard(self, game_id: str, limit: int = 10, *,
                    mode: str = "classic",
                    ruleset_version: Optional[str] = None,
                    status: str = "completed") -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError("invalid_limit", "limit must be between 1 and 50")
        where, params = self._query_dimensions(
            game_id, None, mode, ruleset_version, status)
        with self.connection() as conn:
            rows = conn.execute(
                "WITH best_attempts AS ("
                "SELECT profile_id, player, score, score_achieved_at, id, "
                "ROW_NUMBER() OVER (PARTITION BY profile_id "
                "ORDER BY score DESC, score_achieved_at ASC, id ASC) AS pick "
                f"FROM attempts WHERE {where}) "
                "SELECT profile_id, player, score, score_achieved_at AS ts "
                "FROM best_attempts WHERE pick=1 "
                "ORDER BY score DESC, ts ASC, profile_id ASC LIMIT ?",
                (*params, limit),
            ).fetchall()
        result = []
        rank = 0
        previous_score = None
        for index, row in enumerate(rows):
            if row["score"] != previous_score:
                rank = index + 1
                previous_score = row["score"]
            result.append({"rank": rank, "profile_id": row["profile_id"],
                           "player": row["player"], "score": row["score"],
                           "ts": row["ts"]})
        return result

    def recent(self, limit: int = 20, *, profile_id: Optional[str] = None,
               game_id: Optional[str] = None, mode: str = "classic",
               ruleset_version: Optional[str] = None,
               status: str = "completed") -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError("invalid_limit", "limit must be between 1 and 50")
        profile_id = self._query_identifier(
            profile_id, "profile_id", 64, optional=True)
        mode = self._query_identifier(mode, "mode", 32)
        ruleset_version = self._query_identifier(
            ruleset_version, "ruleset_version", 32, optional=True)
        status = self._query_identifier(status, "status", 16)
        if status not in ATTEMPT_STATUSES:
            raise StoreError(
                "invalid_status",
                f"status must be one of: {', '.join(sorted(ATTEMPT_STATUSES))}")
        clauses = ["mode=?", "status=?"]
        params: list = [mode, status]
        if game_id is not None:
            if game_id not in VALID_GAME_IDS:
                raise StoreError("unknown_game", f"unknown game_id: {game_id}", 404)
            clauses.append("game_id=?")
            params.append(game_id)
        if ruleset_version is not None:
            clauses.append("ruleset_version=?")
            params.append(ruleset_version)
        elif game_id is None:
            current = sorted(
                (descriptor.id, descriptor.ruleset_version)
                for descriptor in GAME_BY_ID.values())
            clauses.append("(" + " OR ".join(
                "(game_id=? AND ruleset_version=?)" for _ in current) + ")")
            for pair in current:
                params.extend(pair)
        else:
            clauses.append("ruleset_version=?")
            params.append(GAME_BY_ID[game_id].ruleset_version)
        if profile_id is not None:
            clauses.append("profile_id=?")
            params.append(profile_id)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT attempt_uuid, game_id, player, score, updated_at "
                f"FROM attempts WHERE {' AND '.join(clauses)} "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [{"attempt_uuid": row["attempt_uuid"],
                 "game_id": row["game_id"], "player": row["player"],
                 "score": row["score"], "ts": row["updated_at"]}
                for row in rows]

    def stats(self, game_id: str, *, profile_id: Optional[str] = None,
              mode: str = "classic",
              ruleset_version: Optional[str] = None,
              status: str = "completed") -> dict:
        where, params = self._query_dimensions(
            game_id, profile_id, mode, ruleset_version, status)
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS attempts, MAX(score) AS best, "
                f"AVG(score) AS avg FROM attempts WHERE {where}", params,
            ).fetchone()
        return {"game_id": game_id, "profile_id": profile_id,
                "mode": mode,
                "ruleset_version": (ruleset_version
                                    or GAME_BY_ID[game_id].ruleset_version),
                "status": status, "attempts": row["attempts"],
                "records": row["attempts"],
                "best": row["best"] if row["best"] is not None else 0,
                "avg": round(row["avg"], 2) if row["avg"] is not None else 0}

    def attempt_count(self, game_id: Optional[str] = None) -> int:
        with self.connection() as conn:
            if game_id is None:
                row = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM attempts WHERE game_id=?",
                    (game_id,),
                ).fetchone()
        return int(row[0])

    def _legacy_rows(self, legacy_conn: sqlite3.Connection,
                     source: str) -> list[tuple]:
        table = legacy_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scores'"
        ).fetchone()
        if table is None:
            self._migration_messages.append(f"{source}没有可导入的 scores 表")
            self._legacy_summaries[source] = {
                "valid": 0, "skipped": 0, "metadata_recovered": 0,
            }
            return []
        columns = self._table_columns(legacy_conn, "scores")
        missing = sorted(LEGACY_REQUIRED_COLUMNS - columns)
        if missing:
            self._migration_messages.append(
                f"{source}缺少字段，已跳过导入：{', '.join(missing)}")
            self._legacy_summaries[source] = {
                "valid": 0, "skipped": 0, "metadata_recovered": 0,
            }
            return []
        updated_expr = ("COALESCE(updated_at, created_at)"
                        if "updated_at" in columns else "created_at")
        rows = legacy_conn.execute(
            "SELECT id, game_id, player, score, extra, created_at, "
            f"{updated_expr} AS updated_at FROM scores"
        ).fetchall()
        valid = []
        skipped = 0
        metadata_recovered = 0
        for row in rows:
            try:
                row_id = row["id"]
                if type(row_id) is not int or row_id <= 0:
                    raise ValueError("invalid id")
                created_at = float(row["created_at"])
                updated_at = float(row["updated_at"])
                if (not math.isfinite(created_at)
                        or not math.isfinite(updated_at)
                        or created_at < 0 or updated_at < 0):
                    raise ValueError("invalid timestamp")
                source_hash = hashlib.sha256(
                    source.encode("utf-8")).hexdigest()[:16]
                request_id = f"legacy-{source_hash}-{row_id:016d}"
                base_mutation = normalize_score_mutation(
                    row["game_id"], row["player"] or "anonymous",
                    row["score"], extra=None, request_id=request_id,
                    attempt_uuid=uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"classic-games-legacy:{source}:{row_id}").hex,
                    ruleset_version=LEGACY_RULESET_VERSION)
                extra, lost_metadata = self._decode_legacy_extra(row["extra"])
                if lost_metadata:
                    mutation = base_mutation
                    metadata_recovered += 1
                else:
                    try:
                        mutation = normalize_score_mutation(
                            row["game_id"], row["player"] or "anonymous",
                            row["score"], extra=extra, request_id=request_id,
                            attempt_uuid=base_mutation.attempt_uuid,
                            ruleset_version=LEGACY_RULESET_VERSION)
                    except MutationError as exc:
                        if exc.code not in {"invalid_extra", "extra_too_large"}:
                            raise
                        mutation = base_mutation
                        metadata_recovered += 1
                valid.append((mutation, created_at, updated_at, row_id))
            except (MutationError, ValueError, TypeError, json.JSONDecodeError):
                skipped += 1
        if skipped:
            self._migration_messages.append(f"{source}有 {skipped} 条无效记录已跳过")
        if metadata_recovered:
            self._migration_messages.append(
                f"{source}有 {metadata_recovered} 条成绩已恢复，附加信息无法读取")
        self._legacy_summaries[source] = {
            "valid": len(valid), "skipped": skipped,
            "metadata_recovered": metadata_recovered,
        }
        return valid

    @classmethod
    def _decode_legacy_extra(cls, raw) -> tuple[Optional[dict], bool]:
        if raw is None:
            return None, False
        if not isinstance(raw, str):
            return None, True
        for decoder in (json.loads, ast.literal_eval):
            try:
                value = decoder(raw)
            except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if value is None:
                return None, False
            if not isinstance(value, dict) or not cls._legacy_value_is_safe(value):
                return None, True
            try:
                encoded = canonical_json(value)
            except MutationError:
                return None, True
            if len(encoded.encode("utf-8")) > 8 * 1024:
                return None, True
            return value, False
        return None, True

    @classmethod
    def _legacy_value_is_safe(cls, value) -> bool:
        if value is None or isinstance(value, (str, bool)):
            return True
        if type(value) in (int, float):
            return type(value) is int or math.isfinite(value)
        if isinstance(value, list):
            return all(cls._legacy_value_is_safe(item) for item in value)
        if isinstance(value, dict):
            return all(isinstance(key, str)
                       and cls._legacy_value_is_safe(item)
                       for key, item in value.items())
        return False

    @staticmethod
    def _insert_legacy_rows(conn: sqlite3.Connection, rows, source: str) -> int:
        imported = 0
        for mutation, created_at, updated_at, row_id in rows:
            source_key = f"{source}:{row_id}"
            existing = conn.execute(
                "SELECT id FROM attempts WHERE source_key=? "
                "OR attempt_uuid=? OR request_id=? LIMIT 1",
                (source_key, mutation.attempt_uuid,
                 mutation.request_id)).fetchone()
            values = (
                mutation.attempt_uuid, mutation.request_id,
                mutation.profile_id, mutation.game_id, mutation.player,
                mutation.mode, mutation.ruleset_version, mutation.status,
                mutation.revision, mutation.score, mutation.extra_json,
                created_at, updated_at, created_at, created_at, updated_at,
                source_key,
            )
            if existing is None:
                conn.execute(
                    "INSERT INTO attempts "
                    "(attempt_uuid, request_id, profile_id, game_id, player, "
                    "mode, ruleset_version, status, revision, score, extra_json, "
                    "started_at, finished_at, score_achieved_at, created_at, "
                    "updated_at, source_key) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values)
                imported += 1
                continue
            conn.execute(
                "UPDATE attempts SET attempt_uuid=?, request_id=?, profile_id=?, "
                "game_id=?, player=?, mode=?, ruleset_version=?, status=?, "
                "revision=?, score=?, extra_json=?, started_at=?, finished_at=?, "
                "score_achieved_at=?, created_at=?, updated_at=?, source_key=? "
                "WHERE id=?",
                (*values, existing["id"]))
        return imported

    def _import_embedded_legacy_scores(self, conn: sqlite3.Connection) -> None:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scores'"
        ).fetchone()
        if table is None:
            return
        marker = conn.execute(
            "SELECT value FROM schema_meta "
            "WHERE key='embedded_legacy_scores_v3'"
        ).fetchone()
        if marker is not None:
            return
        rows = self._legacy_rows(conn, "embedded-legacy")
        imported = self._insert_legacy_rows(conn, rows, "embedded-legacy")
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("embedded_legacy_scores_v3",
             canonical_json({"time": time.time(), "imported": imported})),
        )

    @staticmethod
    def _legacy_marker(path: Path) -> tuple[str, dict]:
        resolved = str(path.resolve())
        stat = path.stat()
        path_hash = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:24]
        fingerprint = {
            "path": resolved, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "migration_version": SCHEMA_VERSION,
        }
        return f"legacy_scores_v3_{path_hash}", fingerprint

    @staticmethod
    def _legacy_marker_matches(value: str, fingerprint: dict) -> bool:
        try:
            marker = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return (isinstance(marker, dict)
                and all(marker.get(key) == expected
                        for key, expected in fingerprint.items()))

    def _import_legacy_scores(self) -> None:
        legacy = self.legacy_db_path
        if legacy is None or not legacy.is_file():
            return
        try:
            if legacy.resolve() == self.db_path.resolve():
                return
        except OSError:
            return
        try:
            marker_key, fingerprint = self._legacy_marker(legacy)
        except OSError:
            self._migration_messages.append("旧成绩库状态无法读取，已跳过")
            return
        with self.connection() as conn:
            marker = conn.execute(
                "SELECT value FROM schema_meta WHERE key=?", (marker_key,)
            ).fetchone()
        if marker is not None and self._legacy_marker_matches(
                marker["value"], fingerprint):
            return
        try:
            legacy_conn = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
            legacy_conn.row_factory = sqlite3.Row
            try:
                rows = self._legacy_rows(legacy_conn, str(legacy.resolve()))
            finally:
                legacy_conn.close()
        except (sqlite3.Error, OSError) as exc:
            self._migration_messages.append(
                f"旧成绩库无法读取，已跳过：{type(exc).__name__}")
            return
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            imported = self._insert_legacy_rows(
                conn, rows, f"legacy:{legacy.resolve()}")
            summary = self._legacy_summaries.get(str(legacy.resolve()), {})
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (marker_key, canonical_json({
                    **fingerprint, **summary, "imported": imported,
                    "imported_at": time.time(),
                })),
            )
            conn.commit()
