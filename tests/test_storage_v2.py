"""Focused checks for local storage and lifecycle boundaries."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from game_service.local_backend import (LocalBackendClient,
                                        PersistentSaveOutbox)
from game_service.catalog import GAMES
from game_service.mutation import MAX_SQLITE_INTEGER
from game_service.store import (LEGACY_RULESET_VERSION, LocalGameStore,
                                StoreError)


class LocalAsyncTests(unittest.TestCase):
    def test_integrated_spool_conflict_never_reaches_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            request_id = "integrated-conflict-request-001"
            original = backend.outbox.add({
                "game_id": "snake", "player": "p", "score": 10,
                "request_id": request_id,
            })
            result = backend.submit_score(
                "snake", "p", 20, request_id=request_id)
            self.assertEqual(result["code"], "request_id_conflict")
            self.assertFalse(result["retryable"])
            self.assertEqual(result["existing_payload_hash"],
                             original.payload_hash)
            self.assertNotEqual(result["new_payload_hash"],
                                original.payload_hash)
            self.assertEqual(backend.store.attempt_count(), 0)
            self.assertEqual(backend.outbox.list()[0]["score"], 10)
            backend.close()

    def test_current_schema_startup_does_not_wait_for_write_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            initial.close()
            blocker = sqlite3.connect(root / "games.db")
            blocker.execute("BEGIN IMMEDIATE")
            started = time.perf_counter()
            reopened = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertLess(elapsed_ms, 100.0)
            blocker.rollback()
            blocker.close()
            reopened.close()

    def test_submit_returns_before_locked_database_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            lock = sqlite3.connect(root / "games.db")
            lock.execute("BEGIN IMMEDIATE")
            started = time.perf_counter()
            future = backend.submit_score_async(
                "2048", "player", 100,
                request_id="locked-request-0000000001",
                attempt_uuid="locked-attempt-0000000001", revision=1)
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertLess(elapsed_ms, 2.0)
            self.assertFalse(future.done())
            result = future.result(timeout=5)
            self.assertTrue(result["durable_pending"])
            self.assertEqual(backend.failed_save_count(), 1)
            lock.rollback()
            lock.close()
            self.assertEqual(
                backend.retry_failed_saves().result(timeout=5), 1)
            self.assertTrue(backend.drain(5))
            self.assertEqual(backend.failed_save_count(), 0)
            backend.close()

    def test_invalid_extra_is_a_stable_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            future = backend.submit_score_async(
                "tetris", "p", 1, extra={"bad": {1, 2}},
                request_id="invalid-extra-request-001")
            result = future.result(timeout=5)
            self.assertEqual(result["code"], "invalid_extra")
            self.assertFalse(result["retryable"])
            self.assertEqual(backend.failed_save_count(), 0)
            backend.close()

    def test_close_finishes_or_spools_every_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            futures = [backend.submit_score_async(
                "snake", f"p{index}", index,
                request_id=f"close-request-{index:016d}")
                for index in range(40)]
            backend.close()
            self.assertTrue(all(future.done() for future in futures))
            reopened = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            self.assertTrue(reopened.drain(5))
            self.assertEqual(reopened.store.attempt_count("snake"), 40)
            reopened.close()

    def test_forced_process_exit_after_durable_result_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            blocker = sqlite3.connect(database)
            blocker.execute("BEGIN IMMEDIATE")
            code = """
import os, sys
from game_service.local_backend import LocalBackendClient
backend = LocalBackendClient(db_path=sys.argv[1], outbox_path=sys.argv[2])
result = backend.submit_score_async(
    'zuma', 'crash', 88,
    request_id='forced-exit-request-000001',
    attempt_uuid='forced-exit-attempt-000001', revision=1).result(timeout=5)
os._exit(0 if result.get('durable_pending') else 7)
"""
            process = subprocess.run(
                [sys.executable, "-c", code, str(database),
                 str(root / "pending")], timeout=10)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(len(list((root / "pending").glob("*.json"))), 1)
            blocker.rollback()
            blocker.close()
            recovered = LocalBackendClient(
                db_path=database, outbox_path=root / "pending")
            self.assertTrue(recovered.drain(5))
            self.assertEqual(recovered.store.attempt_count("zuma"), 1)
            recovered.close()


class SpoolTests(unittest.TestCase):
    def test_no_hardlink_fallback_publishes_complete_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spool = PersistentSaveOutbox(root / "pending")
            with mock.patch("game_service.local_backend.os.link",
                            side_effect=OSError("unsupported")):
                envelope = spool.add({
                    "game_id": "tetris", "player": "p", "score": 7,
                    "request_id": "fallback-request-000000001",
                })
            target = root / "pending" / f"{envelope.request_id}.json"
            self.assertTrue(target.is_file())
            self.assertEqual(spool.list()[0]["score"], 7)
            self.assertEqual(list((root / "pending").glob("*.tmp")), [])

    def test_misnamed_valid_file_is_restored_to_canonical_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spool = PersistentSaveOutbox(root / "pending")
            envelope = spool.add({
                "game_id": "zuma", "player": "p", "score": 8,
                "request_id": "canonical-request-00000001",
            })
            canonical = root / "pending" / f"{envelope.request_id}.json"
            wrong = root / "pending" / "wrong-name.json"
            os.replace(canonical, wrong)
            self.assertEqual(spool.list()[0]["score"], 8)
            self.assertTrue(canonical.is_file())
            self.assertFalse(wrong.exists())

    def test_oversized_file_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "pending"
            pending.mkdir()
            (pending / "oversized.json").write_bytes(b"{" + b"x" * 70000)
            spool = PersistentSaveOutbox(pending)
            self.assertEqual(spool.list(), [])
            self.assertEqual(spool.quarantined_count, 1)
            self.assertTrue(list((root / "pending-quarantine").iterdir()))

    def test_bad_item_is_quarantined_without_blocking_good_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spool = PersistentSaveOutbox(root / "pending")
            spool.add({
                "game_id": "zuma", "player": "p", "score": 55,
                "request_id": "good-spool-request-00001",
            })
            bad = root / "pending" / "bad-record.json"
            bad.write_text(json.dumps({
                "schema_version": 1, "request_id": "bad", "bogus": 1,
            }), encoding="utf-8")
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            self.assertTrue(backend.drain(5))
            self.assertEqual(backend.store.attempt_count("zuma"), 1)
            self.assertIn("已隔离 1 条", backend.recovery_notice)
            self.assertTrue(list((root / "pending-quarantine").iterdir()))
            backend.close()

    def test_legacy_invalid_root_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "pending_saves.json"
            legacy.write_text('{"not":"a-list"}', encoding="utf-8")
            spool = PersistentSaveOutbox(root / "pending", legacy_path=legacy)
            self.assertEqual(spool.list(), [])
            self.assertEqual(spool.quarantined_count, 1)
            self.assertTrue(list((root / "pending-quarantine").iterdir()))

    def test_request_id_payload_conflict_is_not_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = PersistentSaveOutbox(Path(directory) / "pending")
            request_id = "spool-conflict-request-001"
            spool.add({"game_id": "snake", "player": "p", "score": 1,
                       "request_id": request_id})
            with self.assertRaises(StoreError) as raised:
                spool.add({"game_id": "snake", "player": "p", "score": 2,
                           "request_id": request_id})
            self.assertEqual(raised.exception.code, "request_id_conflict")
            self.assertEqual(len(spool.list()), 1)

    def test_nonfinite_envelope_metadata_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spool = PersistentSaveOutbox(root / "pending")
            spool.add({"game_id": "snake", "player": "p", "score": 1,
                       "request_id": "nonfinite-envelope-request1"})
            path = next((root / "pending").glob("*.json"))
            value = json.loads(path.read_text(encoding="utf-8"))
            value["created_at"] = float("nan")
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(spool.list(), [])
            self.assertEqual(spool.quarantined_count, 1)

    def test_32_processes_do_not_lose_spool_files(self):
        with tempfile.TemporaryDirectory() as directory:
            spool_path = Path(directory) / "pending"
            code = (
                "import sys\n"
                "from pathlib import Path\n"
                "from game_service.local_backend import PersistentSaveOutbox\n"
                "i=int(sys.argv[2])\n"
                "PersistentSaveOutbox(Path(sys.argv[1])).add({"
                "'game_id':'tetris','player':f'p{i}','score':i,"
                "'request_id':f'multiprocess-request-{i:08d}'})\n"
            )
            processes = [subprocess.Popen(
                [sys.executable, "-c", code, str(spool_path), str(index)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for index in range(32)]
            failures = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                if process.returncode:
                    failures.append((process.returncode, stdout, stderr))
            self.assertEqual(failures, [])
            self.assertEqual(len(PersistentSaveOutbox(spool_path).list()), 32)


class AttemptModelTests(unittest.TestCase):
    def test_expired_receipt_replays_derived_attempt_without_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            request_id = "expired-receipt-request-0001"
            first = store.record_score(
                "snake", "p", 12, request_id=request_id)
            with store.connection() as connection:
                connection.execute(
                    "DELETE FROM save_requests WHERE request_id=?", (request_id,))
                connection.commit()
            replay = store.record_score(
                "snake", "p", 12, request_id=request_id)
            self.assertEqual(replay["id"], first["id"])
            self.assertTrue(replay["no_op"])
            self.assertEqual(store.attempt_count(), 1)

    def test_corrupt_receipt_is_rebuilt_from_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            request_id = "corrupt-receipt-request-0001"
            first = store.record_score(
                "snake", "p", 13, request_id=request_id)
            with store.connection() as connection:
                connection.execute(
                    "UPDATE save_requests SET response_json='{' "
                    "WHERE request_id=?", (request_id,))
                connection.commit()
            replay = store.record_score(
                "snake", "p", 13, request_id=request_id)
            self.assertEqual(replay["id"], first["id"])
            self.assertEqual(store.attempt_count(), 1)
            with store.connection() as connection:
                value = connection.execute(
                    "SELECT response_json FROM save_requests WHERE request_id=?",
                    (request_id,)).fetchone()[0]
            self.assertTrue(json.loads(value)["ok"])

    def test_revision_policy_rejects_mixed_score_metadata_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            attempt = "monotonic-policy-attempt-00001"
            store.record_score(
                "2048", "p", 100, extra={"tile": 16}, attempt_uuid=attempt,
                revision=1, request_id="policy-origin-request-00001")
            with self.assertRaises(StoreError) as lower:
                store.record_score(
                    "2048", "p", 90, extra={"tile": 32},
                    attempt_uuid=attempt, revision=2,
                    request_id="policy-lower-request-000001")
            self.assertEqual(lower.exception.code, "score_regression")
            with self.assertRaises(StoreError) as status:
                store.record_score(
                    "2048", "p", 110, status="practice",
                    attempt_uuid=attempt, revision=2,
                    request_id="policy-status-request-00001")
            self.assertEqual(status.exception.code, "attempt_status_conflict")
            with self.assertRaises(StoreError) as final_only:
                final_attempt = "final-policy-attempt-0000001"
                store.record_score(
                    "snake", "p", 5, attempt_uuid=final_attempt, revision=1,
                    request_id="final-policy-origin-0000001")
                store.record_score(
                    "snake", "p", 6, attempt_uuid=final_attempt, revision=2,
                    request_id="final-policy-update-0000001")
            self.assertEqual(final_only.exception.code, "attempt_finalized")

    def test_empty_transport_ids_and_oversized_integers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            for field, kwargs, code in (
                ("request", {"request_id": ""}, "invalid_request_id"),
                ("attempt", {"attempt_uuid": ""}, "invalid_attempt_uuid"),
                ("revision", {"revision": MAX_SQLITE_INTEGER + 1},
                 "invalid_revision"),
                ("submission", {"submission_id": MAX_SQLITE_INTEGER + 1},
                 "invalid_submission_id"),
            ):
                with self.subTest(field=field), self.assertRaises(StoreError) as raised:
                    store.record_score("tetris", "p", 1, **kwargs)
                self.assertEqual(raised.exception.code, code)

    def test_pending_milestone_final_and_replay_are_one_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            attempt = "same-run-attempt-000000001"
            lock = sqlite3.connect(root / "games.db")
            lock.execute("BEGIN IMMEDIATE")
            first = backend.submit_score_async(
                "2048", "p", 100, request_id="milestone-request-00000001",
                attempt_uuid=attempt, revision=1).result(timeout=5)
            self.assertTrue(first["durable_pending"])
            lock.rollback()
            lock.close()
            final = backend.submit_score_async(
                "2048", "p", 200, request_id="final-request-00000000001",
                attempt_uuid=attempt, revision=2).result(timeout=5)
            self.assertTrue(final["ok"])
            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            self.assertEqual(backend.store.attempt_count("2048"), 1)
            recent = backend.store.recent()
            self.assertEqual(recent[0]["score"], 200)
            backend.close()

    def test_stale_and_mismatched_submission_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            first = store.record_score(
                "snake", "Alice", 10,
                request_id="submission-origin-request-01")
            with self.assertRaises(StoreError) as missing:
                store.record_score(
                    "snake", "Alice", 20, submission_id=999999,
                    request_id="submission-missing-request-1")
            self.assertEqual((missing.exception.code, missing.exception.status),
                             ("submission_not_found", 404))
            with self.assertRaises(StoreError) as mismatch:
                store.record_score(
                    "snake", "Bob", 20, submission_id=first["id"],
                    request_id="submission-mismatch-request1")
            self.assertEqual((mismatch.exception.code, mismatch.exception.status),
                             ("submission_mismatch", 409))

    def test_rank_and_tie_time_use_the_personal_best_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            with mock.patch("game_service.store.time.time",
                            side_effect=[1.0, 2.0, 3.0, 4.0]):
                store.record_score(
                    "tetris", "Alice", 10,
                    request_id="tie-alice-low-request-001")
                store.record_score(
                    "tetris", "Bob", 100,
                    request_id="tie-bob-high-request-0001")
                best = store.record_score(
                    "tetris", "Alice", 100,
                    request_id="tie-alice-high-request-01")
                lower = store.record_score(
                    "tetris", "Alice", 5,
                    request_id="tie-alice-new-low-request1")
            board = store.leaderboard("tetris")
            alice = next(row for row in board if row["player"] == "Alice")
            bob = next(row for row in board if row["player"] == "Bob")
            self.assertEqual((alice["rank"], bob["rank"]), (1, 1))
            self.assertEqual((alice["ts"], bob["ts"]), (3.0, 2.0))
            self.assertEqual(best["personal_best_rank"], 1)
            self.assertEqual(lower["personal_best_rank"], 1)

    def test_mode_ruleset_and_status_are_separate_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            store.record_score(
                "snake", "p", 10, request_id="classic-mode-request-00001")
            store.record_score(
                "snake", "p", 99, mode="comfort",
                request_id="comfort-mode-request-00001")
            store.record_score(
                "snake", "p", 999, status="practice",
                request_id="practice-mode-request-0001")
            self.assertEqual(store.leaderboard("snake")[0]["score"], 10)
            self.assertEqual(
                store.leaderboard("snake", mode="comfort")[0]["score"], 99)
            self.assertEqual(store.stats("snake")["attempts"], 1)

    def test_extra_only_revision_keeps_best_score_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            attempt = "score-time-attempt-000000001"
            with mock.patch("game_service.store.time.time",
                            side_effect=[10.0, 20.0, 30.0]):
                store.record_score(
                    "2048", "p", 100, extra={"phase": 1},
                    attempt_uuid=attempt, revision=1,
                    request_id="score-time-request-0000001")
                store.record_score(
                    "2048", "p", 100, extra={"phase": 2},
                    attempt_uuid=attempt, revision=2,
                    request_id="score-time-request-0000002")
                self.assertEqual(store.leaderboard("2048")[0]["ts"], 10.0)
                store.record_score(
                    "2048", "p", 200, extra={"phase": 3},
                    attempt_uuid=attempt, revision=3,
                    request_id="score-time-request-0000003")
            self.assertEqual(store.leaderboard("2048")[0]["ts"], 30.0)


class RecoveryAndBoundaryTests(unittest.TestCase):
    def test_2048_same_score_final_metadata_is_not_dropped(self):
        import pygame
        from client.games.game_2048 import Game2048

        pygame.init()

        class Backend:
            def __init__(self):
                self.calls = []
                self.futures = [Future(), Future()]

            def submit_score_reliable_async(self, *_args, **kwargs):
                self.calls.append(kwargs)
                return self.futures[len(self.calls) - 1]

            def leaderboard_async(self, *_args, **_kwargs):
                future = Future()
                future.set_result([])
                return future

        backend = Backend()
        game = Game2048(backend=backend)
        game.score = 100
        game._submit_score(extra={"won": True})
        game._submit_score(extra={"won": True, "final": True})
        backend.futures[0].set_result({"ok": True, "id": 1, "score": 100})
        game._poll_score_submission()
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(backend.calls[1]["extra"],
                         {"won": True, "final": True})
        backend.futures[1].set_result({"ok": True, "id": 1, "score": 100})
        game._poll_score_submission()
        self.assertEqual(game.submitted_extra,
                         {"won": True, "final": True})
        pygame.quit()

    def test_failed_schema_migration_rolls_back_schema_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "v1.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
                connection.execute(
                    "INSERT INTO schema_meta VALUES ('version','1')")
                connection.execute(
                    "CREATE TABLE attempts (id INTEGER PRIMARY KEY, "
                    "request_id TEXT NOT NULL UNIQUE, game_id TEXT NOT NULL, "
                    "player TEXT NOT NULL, score INTEGER NOT NULL, "
                    "extra_json TEXT, created_at REAL NOT NULL, "
                    "updated_at REAL NOT NULL)")
                connection.execute(
                    "CREATE TABLE save_requests (request_id TEXT PRIMARY KEY, "
                    "payload_hash TEXT NOT NULL, response_json TEXT NOT NULL, "
                    "created_at REAL NOT NULL)")

            class FailingMigration(LocalGameStore):
                def _migrate_attempt_rows(self, connection):
                    raise RuntimeError("injected migration failure")

            with self.assertRaisesRegex(RuntimeError, "injected"):
                FailingMigration(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                columns = {row[1] for row in
                           connection.execute("PRAGMA table_info(attempts)")}
                version = connection.execute(
                    "SELECT value FROM schema_meta WHERE key='version'").fetchone()[0]
            self.assertNotIn("attempt_uuid", columns)
            self.assertEqual(version, "1")

    def test_incompatible_external_legacy_database_does_not_disable_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.db"
            with closing(sqlite3.connect(legacy)) as connection, connection:
                connection.execute(
                    "CREATE TABLE scores (id INTEGER PRIMARY KEY, game_id TEXT)")
            store = LocalGameStore(root / "games.db", legacy_db_path=legacy)
            self.assertTrue(store.health())
            self.assertEqual(store.attempt_count(), 0)
            self.assertIn("缺少字段", store.migration_notice)

    def test_corrupt_legacy_metadata_recovers_base_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.db"
            with closing(sqlite3.connect(legacy)) as connection, connection:
                connection.execute(
                    "CREATE TABLE scores (id INTEGER PRIMARY KEY, game_id TEXT, "
                    "player TEXT, score, extra TEXT, created_at REAL)")
                connection.execute(
                    "INSERT INTO scores VALUES (1,'zuma','ok',50,NULL,1.0)")
                connection.execute(
                    "INSERT INTO scores VALUES (2,'zuma','bad',60,'{',2.0)")
            store = LocalGameStore(root / "games.db", legacy_db_path=legacy)
            self.assertEqual(store.attempt_count("zuma"), 2)
            self.assertIn("成绩已恢复", store.migration_notice)

    def test_python_repr_legacy_extra_is_imported_under_legacy_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.db"
            with closing(sqlite3.connect(legacy)) as connection, connection:
                connection.execute(
                    "CREATE TABLE scores (id INTEGER PRIMARY KEY, game_id TEXT, "
                    "player TEXT, score, extra TEXT, created_at REAL)")
                connection.execute(
                    "INSERT INTO scores VALUES "
                    "(1,'tetris','old',88,\"{'lines': 4, 'level': 2}\",1.0)")
            store = LocalGameStore(root / "games.db", legacy_db_path=legacy)
            self.assertEqual(store.leaderboard("tetris"), [])
            legacy_board = store.leaderboard(
                "tetris", ruleset_version=LEGACY_RULESET_VERSION)
            self.assertEqual(legacy_board[0]["score"], 88)
            with store.connection() as connection:
                extra = connection.execute(
                    "SELECT extra_json FROM attempts").fetchone()[0]
            self.assertEqual(json.loads(extra), {"lines": 4, "level": 2})

    def test_current_ruleset_versions_are_explicit(self):
        self.assertEqual(
            {game.id: game.ruleset_version for game in GAMES},
            {"tetris": "tetris-assist-2", "snake": "snake-classic-1",
             "2048": "2048-classic-2",
             "sokoban": "sokoban-campaign-2",
             "zuma": "zuma-classic-2"})

    def test_same_version_missing_index_is_backed_up_and_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("DROP INDEX idx_attempts_best")
            repaired = LocalGameStore(database)
            self.assertIsNotNone(repaired.migration_backup)
            self.assertTrue(repaired.migration_backup.is_file())
            with repaired.connection() as connection:
                indexes = {row["name"] for row in
                           connection.execute("PRAGMA index_list(attempts)")}
            self.assertIn("idx_attempts_best", indexes)

    def test_changed_external_legacy_source_is_reimported_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.db"
            with closing(sqlite3.connect(legacy)) as connection, connection:
                connection.execute(
                    "CREATE TABLE scores (id INTEGER PRIMARY KEY, game_id TEXT, "
                    "player TEXT, score, extra TEXT, created_at REAL)")
                connection.execute(
                    "INSERT INTO scores VALUES (1,'zuma','old',10,NULL,1.0)")
            database = root / "games.db"
            LocalGameStore(database, legacy_db_path=legacy)
            with closing(sqlite3.connect(legacy)) as connection, connection:
                connection.execute(
                    "INSERT INTO scores VALUES (2,'zuma','new',20,NULL,2.0)")
            reopened = LocalGameStore(database, legacy_db_path=legacy)
            self.assertEqual(reopened.attempt_count("zuma"), 2)
            with reopened.connection() as connection:
                marker = connection.execute(
                    "SELECT value FROM schema_meta "
                    "WHERE key LIKE 'legacy_scores_v4_%' "
                    "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            report = json.loads(marker)
            self.assertEqual((report["valid"], report["skipped"],
                              report["imported"]), (2, 0, 1))

    def test_different_legacy_sources_with_same_row_id_do_not_collide(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [root / "first" / "legacy.db",
                       root / "second" / "legacy.db"]
            for index, source in enumerate(sources, start=1):
                source.parent.mkdir()
                with closing(sqlite3.connect(source)) as connection, connection:
                    connection.execute(
                        "CREATE TABLE scores (id INTEGER PRIMARY KEY, "
                        "game_id TEXT, player TEXT, score, extra TEXT, "
                        "created_at REAL)")
                    connection.execute(
                        "INSERT INTO scores VALUES (1,'snake',?,?,NULL,?)",
                        (f"p{index}", index * 10, float(index)))
            database = root / "games.db"
            LocalGameStore(database, legacy_db_path=sources[0])
            store = LocalGameStore(database, legacy_db_path=sources[1])
            self.assertEqual(store.attempt_count("snake"), 2)
            board = store.leaderboard(
                "snake", ruleset_version=LEGACY_RULESET_VERSION)
            self.assertEqual({row["score"] for row in board}, {10, 20})

    def test_same_version_missing_receipt_unique_index_is_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "DROP INDEX idx_save_requests_request_id")
            repaired = LocalGameStore(database)
            self.assertIsNotNone(repaired.migration_backup)
            with repaired.connection() as connection:
                indexes = {row["name"] for row in
                           connection.execute("PRAGMA index_list(save_requests)")}
            self.assertIn("idx_save_requests_request_id", indexes)

    def test_transient_initialization_lock_self_heals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            store = LocalGameStore(database)
            with store.connection() as connection:
                connection.execute(
                    "UPDATE schema_meta SET value='2' WHERE key='version'")
                connection.commit()
            blocker = sqlite3.connect(database)
            blocker.execute("BEGIN IMMEDIATE")
            backend = LocalBackendClient(
                db_path=database, outbox_path=root / "pending")
            self.assertIsNone(backend.store)
            blocker.rollback()
            blocker.close()
            self.assertTrue(backend.storage_status().readable)
            self.assertIsNotNone(backend.store)
            backend.close()

    def test_long_lived_client_discovers_external_pending_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            PersistentSaveOutbox(root / "pending").add({
                "game_id": "sokoban", "player": "other", "score": 22,
                "request_id": "external-pending-request-0001",
            })
            self.assertEqual(
                backend.retry_failed_saves().result(timeout=5), 1)
            self.assertTrue(backend.drain(5))
            self.assertEqual(backend.store.attempt_count("sokoban"), 1)
            backend.close()

    def test_local_sync_api_rejects_unknown_keyword(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(db_path=Path(directory) / "games.db")
            with self.assertRaises(TypeError):
                backend.submit_score("snake", "p", 1, unexpected=True)
            backend.close()

    def test_flask_leaderboard_rejects_profile_id_as_json(self):
        try:
            from server.app import create_app
        except ImportError:
            self.skipTest("Flask optional dependency is not installed")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DB_PATH": str(Path(directory) / "games.db")})
            response = app.test_client().get(
                "/api/leaderboard/tetris?profile_id=p")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["code"],
                             "unsupported_query_parameter")

    def test_core_import_does_not_require_requests(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'requests' or name.startswith('requests.'):
        raise ImportError('requests intentionally unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import client.launcher
import client.games.tetris
"""
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            env={**os.environ, "SDL_VIDEODRIVER": "dummy",
                 "SDL_AUDIODRIVER": "dummy"}, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_window_close_and_reset_use_the_same_unsaved_guard(self):
        import pygame
        from client.common.ui import BaseGame, SAVE_SAVING

        pygame.init()

        class Backend:
            def leaderboard_async(self, *_args, **_kwargs):
                future = Future()
                future.set_result([])
                return future

        class Demo(BaseGame):
            game_id = "tetris"

            def __init__(self):
                self.resets = 0
                super().__init__(240, 240, backend=Backend())

            def update(self, _dt):
                pass

            def draw(self):
                pass

            def reset(self):
                self.resets += 1

        game = Demo()
        game.score_save_state = SAVE_SAVING
        quit_event = pygame.event.Event(pygame.QUIT)
        game.handle_event(quit_event)
        self.assertTrue(game.running)
        game.handle_event(quit_event)
        self.assertFalse(game.running)

        game.running = True
        game._destructive_action_armed = None
        game._discard_unsaved_armed = False
        game.request_reset()
        self.assertEqual(game.resets, 0)
        game.request_reset()
        self.assertEqual(game.resets, 1)
        pygame.quit()

    def test_ui_retry_reuses_attempt_and_revision(self):
        import pygame
        from game_service.local_backend import completed_future
        from client.common.ui import BaseGame, SAVE_FAILED, SAVE_SAVED

        pygame.init()

        class Backend:
            def __init__(self):
                self.payloads = []

            def submit_score_reliable_async(self, *_args, **kwargs):
                self.payloads.append(kwargs)
                if len(self.payloads) == 1:
                    return completed_future({
                        "ok": False, "code": "busy", "retryable": True})
                return completed_future({
                    "ok": True, "id": 1, "attempt_recorded": True})

            def leaderboard_async(self, *_args, **_kwargs):
                return completed_future([])

        class Demo(BaseGame):
            game_id = "snake"

            def update(self, _dt):
                pass

            def draw(self):
                pass

            def reset(self):
                self.begin_score_session()

        backend = Backend()
        game = Demo(240, 240, backend=backend)
        game.on_game_over(12)
        game._poll_score_submission()
        self.assertEqual(game.score_save_state, SAVE_FAILED)
        game.retry_score_save()
        game._poll_score_submission()
        self.assertEqual(game.score_save_state, SAVE_SAVED)
        first, second = backend.payloads
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(first["attempt_uuid"], second["attempt_uuid"])
        self.assertEqual(first["revision"], second["revision"])
        prior_attempt = first["attempt_uuid"]
        game.request_reset()
        self.assertNotEqual(game._score_attempt_uuid, prior_attempt)
        pygame.quit()


if __name__ == "__main__":
    unittest.main()
