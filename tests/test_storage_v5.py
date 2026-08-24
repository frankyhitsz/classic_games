"""Seventh-review upgrade, profile and local-state recovery checks."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from game_service.catalog import GAME_BY_ID
from game_service.local_backend import (LocalBackendClient,
                                        PersistentSaveOutbox,
                                        completed_future)
from game_service.mutation import canonical_json
from game_service.profile import ProfileIdentity
from game_service.service import SaveState
from game_service.store import LocalGameStore, SCHEMA_VERSION


class UpgradeSafetyTests(unittest.TestCase):
    def test_windows_request_lock_never_calls_os_kill_zero(self):
        fake_msvcrt = types.SimpleNamespace(
            LK_NBLCK=1, LK_UNLCK=2, locking=mock.Mock())
        with (mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
              mock.patch("game_service.local_backend.os.name", "nt"),
              mock.patch("game_service.local_backend.os.lseek"),
              mock.patch("game_service.local_backend.os.kill") as kill):
            self.assertTrue(PersistentSaveOutbox._try_lock_descriptor(8))
            PersistentSaveOutbox._unlock_descriptor(8)
        kill.assert_not_called()
        self.assertEqual(fake_msvcrt.locking.call_count, 2)

    def test_e99_spool_is_upgraded_without_losing_profile_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "pending"
            pending.mkdir()
            request_id = "e99-upgrade-request-000001"
            payload = {
                "game_id": "tetris", "player": "旧玩家", "score": 123,
                "extra": None, "replace": False, "submission_id": None,
                "request_id": request_id,
                "attempt_uuid": "e99-upgrade-attempt-000001",
                "revision": 1, "profile_id": "旧玩家", "mode": "classic",
                "ruleset_version": GAME_BY_ID["tetris"].ruleset_version,
                "status": "completed",
            }
            semantic = {key: value for key, value in payload.items()
                        if key != "request_id"}
            import hashlib
            legacy_hash = hashlib.sha256(
                canonical_json(semantic).encode("utf-8")).hexdigest()
            envelope = {
                "schema_version": 1, "request_id": request_id,
                "payload_hash": legacy_hash,
                "attempt_uuid": payload["attempt_uuid"], "revision": 1,
                "created_at": time.time(), "attempt_count": 0,
                "payload": payload,
            }
            target = pending / f"{request_id}.json"
            target.write_text(canonical_json(envelope), encoding="utf-8")

            outbox = PersistentSaveOutbox(pending)
            recovered = outbox.list()
            self.assertEqual(recovered[0]["score"], 123)
            self.assertEqual(
                recovered[0]["profile_id"],
                ProfileIdentity.from_legacy_name("旧玩家").profile_id)
            upgraded = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(upgraded["schema_version"], 2)
            self.assertNotEqual(upgraded["payload_hash"], legacy_hash)
            self.assertTrue(any(outbox.migration_backup_path.iterdir()))

    @staticmethod
    def _make_v2_database(path: Path, *, with_attempt: bool = False) -> None:
        connection = sqlite3.connect(path)
        connection.executescript("""
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES('version', '2');
            CREATE TABLE settings(
                key TEXT PRIMARY KEY, value_json TEXT NOT NULL,
                updated_at REAL NOT NULL);
            CREATE TABLE progress(
                profile_id TEXT NOT NULL, game_id TEXT NOT NULL,
                ruleset_version TEXT NOT NULL, progress_json TEXT NOT NULL,
                updated_at REAL NOT NULL);
            CREATE TABLE save_slots(
                profile_id TEXT NOT NULL, game_id TEXT NOT NULL,
                slot TEXT NOT NULL, ruleset_version TEXT NOT NULL,
                state_json TEXT NOT NULL, updated_at REAL NOT NULL);
        """)
        connection.execute(
            "INSERT INTO settings VALUES('volume', ?, 10)",
            (json.dumps(0.4),))
        connection.execute(
            "INSERT INTO progress VALUES(?,?,?,?,10)",
            ("old-player", "sokoban", "sokoban-classic-1",
             json.dumps({"unlocked_level": 4})))
        connection.execute(
            "INSERT INTO save_slots VALUES(?,?,?,?,?,10)",
            ("old-player", "2048", "autosave", "2048-classic-1",
             json.dumps({"version": 1, "score": 64})))
        if with_attempt:
            connection.executescript("""
                CREATE TABLE attempts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    game_id TEXT NOT NULL, player TEXT NOT NULL,
                    score INTEGER NOT NULL, extra_json TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                INSERT INTO attempts(request_id,game_id,player,score,extra_json,
                    created_at,updated_at) VALUES(
                    'v2-attempt-request-0001','snake','old-player',9,NULL,10,10);
            """)
        connection.commit()
        connection.close()

    def test_schema_v2_state_tables_upgrade_transactionally_and_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "v2.db"
            self._make_v2_database(database)
            store = LocalGameStore(database)
            self.assertEqual(store.schema_version(), SCHEMA_VERSION)
            self.assertIsNotNone(store.migration_backup)
            old_profile = ProfileIdentity.from_legacy_name("old-player").profile_id
            guest = ProfileIdentity.from_legacy_name("guest").profile_id
            self.assertEqual(store.get_setting(guest, "volume"), 0.4)
            self.assertEqual(
                store.get_progress(
                    old_profile, "sokoban", "campaign", ruleset_version=
                    "sokoban-classic-1")["unlocked_level"], 4)
            self.assertEqual(
                store.load_slot(old_profile, "2048", "autosave")["state"]["score"],
                64)
            with store.connection() as connection:
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            second = LocalGameStore(database)
            self.assertIsNone(second.migration_backup)

    def test_schema_v2_with_attempt_migrates_profile_and_score(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "v2-with-attempt.db"
            self._make_v2_database(database, with_attempt=True)
            store = LocalGameStore(database)
            profile_id = ProfileIdentity.from_legacy_name("old-player").profile_id
            self.assertEqual(store.attempt_count("snake"), 1)
            self.assertEqual(store.list_profiles()[0]["profile_id"], profile_id)
            self.assertEqual(store.leaderboard("snake")[0]["score"], 9)


class ProfileAndStateTests(unittest.TestCase):
    def test_schema_repair_pending_uses_recovery_state_and_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            error = sqlite3.OperationalError("schema changed")
            error.sqlite_errorcode = sqlite3.SQLITE_SCHEMA
            with mock.patch.object(
                    backend.store, "record_mutation", side_effect=error):
                result = backend.submit_score(
                    "snake", "p", 3,
                    request_id="schema-repair-request-0001",
                    attempt_uuid="schema-repair-attempt-0001")
                event = backend.get_save_status(
                    "schema-repair-request-0001")
                self.assertEqual(event.state, SaveState.RECOVERY_REQUIRED)
                self.assertTrue(result["durable_pending"])
                backend.retry_failed_saves().result(timeout=5)
                backend.drain(5)
                self.assertGreater(
                    backend._next_auto_retry_at, time.monotonic() + 30)
            backend.close()

    def test_pending_status_can_be_reconstructed_after_cache_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            blocker = sqlite3.connect(root / "games.db")
            blocker.execute("BEGIN IMMEDIATE")
            request_id = "status-rebuild-request-0001"
            backend.submit_score(
                "tetris", "p", 5, request_id=request_id,
                attempt_uuid="status-rebuild-attempt-0001")
            backend._save_status.clear()
            event = backend.get_save_status(request_id)
            self.assertIsNotNone(event)
            self.assertEqual(event.state, SaveState.DURABLE_PENDING)
            self.assertTrue(event.result["reconstructed"])
            blocker.rollback()
            blocker.close()
            backend.close()

    def test_standalone_score_creates_profile_and_rename_updates_board(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            profile_id = "1234567890abcdef1234567890abcdef"
            store.record_score(
                "snake", "旧名", 12, profile_id=profile_id,
                request_id="profile-create-request-0001")
            self.assertEqual(store.list_profiles()[0]["display_name"], "旧名")
            store.ensure_profile("新名", profile_id)
            self.assertEqual(store.leaderboard("snake")[0]["player"], "新名")

    def test_foreign_keys_reject_orphan_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            with self.assertRaisesRegex(Exception, "profile does not exist"):
                store.set_setting(
                    "1234567890abcdef1234567890abcdef", "volume", 1)

    def test_progress_merge_is_monotonic_and_ruleset_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            profile_id = store.ensure_profile("p")["profile_id"]
            store.merge_progress(
                profile_id, "sokoban", "campaign",
                {"unlocked_level": 8, "completed_levels": [0, 2],
                 "level_scores": {"0": 900}})
            store.merge_progress(
                profile_id, "sokoban", "campaign",
                {"unlocked_level": 2, "completed_levels": [1],
                 "level_scores": {"0": 100}})
            value = store.get_progress(
                profile_id, "sokoban", "campaign")
            self.assertEqual(value["unlocked_level"], 8)
            self.assertEqual(value["completed_levels"], [0, 1, 2])
            self.assertEqual(value["level_scores"]["0"], 900)
            self.assertIsNone(store.get_progress(
                profile_id, "sokoban", "campaign", ruleset_version="future-2"))

    def test_corrupt_slot_is_quarantined_and_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            profile_id = store.ensure_profile("p")["profile_id"]
            store.save_slot(profile_id, "2048", "autosave", {"version": 1})
            with store.connection() as connection:
                connection.execute(
                    "UPDATE save_slots SET state_json='{' WHERE profile_id=?",
                    (profile_id,))
                connection.commit()
            self.assertIsNone(store.load_slot(profile_id, "2048", "autosave"))
            with store.connection() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM invalid_local_state").fetchone()[0]
            self.assertEqual(count, 1)

    def test_progress_write_survives_database_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile = backend.ensure_profile_async("p").result(timeout=5)
            blocker = sqlite3.connect(root / "games.db")
            blocker.execute("BEGIN IMMEDIATE")
            result = backend.merge_progress_async(
                profile["profile_id"], "zuma", "campaign",
                {"unlocked_level": 3}).result(timeout=5)
            self.assertFalse(result["ok"])
            self.assertTrue(result["durable_pending"])
            blocker.rollback()
            blocker.close()
            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            self.assertEqual(backend.store.get_progress(
                profile["profile_id"], "zuma", "campaign")["unlocked_level"], 3)
            backend.close()

    def test_keyed_state_journal_replays_only_latest_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile = backend.ensure_profile_async("p").result(timeout=5)
            blocker = sqlite3.connect(root / "games.db")
            blocker.execute("BEGIN IMMEDIATE")
            first = backend.save_slot_async(
                profile["profile_id"], "2048", "autosave",
                {"version": 1, "score": 4}).result(timeout=5)
            second = backend.save_slot_async(
                profile["profile_id"], "2048", "autosave",
                {"version": 1, "score": 8}).result(timeout=5)
            self.assertTrue(first["durable_pending"])
            self.assertTrue(second["durable_pending"])
            self.assertEqual(backend.failed_save_count(), 1)
            blocker.rollback()
            blocker.close()
            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            self.assertEqual(backend.store.load_slot(
                profile["profile_id"], "2048", "autosave")["state"]["score"],
                8)
            backend.close()

    def test_state_replay_creates_profile_before_dependent_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            blocker = sqlite3.connect(root / "games.db")
            blocker.execute("BEGIN IMMEDIATE")
            profile_id = "c" * 32
            profile_result = backend.ensure_profile_async(
                "p", profile_id).result(timeout=5)
            slot_result = backend.save_slot_async(
                profile_id, "2048", "autosave",
                {"version": 1, "score": 12}).result(timeout=5)
            self.assertTrue(profile_result["durable_pending"])
            self.assertTrue(slot_result["durable_pending"])
            blocker.rollback()
            blocker.close()
            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            self.assertEqual(
                backend.store.load_slot(
                    profile_id, "2048", "autosave")["state"]["score"], 12)
            backend.close()

    def test_clock_rollback_is_disclosed_instead_of_rejecting_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            mutation_time = time.time() + 86400
            result = store.record_score(
                "tetris", "p", 1,
                request_id="clock-rollback-request-0001",
                attempt_uuid="clock-rollback-attempt-0001")
            self.assertFalse(result["clock_adjusted"])
            from game_service.mutation import normalize_score_mutation
            mutation = normalize_score_mutation(
                "snake", "p", 2,
                request_id="clock-rollback-request-0002",
                attempt_uuid="clock-rollback-attempt-0002")
            adjusted = store.record_mutation(mutation, occurred_at=mutation_time)
            self.assertTrue(adjusted["clock_adjusted"])


class Game2048SlotTests(unittest.TestCase):
    class Backend:
        is_local = True
        pending_saves_are_durable = True

        def __init__(self, saved=None, pending=False):
            self.load = Future()
            if not pending:
                self.load.set_result(saved)
            self.saved = []

        def load_slot_async(self, *_args):
            return self.load

        def save_slot_async(self, *_args):
            self.saved.append(_args[-1])
            return completed_future(None)

        def failed_save_count(self):
            return 0

    def setUp(self):
        import pygame
        pygame.init()

    def tearDown(self):
        import pygame
        pygame.display.quit()

    def test_input_is_blocked_until_slot_load_finishes(self):
        import pygame
        from client.games.game_2048 import Game2048
        backend = self.Backend(pending=True)
        game = Game2048(backend=backend, profile_id="a" * 32)
        before = [[game.grid[r][c].value if game.grid[r][c] else 0
                   for c in range(4)] for r in range(4)]
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_LEFT))
        after = [[game.grid[r][c].value if game.grid[r][c] else 0
                  for c in range(4)] for r in range(4)]
        self.assertEqual(before, after)
        self.assertEqual(game.slot_load_state, "loading")

    def test_slot_load_timeout_stays_gated_and_can_retry(self):
        from client.games.game_2048 import Game2048
        backend = self.Backend(pending=True)
        with mock.patch(
                "client.games.game_2048.pygame.time.get_ticks",
                side_effect=[0, 9000]):
            game = Game2048(backend=backend, profile_id="f" * 32)
            game._poll_slot_load()
        self.assertEqual(game.slot_load_state, "failed")
        self.assertIn("超时", game.slot_load_error)

    def test_v2_slot_restores_attempt_and_terminal_slot_does_not_resume(self):
        from client.games.game_2048 import Game2048
        profile_id = "b" * 32
        ruleset = GAME_BY_ID["2048"].ruleset_version
        state = {
            "version": 2, "game_state": "playing", "score": 16,
            "won": False, "won_announced": False,
            "attempt_uuid": "restored-attempt-00000001", "revision": 3,
            "submission_id": 7, "confirmed_score": 16,
            "grid": [[2, 4, 0, 0], [0, 0, 0, 0],
                     [0, 0, 0, 0], [0, 0, 0, 0]],
        }
        game = Game2048(
            backend=self.Backend({"state": state,
                                  "ruleset_version": ruleset}),
            profile_id=profile_id)
        game._poll_slot_load()
        self.assertEqual(game._score_attempt_uuid, state["attempt_uuid"])
        self.assertEqual(game.attempt_context.revision, 3)
        self.assertIsNone(game.score_submission_id)
        self.assertIsNone(game._score_submission_id)

        terminal = {**state, "game_state": "gameover"}
        terminal_game = Game2048(
            backend=self.Backend({"state": terminal,
                                  "ruleset_version": ruleset}),
            profile_id=profile_id)
        terminal_game._poll_slot_load()
        self.assertEqual(terminal_game.state, "playing")
        self.assertNotEqual(terminal_game.score, 16)


if __name__ == "__main__":
    unittest.main()
