"""Backend-compatible facade over the in-process local SQLite store."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Optional

from .catalog import public_games
from .store import LocalGameStore, StoreError, default_database_path


def completed_future(value=None, exception: Optional[BaseException] = None) -> Future:
    future = Future()
    if exception is not None:
        future.set_exception(exception)
    else:
        future.set_result(value)
    return future


class PersistentSaveOutbox:
    """Small atomic JSON outbox used only when a local transaction fails."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _read_unlocked(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            corrupt = self.path.with_name(
                f"{self.path.name}.corrupt-{int(time.time())}")
            try:
                os.replace(self.path, corrupt)
            except OSError:
                pass
            return []
        return [item for item in value
                if isinstance(item, dict) and isinstance(item.get("request_id"), str)]

    def list(self) -> list[dict]:
        with self._lock:
            return self._read_unlocked()

    def _write_unlocked(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(items, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)

    def add(self, payload: dict) -> None:
        with self._lock:
            items = self._read_unlocked()
            if not any(item["request_id"] == payload["request_id"]
                       for item in items):
                items.append(payload)
                self._write_unlocked(items)

    def remove(self, request_id: str) -> None:
        with self._lock:
            items = self._read_unlocked()
            remaining = [item for item in items
                         if item["request_id"] != request_id]
            if remaining == items:
                return
            if remaining:
                self._write_unlocked(remaining)
            else:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass


class LocalBackendClient:
    """The default desktop data service; no Flask, port or network required."""

    is_local = True
    pending_saves_are_durable = True

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
        if store is None:
            selected = Path(db_path) if db_path is not None else default_database_path()
            if (legacy_db_path is None and db_path is None
                    and not os.environ.get("GAMES_DB")):
                legacy_db_path = (Path(__file__).resolve().parents[1]
                                  / "data" / "scores.db")
            try:
                store = LocalGameStore(selected, legacy_db_path=legacy_db_path)
            except sqlite3.DatabaseError as exc:
                message = str(exc).lower()
                corrupt = ("malformed" in message
                           or "not a database" in message)
                if corrupt and selected.is_file():
                    backup = selected.with_name(
                        f"{selected.name}.corrupt-{int(time.time())}")
                    try:
                        os.replace(selected, backup)
                        store = LocalGameStore(
                            selected, legacy_db_path=legacy_db_path)
                    except (OSError, sqlite3.Error):
                        store = None
                    else:
                        self.recovery_notice = (
                            f"损坏的成绩库已保留为 {backup.name}，已创建新库")
                else:
                    store = None
                if store is None:
                    self.initialization_error = "本机成绩库暂时不可用"
            except (OSError, StoreError):
                store = None
                self.initialization_error = "本机成绩目录暂时不可用"
        else:
            selected = store.db_path
        self.store = store
        self.outbox = PersistentSaveOutbox(
            Path(outbox_path) if outbox_path is not None
            else selected.with_name("pending_saves.json"))
        self._closed = False
        self._lock = threading.RLock()
        self.retry_failed_saves()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("LocalBackendClient is closed")

    def health(self) -> bool:
        if self.store is None:
            return False
        try:
            return self.store.health()
        except sqlite3.Error:
            return False

    def health_async(self) -> Future:
        return completed_future(self.health())

    def list_games(self) -> list[dict]:
        return public_games()

    def list_games_async(self) -> Future:
        return completed_future(self.list_games())

    def leaderboard(self, game_id: str, limit: int = 10) -> list[dict]:
        if self.store is None:
            return []
        try:
            return self.store.leaderboard(game_id, limit)
        except (StoreError, sqlite3.Error):
            return []

    def leaderboard_async(self, game_id: str, limit: int = 10) -> Future:
        return completed_future(self.leaderboard(game_id, limit))

    def recent(self, limit: int = 20) -> list[dict]:
        if self.store is None:
            return []
        try:
            return self.store.recent(limit)
        except (StoreError, sqlite3.Error):
            return []

    def recent_async(self, limit: int = 20) -> Future:
        return completed_future(self.recent(limit))

    def stats(self, game_id: str) -> dict:
        if self.store is None:
            return {"game_id": game_id, "attempts": 0, "records": 0,
                    "best": 0, "avg": 0}
        try:
            return self.store.stats(game_id)
        except (StoreError, sqlite3.Error):
            return {"game_id": game_id, "attempts": 0, "records": 0,
                    "best": 0, "avg": 0}

    def _save_payload(self, payload: dict) -> dict:
        self._ensure_open()
        outbox_error = None
        try:
            self.outbox.add(payload)
        except OSError as exc:
            outbox_error = exc
        try:
            if self.store is None:
                raise sqlite3.OperationalError("local store unavailable")
            result = self.store.record_score(**payload)
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
                self.outbox.remove(payload["request_id"])
            except OSError:
                # The attempt is already committed. A stale outbox replay is
                # harmless because request_id is idempotent.
                pass
            return result
        if outbox_error is not None:
            result["error"] += "，且无法写入待保存队列"
        result["durable_pending"] = (
            outbox_error is None and result.get("retryable", False))
        if not result.get("retryable", False):
            try:
                self.outbox.remove(payload["request_id"])
            except OSError:
                pass
        return result

    def submit_score(self, game_id: str, player: str, score: int,
                     extra=None, replace: bool = False,
                     submission_id: Optional[int] = None,
                     request_id: Optional[str] = None, **_ignored) -> dict:
        payload = {"game_id": game_id, "player": player, "score": score,
                   "extra": extra, "replace": replace,
                   "submission_id": submission_id,
                   "request_id": request_id or uuid.uuid4().hex}
        return self._save_payload(payload)

    def submit_score_async(self, game_id: str, player: str, score: int,
                           extra=None, replace: bool = False,
                           submission_id: Optional[int] = None,
                           request_id: Optional[str] = None) -> Future:
        return completed_future(self.submit_score(
            game_id, player, score, extra=extra, replace=replace,
            submission_id=submission_id, request_id=request_id))

    submit_score_reliable_async = submit_score_async

    def failed_save_count(self) -> int:
        return len(self.outbox.list())

    def retry_failed_saves(self) -> int:
        with self._lock:
            self._ensure_open()
            items = self.outbox.list()
            scheduled = 0
            for payload in items:
                result = self._save_payload(payload)
                if result.get("ok") is True:
                    scheduled += 1
                elif not result.get("retryable", False):
                    # Invalid data cannot become valid through repeated I/O.
                    try:
                        self.outbox.remove(payload["request_id"])
                    except OSError:
                        pass
            return scheduled

    def drain(self, timeout: Optional[float] = None) -> bool:
        del timeout
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
