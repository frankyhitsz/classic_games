"""SQLite-backed local records shared by pygame and the optional API."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .catalog import VALID_GAME_IDS

SCHEMA_VERSION = 1
MAX_EXTRA_BYTES = 8 * 1024
MAX_SCORE = 2_147_483_647


class StoreError(Exception):
    """Stable error contract for local callers and the Flask adapter."""

    def __init__(self, code: str, message: str, status: int = 400,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable

    def result(self) -> dict:
        return {"ok": False, "code": self.code, "error": self.message,
                "retryable": self.retryable}


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


def _canonical_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StoreError("invalid_extra",
                         "extra must contain valid JSON values") from exc


def _normalize_payload(game_id: str, player: str, score: int, extra,
                       replace: bool, submission_id: Optional[int]) -> dict:
    if not isinstance(game_id, str):
        raise StoreError("invalid_game_id", "game_id must be a string")
    game_id = game_id.strip()
    if game_id not in VALID_GAME_IDS:
        raise StoreError("unknown_game", f"unknown game_id: {game_id}", 404)
    if not isinstance(player, str):
        raise StoreError("invalid_player", "player must be a string")
    player = unicodedata.normalize("NFC", player).strip() or "anonymous"
    if len(player) > 32:
        raise StoreError("invalid_player",
                         "player must be at most 32 characters")
    if any(unicodedata.category(ch).startswith("C") for ch in player):
        raise StoreError("invalid_player", "player contains control characters")
    if type(score) is not int or not 0 <= score <= MAX_SCORE:
        raise StoreError("invalid_score",
                         f"score must be an integer between 0 and {MAX_SCORE}")
    if extra is not None and not isinstance(extra, dict):
        raise StoreError("invalid_extra", "extra must be an object or null")
    extra_json = _canonical_json(extra) if extra is not None else None
    if extra_json is not None and len(extra_json.encode("utf-8")) > MAX_EXTRA_BYTES:
        raise StoreError("extra_too_large", "extra exceeds 8 KiB")
    if not isinstance(replace, bool):
        raise StoreError("invalid_replace", "replace must be boolean")
    if (submission_id is not None
            and (type(submission_id) is not int or submission_id <= 0)):
        raise StoreError("invalid_submission_id",
                         "submission_id must be a positive integer")
    return {"game_id": game_id, "player": player, "score": score,
            "extra": extra, "extra_json": extra_json, "replace": replace,
            "submission_id": submission_id}


def _validate_request_id(request_id: Optional[str]) -> str:
    request_id = request_id or uuid.uuid4().hex
    if (not isinstance(request_id, str)
            or not 16 <= len(request_id) <= 64
            or not all(ch.isascii() and (ch.isalnum() or ch in "-_")
                       for ch in request_id)):
        raise StoreError(
            "invalid_request_id",
            "request_id must be 16-64 ASCII letters, digits, - or _")
    return request_id


class LocalGameStore:
    """Short-transaction repository for attempts and personal bests."""

    def __init__(self, db_path: Optional[Path | str] = None,
                 legacy_db_path: Optional[Path | str] = None,
                 initialize: bool = True):
        self.db_path = Path(db_path) if db_path is not None else default_database_path()
        self.legacy_db_path = (Path(legacy_db_path)
                               if legacy_db_path is not None else None)
        self.migration_backup: Optional[Path] = None
        if initialize:
            self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
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
        if (self.db_path.is_file() and self.db_path.stat().st_size > 0
                and existing_version < SCHEMA_VERSION):
            self.migration_backup = self._backup_database(existing_version)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    game_id TEXT NOT NULL,
                    player TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'classic',
                    ruleset_version TEXT NOT NULL DEFAULT '1',
                    status TEXT NOT NULL DEFAULT 'completed',
                    score INTEGER NOT NULL,
                    extra_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    source_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_attempts_game_score
                    ON attempts(game_id, score DESC);
                CREATE INDEX IF NOT EXISTS idx_attempts_recent
                    ON attempts(updated_at DESC);
                CREATE TABLE IF NOT EXISTS save_requests (
                    request_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS progress (
                    profile_id TEXT NOT NULL,
                    game_id TEXT NOT NULL,
                    ruleset_version TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (profile_id, game_id)
                );
                CREATE TABLE IF NOT EXISTS save_slots (
                    profile_id TEXT NOT NULL,
                    game_id TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    ruleset_version TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (profile_id, game_id, slot)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            self._import_embedded_legacy_scores(conn)
            conn.commit()
        self._import_legacy_scores()

    def _import_embedded_legacy_scores(self, conn: sqlite3.Connection) -> None:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scores'"
        ).fetchone()
        if table is None:
            return
        imported = conn.execute(
            "SELECT value FROM schema_meta "
            "WHERE key='embedded_legacy_scores_v1'"
        ).fetchone()
        if imported is not None:
            return
        columns = {row["name"] for row in
                   conn.execute("PRAGMA table_info(scores)").fetchall()}
        required = {"id", "game_id", "player", "score", "extra", "created_at"}
        if not required <= columns:
            raise StoreError("invalid_legacy_schema",
                             "legacy scores table is missing required columns")
        updated_expr = ("COALESCE(updated_at, created_at)"
                        if "updated_at" in columns else "created_at")
        game_ids = sorted(VALID_GAME_IDS)
        placeholders = ",".join("?" for _ in game_ids)
        conn.execute(
            "INSERT OR IGNORE INTO attempts "
            "(request_id, game_id, player, score, extra_json, created_at, "
            "updated_at, source_key) "
            "SELECT 'legacy-score-' || printf('%016d', id), game_id, "
            "COALESCE(player, 'anonymous'), score, extra, created_at, "
            f"{updated_expr}, 'legacy-score:' || id FROM scores "
            f"WHERE game_id IN ({placeholders})",
            game_ids,
        )
        conn.execute(
            "INSERT INTO schema_meta(key, value) "
            "VALUES('embedded_legacy_scores_v1', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(time.time()),),
        )

    def _existing_schema_version(self) -> int:
        if not self.db_path.is_file() or self.db_path.stat().st_size == 0:
            return 0
        with self.connection() as conn:
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
        backup = self.db_path.with_name(
            f"{self.db_path.name}.backup-v{version}-{int(time.time())}")
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

    @staticmethod
    def payload_hash(payload: dict) -> str:
        canonical = _canonical_json(payload)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def record_score(self, game_id: str, player: str, score: int,
                     extra=None, replace: bool = False,
                     submission_id: Optional[int] = None,
                     request_id: Optional[str] = None) -> dict:
        normalized = _normalize_payload(
            game_id, player, score, extra, replace, submission_id)
        request_id = _validate_request_id(request_id)
        semantic_payload = {
            "game_id": normalized["game_id"],
            "player": normalized["player"],
            "score": normalized["score"],
            "extra": normalized["extra"],
            "replace": normalized["replace"],
            "submission_id": normalized["submission_id"],
        }
        payload_hash = self.payload_hash(semantic_payload)
        now = time.time()

        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT payload_hash, response_json FROM save_requests "
                "WHERE request_id=?", (request_id,),
            ).fetchone()
            if prior is not None:
                if prior["payload_hash"] != payload_hash:
                    conn.rollback()
                    raise StoreError(
                        "request_id_conflict",
                        "request_id was already used for another score", 409)
                response = json.loads(prior["response_json"])
                response["duplicate_request"] = True
                conn.commit()
                return response

            best_row = conn.execute(
                "SELECT MAX(score) AS best FROM attempts "
                "WHERE game_id=? AND player=?",
                (normalized["game_id"], normalized["player"]),
            ).fetchone()
            best_before = best_row["best"] if best_row else None

            attempt = None
            if normalized["submission_id"] is not None:
                attempt = conn.execute(
                    "SELECT id, score, extra_json, updated_at FROM attempts "
                    "WHERE id=? AND game_id=? AND player=?",
                    (normalized["submission_id"], normalized["game_id"],
                     normalized["player"]),
                ).fetchone()

            updated = False
            no_op = False
            if attempt is None:
                cur = conn.execute(
                    "INSERT INTO attempts "
                    "(request_id, game_id, player, score, extra_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (request_id, normalized["game_id"], normalized["player"],
                     normalized["score"], normalized["extra_json"], now, now),
                )
                attempt_id = cur.lastrowid
                stored_score = normalized["score"]
            else:
                attempt_id = attempt["id"]
                stored_score = max(attempt["score"], normalized["score"])
                effective = (normalized["score"] > attempt["score"]
                             or (normalized["score"] == attempt["score"]
                                 and normalized["extra_json"]
                                 != attempt["extra_json"]))
                if effective:
                    conn.execute(
                        "UPDATE attempts SET score=?, extra_json=?, "
                        "updated_at=? WHERE id=?",
                        (stored_score, normalized["extra_json"], now,
                         attempt_id),
                    )
                    updated = True
                else:
                    no_op = True

            best_after_row = conn.execute(
                "SELECT MAX(score) AS best FROM attempts "
                "WHERE game_id=? AND player=?",
                (normalized["game_id"], normalized["player"]),
            ).fetchone()
            personal_best = best_after_row["best"] or 0
            new_personal_best = (best_before is None
                                 or personal_best > best_before)
            rank = conn.execute(
                "SELECT COUNT(*) + 1 FROM ("
                "SELECT player, MAX(score) AS best FROM attempts "
                "WHERE game_id=? GROUP BY player) WHERE best > ?",
                (normalized["game_id"], stored_score),
            ).fetchone()[0]
            response = {
                "ok": True,
                "id": attempt_id,
                "request_id": request_id,
                "rank": rank,
                "score": stored_score,
                "attempt_recorded": True,
                "new_personal_best": new_personal_best,
                "personal_best": personal_best,
                "updated": updated,
                "no_op": no_op,
                "preserved": False,
                "replaced": 0,
                "duplicate_request": False,
            }
            conn.execute(
                "INSERT INTO save_requests "
                "(request_id, payload_hash, response_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (request_id, payload_hash, _canonical_json(response), now),
            )
            conn.commit()
            return response

    def leaderboard(self, game_id: str, limit: int = 10) -> list[dict]:
        if game_id not in VALID_GAME_IDS:
            raise StoreError("unknown_game", f"unknown game_id: {game_id}", 404)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError("invalid_limit", "limit must be between 1 and 50")
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player, MAX(score) AS score, MIN(created_at) AS ts "
                "FROM attempts WHERE game_id=? GROUP BY player "
                "ORDER BY score DESC, ts ASC LIMIT ?", (game_id, limit),
            ).fetchall()
        result = []
        rank = 0
        previous_score = None
        for index, row in enumerate(rows):
            if row["score"] != previous_score:
                rank = index + 1
                previous_score = row["score"]
            result.append({"rank": rank, "player": row["player"],
                           "score": row["score"], "ts": row["ts"]})
        return result

    def recent(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError("invalid_limit", "limit must be between 1 and 50")
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT game_id, player, score, updated_at FROM attempts "
                "ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [{"game_id": row["game_id"], "player": row["player"],
                 "score": row["score"], "ts": row["updated_at"]}
                for row in rows]

    def stats(self, game_id: str) -> dict:
        if game_id not in VALID_GAME_IDS:
            raise StoreError("unknown_game", f"unknown game_id: {game_id}", 404)
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS attempts, MAX(score) AS best, "
                "AVG(score) AS avg FROM attempts WHERE game_id=?",
                (game_id,),
            ).fetchone()
        return {"game_id": game_id, "attempts": row["attempts"],
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

    def _import_legacy_scores(self) -> None:
        legacy = self.legacy_db_path
        if legacy is None or not legacy.is_file():
            return
        try:
            if legacy.resolve() == self.db_path.resolve():
                return
        except OSError:
            return
        with self.connection() as conn:
            imported = conn.execute(
                "SELECT value FROM schema_meta WHERE key='legacy_scores_v1'"
            ).fetchone()
        if imported is not None:
            return
        legacy_conn = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
        legacy_conn.row_factory = sqlite3.Row
        try:
            table = legacy_conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='scores'"
            ).fetchone()
            if table:
                columns = {row["name"] for row in legacy_conn.execute(
                    "PRAGMA table_info(scores)").fetchall()}
                updated_expr = ("COALESCE(updated_at, created_at)"
                                if "updated_at" in columns else "created_at")
                rows = legacy_conn.execute(
                    "SELECT id, game_id, player, score, extra, created_at, "
                    f"{updated_expr} AS updated_at FROM scores"
                ).fetchall()
            else:
                rows = []
        finally:
            legacy_conn.close()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in rows:
                if row["game_id"] not in VALID_GAME_IDS:
                    continue
                source_key = f"legacy-score:{row['id']}"
                request_id = f"legacy-score-{row['id']:016d}"
                conn.execute(
                    "INSERT OR IGNORE INTO attempts "
                    "(request_id, game_id, player, score, extra_json, "
                    "created_at, updated_at, source_key) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (request_id, row["game_id"], row["player"], row["score"],
                     row["extra"], row["created_at"], row["updated_at"],
                     source_key),
                )
            conn.execute(
                "INSERT INTO schema_meta(key, value) "
                "VALUES('legacy_scores_v1', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(time.time()),),
            )
            conn.commit()
