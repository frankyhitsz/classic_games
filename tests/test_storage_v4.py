"""Sixth-review fault-injection and local-data contract tests."""

from __future__ import annotations

import errno
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from game_service.catalog import GAMES, GameDescriptor, validate_catalog
from game_service.local_backend import (
    LocalBackendClient,
    PersistentSaveOutbox,
    classify_sqlite_error,
)
from game_service.mutation import normalize_score_mutation
from game_service.service import SaveState, StorageErrorKind
from game_service.store import LocalGameStore


def sqlite_failure(code: int, message: str = "localized message") -> sqlite3.Error:
    error = sqlite3.OperationalError(message)
    error.sqlite_errorcode = code
    return error


class ToggleFailureStore(LocalGameStore):
    def __init__(self, path: Path, code: int):
        super().__init__(path)
        self.code = code
        self.fail = True

    def record_mutation(self, mutation, occurred_at=None):
        if self.fail:
            raise sqlite_failure(self.code)
        return super().record_mutation(mutation, occurred_at=occurred_at)


class StorageFailureTests(unittest.TestCase):
    def test_sqlite_full_stays_pending_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ToggleFailureStore(root / "games.db", sqlite3.SQLITE_FULL)
            backend = LocalBackendClient(
                store=store, outbox_path=root / "pending")
            result = backend.submit_score(
                "tetris", "full", 88,
                request_id="full-request-000000000001",
                attempt_uuid="full-attempt-000000000001")
            self.assertEqual(result["storage_error_kind"], "full")
            self.assertTrue(result["durable_pending"])
            self.assertEqual(backend.failed_save_count(), 1)
            self.assertTrue((root / "pending" /
                             "full-request-000000000001.json").is_file())
            self.assertEqual(
                backend.get_save_status("full-request-000000000001").state,
                SaveState.DURABLE_PENDING)

            store.fail = False
            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            self.assertEqual(store.attempt_count("tetris"), 1)
            self.assertEqual(
                backend.get_save_status("full-request-000000000001").state,
                SaveState.COMMITTED)
            backend.close()

    def test_spool_enospc_and_database_full_remains_non_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ToggleFailureStore(root / "games.db", sqlite3.SQLITE_FULL)
            backend = LocalBackendClient(
                store=store, outbox_path=root / "pending")
            with mock.patch.object(
                    backend.outbox, "add_mutation",
                    side_effect=OSError(errno.ENOSPC, "no space")):
                result = backend.submit_score(
                    "snake", "full", 21,
                    request_id="non-durable-request-000001",
                    attempt_uuid="non-durable-attempt-000001")
            self.assertFalse(result["durable_pending"])
            self.assertTrue(result["pending_preserved"])
            self.assertFalse(backend.pending_saves_are_durable)

            store.fail = False
            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            self.assertEqual(store.attempt_count("snake"), 1)
            self.assertTrue(backend.pending_saves_are_durable)
            backend.close()

    def test_error_code_classification_does_not_need_english_message(self):
        full = classify_sqlite_error(sqlite_failure(sqlite3.SQLITE_FULL))
        locked = classify_sqlite_error(sqlite_failure(sqlite3.SQLITE_BUSY))
        corrupt = classify_sqlite_error(sqlite_failure(sqlite3.SQLITE_CORRUPT))
        self.assertEqual(full.kind, StorageErrorKind.FULL)
        self.assertTrue(full.retryable)
        self.assertEqual(locked.kind, StorageErrorKind.BUSY)
        self.assertEqual(corrupt.kind, StorageErrorKind.CORRUPT)
        self.assertTrue(corrupt.quarantine)

    def test_manual_retry_scan_is_non_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(
                db_path=Path(directory) / "games.db",
                outbox_path=Path(directory) / "pending")
            original = backend.outbox.list_envelopes

            def slow_scan():
                time.sleep(0.1)
                return original()

            with mock.patch.object(
                    backend.outbox, "list_envelopes", side_effect=slow_scan):
                started = time.perf_counter()
                future = backend.retry_failed_saves()
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, 0.01)
                self.assertFalse(future.done())
                future.result(timeout=5)
            backend.close()

    def test_replay_keeps_completion_time_and_emits_commit_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ToggleFailureStore(root / "games.db", sqlite3.SQLITE_BUSY)
            backend = LocalBackendClient(
                store=store, outbox_path=root / "pending")
            before = time.time()
            pending = backend.submit_score(
                "zuma", "clock", 34,
                request_id="clock-request-00000000001",
                attempt_uuid="clock-attempt-00000000001")
            self.assertTrue(pending["durable_pending"])
            time.sleep(0.03)
            retry_started = time.time()
            store.fail = False
            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            with store.connection() as connection:
                row = connection.execute(
                    "SELECT finished_at FROM attempts WHERE player='clock'"
                ).fetchone()
            self.assertGreaterEqual(row["finished_at"], before)
            self.assertLess(row["finished_at"], retry_started)
            events = backend.poll_save_events()
            self.assertIn(SaveState.DURABLE_PENDING,
                          [event.state for event in events])
            self.assertEqual(events[-1].state, SaveState.COMMITTED)
            backend.close()


class PendingParserTests(unittest.TestCase):
    def test_nonfinite_and_deep_legacy_pending_do_not_block_startup(self):
        samples = (
            '[{"game_id":"tetris","player":"p","score":NaN}]',
            json.dumps([{"nested": [[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]}]),
        )
        for sample in samples:
            with self.subTest(sample=sample[:30]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                legacy = root / "pending_saves.json"
                legacy.write_text(sample, encoding="utf-8")
                outbox = PersistentSaveOutbox(
                    root / "pending", legacy_path=legacy)
                self.assertFalse(legacy.exists())
                self.assertGreaterEqual(outbox.quarantined_count, 1)

    def test_quarantine_serialization_failure_is_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentSaveOutbox(Path(directory) / "pending")
            recursive = {}
            recursive["self"] = recursive
            with mock.patch(
                    "game_service.local_backend.canonical_json",
                    side_effect=RecursionError("recursive")):
                outbox._quarantine_value(recursive, "recursive")
            files = list(outbox.quarantine_path.glob("legacy-item.*"))
            self.assertEqual(len(files), 1)
            report = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(report["reason"], "recursive")

    def test_one_spool_oserror_does_not_hide_other_records(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentSaveOutbox(Path(directory) / "pending")
            first = normalize_score_mutation(
                "snake", "a", 1,
                request_id="read-error-request-000001")
            second = normalize_score_mutation(
                "snake", "b", 2,
                request_id="read-good-request-0000001")
            outbox.add_mutation(first)
            outbox.add_mutation(second)
            original = outbox._read_file

            def selective(path):
                if path.name.startswith("read-error"):
                    raise PermissionError(errno.EACCES, "denied")
                return original(path)

            with mock.patch.object(
                    outbox, "_read_file", side_effect=selective):
                rows = outbox.list_envelopes()
            self.assertEqual([item[1].player for item in rows], ["b"])
            self.assertIn("已保留原文件", outbox.recovery_notice)


class MigrationAndRepositoryTests(unittest.TestCase):
    @staticmethod
    def _legacy(path: Path) -> None:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE scores(id INTEGER, game_id TEXT, player TEXT, "
                "score INTEGER, extra TEXT, created_at REAL)")
            connection.execute(
                "INSERT INTO scores VALUES (1,'zuma','moved',9,NULL,1234)")

    def test_moved_legacy_database_is_not_imported_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.db"
            second = root / "second.db"
            self._legacy(first)
            second.write_bytes(first.read_bytes())
            db = root / "games.db"
            LocalGameStore(db, legacy_db_path=first)
            reopened = LocalGameStore(db, legacy_db_path=second)
            self.assertEqual(reopened.attempt_count("zuma"), 1)

    def test_invalid_current_row_is_quarantined_on_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "games.db"
            store = LocalGameStore(db)
            with store.connection() as connection:
                connection.execute("DROP TRIGGER validate_attempts_insert")
                connection.execute("DROP TRIGGER validate_attempts_update")
                connection.execute(
                    "INSERT INTO attempts(attempt_uuid,request_id,profile_id,"
                    "game_id,player,mode,ruleset_version,status,revision,score,"
                    "started_at,finished_at,score_achieved_at,created_at,updated_at) "
                    "VALUES('bad-attempt','bad-request','p','snake','p','classic',"
                    "'snake-classic-1','broken',1,1,1,1,1,1,1)")
                connection.commit()
            repaired = LocalGameStore(db)
            self.assertEqual(repaired.attempt_count(), 0)
            with repaired.connection() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM invalid_attempts").fetchone()[0]
            self.assertEqual(count, 1)
            self.assertIsNotNone(repaired.migration_backup)

    def test_profile_settings_progress_and_save_slots_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            profile = store.ensure_profile("玩家甲")
            profile_id = profile["profile_id"]
            self.assertNotEqual(profile_id, "玩家甲")
            store.set_setting(profile_id, "volume", 0.5)
            store.set_progress(
                profile_id, "sokoban", "campaign", {"unlocked_level": 3})
            store.save_slot(profile_id, "2048", "autosave", {"score": 64})
            self.assertEqual(store.get_setting(profile_id, "volume"), 0.5)
            self.assertEqual(
                store.get_progress(
                    profile_id, "sokoban", "campaign")["unlocked_level"], 3)
            self.assertEqual(
                store.load_slot(profile_id, "2048", "autosave")["state"]["score"],
                64)
            self.assertEqual(store.last_profile()["display_name"], "玩家甲")
            with self.assertRaisesRegex(Exception, "32-character UUID"):
                store.ensure_profile("bad", "display-name-is-not-an-id")

    def test_old_display_identity_migrates_to_uuid_without_losing_score(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "games.db"
            store = LocalGameStore(db)
            with store.connection() as connection:
                connection.execute("DROP TRIGGER validate_attempts_insert")
                connection.execute("DROP TRIGGER validate_attempts_update")
                connection.execute(
                    "INSERT INTO attempts(attempt_uuid,request_id,profile_id,"
                    "game_id,player,mode,ruleset_version,status,revision,score,"
                    "started_at,finished_at,score_achieved_at,created_at,updated_at) "
                    "VALUES('legacy-attempt-00001','legacy-request-00001','旧玩家','snake',"
                    "'旧玩家','classic','snake-classic-1','completed',1,7,1,1,1,1,1)")
                connection.commit()
            migrated = LocalGameStore(db)
            self.assertEqual(migrated.attempt_count("snake"), 1)
            with migrated.connection() as connection:
                row = connection.execute(
                    "SELECT profile_id FROM attempts").fetchone()
            self.assertEqual(len(row["profile_id"]), 32)
            self.assertTrue(all(
                char in "0123456789abcdef" for char in row["profile_id"]))

    def test_catalog_rejects_duplicate_or_untyped_policy(self):
        duplicate = list(GAMES) + [GAMES[0]]
        with self.assertRaises(RuntimeError):
            validate_catalog(duplicate)
        bad = GameDescriptor(
            "bad", "bad", "bad", "bad.module", "bad", "bad", "1",
            "monotonic_revision")  # type: ignore[arg-type]
        with self.assertRaises(RuntimeError):
            validate_catalog([bad])

    def test_legacy_extra_has_raw_and_depth_limits(self):
        value, lost = LocalGameStore._decode_legacy_extra(
            json.dumps({"text": "x" * (65 * 1024)}))
        self.assertIsNone(value)
        self.assertTrue(lost)
        nested = {}
        cursor = nested
        for _ in range(40):
            cursor["child"] = {}
            cursor = cursor["child"]
        value, lost = LocalGameStore._decode_legacy_extra(json.dumps(nested))
        self.assertIsNone(value)
        self.assertTrue(lost)

    def test_malformed_request_lock_is_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentSaveOutbox(Path(directory) / "pending")
            outbox.path.mkdir()
            lock = outbox._request_lock_path("orphan-request-0000000001")
            lock.write_text('{"pid":99999999,"created_at":1}',
                            encoding="ascii")
            with outbox._request_lock("orphan-request-0000000001"):
                self.assertTrue(lock.exists())
            # Lock files are stable inodes; the operating-system lock, not
            # stale PID text, defines ownership. Reacquiring proves release.
            with outbox._request_lock("orphan-request-0000000001"):
                self.assertTrue(lock.exists())


class FlaskBoundaryTests(unittest.TestCase):
    def test_stats_rejects_limit_and_http_errors_are_json(self):
        try:
            from server.app import create_app
        except ImportError:
            self.skipTest("Flask optional dependency is not installed")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DB_PATH": str(Path(directory) / "games.db")})
            client = app.test_client()
            invalid = client.get("/api/stats/tetris?limit=3")
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(
                invalid.get_json()["code"], "unsupported_query_parameter")
            missing = client.get("/api/not-here")
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.content_type, "application/json")
            method = client.post("/api/health")
            self.assertEqual(method.status_code, 405)
            self.assertEqual(method.content_type, "application/json")


class LeaderboardSemanticsTests(unittest.TestCase):
    def test_tied_ranks_share_medal_and_recent_has_no_medal(self):
        import pygame

        from client.common import ui

        pygame.init()
        surface = pygame.Surface((360, 220))
        drawn = []

        def capture(_surface, value, *_args, **_kwargs):
            drawn.append(value)

        entries = [
            {"rank": 1, "player": "a", "score": 10},
            {"rank": 1, "player": "b", "score": 10},
            {"rank": 3, "player": "c", "score": 8},
        ]
        with mock.patch.object(ui, "draw_text", side_effect=capture):
            ui.draw_leaderboard(
                surface, pygame.Rect(0, 0, 350, 200), entries)
        self.assertEqual(drawn.count("1st"), 2)
        self.assertEqual(drawn.count("3rd"), 1)

        drawn.clear()
        with mock.patch.object(ui, "draw_text", side_effect=capture):
            ui.draw_leaderboard(
                surface, pygame.Rect(0, 0, 350, 200), entries,
                competitive=False)
        self.assertEqual(drawn.count("•"), 3)
        self.assertNotIn("1st", drawn)
        pygame.quit()

    def test_sokoban_zero_total_is_submitted(self):
        import pygame

        from client.games.sokoban import LEVELS, Sokoban
        from game_service.local_backend import completed_future

        class Backend:
            def __init__(self):
                self.calls = []

            def submit_score_reliable_async(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return completed_future({"ok": True, "id": 1, "score": 0})

            def leaderboard_async(self, *_args, **_kwargs):
                return completed_future([])

        pygame.init()
        backend = Backend()
        game = Sokoban(backend=backend)
        game.completed_levels = set(range(len(LEVELS)))
        game.level_scores = {index: 0 for index in range(len(LEVELS))}
        game.level_idx = len(LEVELS) - 1
        game.boxes = set(game.targets)
        game.moves = 2000
        game._check_win()
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][0][2], 0)
        pygame.quit()

    def test_2048_autosave_restores_the_board(self):
        import pygame

        from client.games.game_2048 import Game2048, Tile

        pygame.init()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            first = Game2048(backend=backend, player="save",
                             profile_id="1234567890abcdef1234567890abcdef")
            first._slot_load_future.result(timeout=5)
            first._poll_slot_load()
            first.tiles = []
            first.grid = [[None] * 4 for _ in range(4)]
            tile = Tile(value=128, row=2, col=3)
            first.tiles.append(tile)
            first.grid[2][3] = tile
            first.score = 512
            first._save_autosave_slot()
            self.assertTrue(backend.drain(5))
            first.before_close()
            self.assertTrue(backend.drain(5))

            restored = Game2048(
                backend=backend, player="save",
                profile_id="1234567890abcdef1234567890abcdef")
            restored._slot_load_future.result(timeout=5)
            restored._poll_slot_load()
            self.assertEqual(restored.score, 512)
            self.assertEqual(restored.grid[2][3].value, 128)
            backend.close()
        pygame.quit()

    def test_durable_pending_ui_becomes_saved_after_background_retry(self):
        import pygame

        from client.common.ui import BaseGame, SAVE_PENDING, SAVE_SAVED

        class ResultGame(BaseGame):
            game_id = "snake"

            def update(self, _dt):
                pass

            def draw(self):
                pass

            def reset(self):
                self.begin_score_session()

        pygame.init()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ToggleFailureStore(root / "games.db", sqlite3.SQLITE_BUSY)
            backend = LocalBackendClient(
                store=store, outbox_path=root / "pending")
            game = ResultGame(
                160, 120, backend=backend, player="pending",
                profile_id="abcdefabcdefabcdefabcdefabcdefab")
            game.on_game_over(12)
            game._score_submit_future.result(timeout=5)
            game._poll_score_submission()
            self.assertEqual(game.score_save_state, SAVE_PENDING)
            store.fail = False
            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            game._poll_score_submission()
            self.assertEqual(game.score_save_state, SAVE_SAVED)
            backend.close()
        pygame.quit()


if __name__ == "__main__":
    unittest.main()
