"""Ninth-review ordering, recovery, and save-slot regression checks."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from client.profile_controller import ProfileController
from game_service.catalog import GAME_BY_ID
from game_service.local_backend import (LocalBackendClient,
                                        PersistentStateOutbox,
                                        completed_future)
from game_service.service import SaveState
from game_service.store import LocalGameStore, StoreError


class StateReceiptTests(unittest.TestCase):
    def test_stale_worker_cannot_overwrite_committed_newer_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            outbox = PersistentStateOutbox(root / "pending-state")
            profile_id = "a" * 32
            store.apply_state_operation(outbox._operation(
                f"profile:{profile_id}", "ensure_profile",
                ("player", profile_id), 1, "profile"))
            key = f"setting:{profile_id}:volume"
            newer = outbox._operation(
                key, "set_setting", (profile_id, "volume", 0.8), 20, "new")
            older = outbox._operation(
                key, "set_setting", (profile_id, "volume", 0.2), 10, "old")

            committed = store.apply_state_operation(newer)
            superseded = store.apply_state_operation(older)

            self.assertEqual(committed["state_apply"], "committed")
            self.assertTrue(superseded["superseded"])
            self.assertEqual(superseded["winning_operation_id"], "new")
            self.assertEqual(store.get_setting(profile_id, "volume"), 0.8)

    def test_duplicate_operation_does_not_change_row_version_or_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            outbox = PersistentStateOutbox(root / "pending-state")
            profile_id = "b" * 32
            store.apply_state_operation(outbox._operation(
                f"profile:{profile_id}", "ensure_profile",
                ("player", profile_id), 1, "profile"))
            operation = outbox._operation(
                f"setting:{profile_id}:volume", "set_setting",
                (profile_id, "volume", 0.5), 2, "setting")
            store.apply_state_operation(operation)
            with store.connection() as connection:
                before = tuple(connection.execute(
                    "SELECT value_version,updated_at FROM settings WHERE "
                    "profile_id=? AND key='volume'", (profile_id,)).fetchone())
            duplicate = store.apply_state_operation(operation)
            with store.connection() as connection:
                after = tuple(connection.execute(
                    "SELECT value_version,updated_at FROM settings WHERE "
                    "profile_id=? AND key='volume'", (profile_id,)).fetchone())
            self.assertTrue(duplicate["duplicate_operation"])
            self.assertEqual(before, after)

    def test_backend_emits_superseded_instead_of_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile_id = backend.ensure_profile_async(
                "player").result(timeout=5)["profile_id"]
            key = f"setting:{profile_id}:volume"
            newer = backend.state_outbox._operation(
                key, "set_setting", (profile_id, "volume", 0.9), 50, "new")
            older = backend.state_outbox._operation(
                key, "set_setting", (profile_id, "volume", 0.1), 40, "old")
            backend._durable_state_write(newer)
            result = backend._durable_state_write(older)
            event = backend.poll_local_state_events()[-1]
            self.assertTrue(result["superseded"])
            self.assertEqual(event.state, SaveState.SUPERSEDED)
            self.assertEqual(
                backend.store.get_setting(profile_id, "volume"), 0.9)
            backend.close()

    def test_persistent_clock_survives_wall_clock_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            first = PersistentStateOutbox(path).next_revision()
            with mock.patch(
                    "game_service.local_backend.time.time_ns",
                    return_value=1):
                second = PersistentStateOutbox(path).next_revision()
            self.assertGreater(second, first)

    def test_stale_profile_progress_and_slot_operations_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            outbox = PersistentStateOutbox(root / "pending-state")
            profile_id = "e" * 32
            profile_key = f"profile:{profile_id}"
            store.apply_state_operation(outbox._operation(
                profile_key, "ensure_profile", ("new name", profile_id),
                20, "new-profile"))
            store.apply_state_operation(outbox._operation(
                profile_key, "ensure_profile", ("old name", profile_id),
                10, "old-profile"))
            self.assertEqual(store.last_profile()["display_name"], "new name")

            ruleset = GAME_BY_ID["sokoban"].ruleset_version
            progress_key = (
                f"progress:{profile_id}:sokoban:{ruleset}:campaign")
            store.apply_state_operation(outbox._operation(
                progress_key, "set_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 8}, ruleset), 40, "new-progress"))
            store.apply_state_operation(outbox._operation(
                progress_key, "set_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 2}, ruleset), 30, "old-progress"))
            self.assertEqual(store.get_progress(
                profile_id, "sokoban", "campaign")["unlocked_level"], 8)

            slot_key = f"slot:{profile_id}:2048:autosave"
            slot_ruleset = GAME_BY_ID["2048"].ruleset_version
            store.apply_state_operation(outbox._operation(
                slot_key, "save_slot",
                (profile_id, "2048", "autosave",
                 {"version": 3, "slot_revision": 9}, slot_ruleset),
                60, "new-slot"))
            store.apply_state_operation(outbox._operation(
                slot_key, "save_slot",
                (profile_id, "2048", "autosave",
                 {"version": 3, "slot_revision": 1}, slot_ruleset),
                50, "old-slot"))
            self.assertEqual(store.load_slot(
                profile_id, "2048", "autosave")["state"]["slot_revision"], 9)

    def test_late_monotonic_merge_applies_once_without_regressing_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            outbox = PersistentStateOutbox(root / "pending-state")
            profile_id = "f" * 32
            store.apply_state_operation(outbox._operation(
                f"profile:{profile_id}", "ensure_profile",
                ("player", profile_id), 1, "profile"))
            ruleset = GAME_BY_ID["sokoban"].ruleset_version
            key = f"progress:{profile_id}:sokoban:{ruleset}:campaign"
            newer = outbox._operation(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 3, "completed_levels": [1]}, ruleset),
                20, "new-merge")
            older = outbox._operation(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 8, "completed_levels": [0]}, ruleset),
                10, "old-merge")
            store.apply_state_operation(newer)
            merged = store.apply_state_operation(older)
            with store.connection() as connection:
                version_before = connection.execute(
                    "SELECT value_version FROM progress WHERE profile_id=?",
                    (profile_id,)).fetchone()[0]
            duplicate = store.apply_state_operation(older)
            with store.connection() as connection:
                version_after = connection.execute(
                    "SELECT value_version FROM progress WHERE profile_id=?",
                    (profile_id,)).fetchone()[0]
            value = store.get_progress(
                profile_id, "sokoban", "campaign", ruleset_version=ruleset)
            receipt = store.get_state_receipt(key)
            self.assertEqual(merged["state_apply"], "merged_stale")
            self.assertTrue(duplicate["duplicate_operation"])
            self.assertEqual(value["unlocked_level"], 8)
            self.assertEqual(value["completed_levels"], [0, 1])
            self.assertEqual(version_before, version_after)
            self.assertEqual(receipt["operation_id"], "new-merge")

    def test_unexpected_worker_failure_preserves_operation_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile_id = backend.ensure_profile_async(
                "player").result(timeout=5)["profile_id"]
            with mock.patch.object(
                    backend, "_durable_state_write",
                    side_effect=RuntimeError("unexpected")):
                future = backend.set_setting_async(
                    profile_id, "volume", 0.4)
                with self.assertRaises(RuntimeError):
                    future.result(timeout=5)
            deadline = time.monotonic() + 2
            while backend.failed_save_count() == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(backend.failed_save_count(), 1)
            self.assertFalse(backend.pending_saves_are_durable)
            backend.close()

    def test_state_status_lookup_never_waits_for_disk_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            with mock.patch.object(
                    backend.state_outbox, "read_key",
                    side_effect=lambda _key: time.sleep(0.2)) as reader:
                started = time.perf_counter()
                self.assertIsNone(backend.get_local_state_status("missing"))
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, 0.05)
                self.assertTrue(backend._read_worker.drain(2))
                for _ in range(5):
                    backend.get_local_state_status("missing")
                self.assertEqual(reader.call_count, 1)
            backend.close()


class ReceiptAndSchemaTests(unittest.TestCase):
    def test_score_retry_after_receipt_expiry_is_semantic(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            request_id = "expired-receipt-request-0001"
            kwargs = {
                "request_id": request_id,
                "attempt_uuid": "expired-receipt-attempt-0001",
                "revision": 1,
            }
            first = store.record_score("snake", "player", 7, **kwargs)
            with store.connection() as connection:
                connection.execute(
                    "UPDATE save_requests SET expires_at=0 WHERE request_id=?",
                    (request_id,))
                connection.commit()
            replay = store.record_score("snake", "player", 7, **kwargs)
            self.assertEqual(replay["id"], first["id"])
            self.assertTrue(replay["receipt_rebuilt"])
            self.assertIsNotNone(store.get_save_receipt(request_id))
            self.assertEqual(store.attempt_count(), 1)
            with self.assertRaises(StoreError) as context:
                store.record_score("snake", "player", 8, **kwargs)
            self.assertEqual(context.exception.code, "request_id_conflict")

    def test_v6_state_checks_reject_invalid_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            profile_id = store.ensure_profile("player")["profile_id"]
            with store.connection() as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO settings VALUES(?,?,?,?,?)",
                        (profile_id, "volume", "0.5", 0, time.time()))

    def test_storage_status_reports_both_durability_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            status = store.storage_status(
                score_outbox_writable=True,
                state_outbox_writable=False)
            self.assertTrue(status.score_outbox_writable)
            self.assertFalse(status.state_outbox_writable)
            self.assertFalse(status.outbox_writable)


class ProfileStartupTests(unittest.TestCase):
    def test_failed_startup_load_remains_unresolved_until_retry(self):
        controller = ProfileController("a" * 32)
        load = Future()
        controller.start_load()
        controller.bind("load", load, match_profile=False)
        controller.queue_launch("2048")
        load.set_exception(OSError("read failed"))
        operation = controller.completed("load")
        self.assertIsNotNone(operation)
        controller.fail_load()
        self.assertTrue(controller.startup_load_failed)
        self.assertFalse(controller.identity_resolved)
        self.assertIsNone(controller.pop_ready_launch(ready=False))
        controller.start_load()
        controller.resolve("b" * 32)
        self.assertEqual(controller.pop_ready_launch(ready=True), "2048")


class Game2048RecoveryTests(unittest.TestCase):
    class Backend:
        is_local = True
        pending_saves_are_durable = True

        def __init__(self, saved):
            self.saved = saved
            self.quarantine = Future()
            self.quarantine_calls = 0

        def load_slot_async(self, *_args):
            return completed_future(self.saved)

        def save_slot_async(self, *_args):
            return completed_future({"ok": True})

        def quarantine_slot_async(self, *_args):
            self.quarantine_calls += 1
            return self.quarantine

        def failed_save_count(self):
            return 0

    @classmethod
    def setUpClass(cls):
        import pygame
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        import pygame
        pygame.display.quit()

    @staticmethod
    def _state(**changes):
        value = {
            "version": 3, "game_state": "playing", "score": 0,
            "won": False, "won_announced": False,
            "attempt_uuid": "slot-attempt-000000000001",
            "attempt_revision": 0, "slot_revision": 1,
            "confirmed_score": None,
            "grid": [[2, 4, 0, 0], [0, 0, 0, 0],
                     [0, 0, 0, 0], [0, 0, 0, 0]],
        }
        value.update(changes)
        return value

    def test_quarantine_message_waits_for_durable_ack(self):
        from client.games.game_2048 import Game2048
        ruleset = GAME_BY_ID["2048"].ruleset_version
        backend = self.Backend({
            "state": self._state(
                won=False,
                grid=[[2048, 4, 0, 0], [0, 0, 0, 0],
                      [0, 0, 0, 0], [0, 0, 0, 0]]),
            "ruleset_version": ruleset})
        game = Game2048(backend=backend, profile_id="c" * 32)
        game._poll_slot_load()
        self.assertEqual(game.slot_load_state, "quarantining")
        self.assertNotIn("已隔离", game.slot_load_error)
        backend.quarantine.set_result(True)
        game._poll_slot_quarantine()
        self.assertEqual(game.slot_load_state, "failed")
        self.assertIn("已隔离", game.slot_load_error)

    def test_ruleset_mismatch_is_not_quarantined_as_corruption(self):
        from client.games.game_2048 import Game2048
        backend = self.Backend({
            "state": self._state(), "ruleset_version": "future-ruleset"})
        game = Game2048(backend=backend, profile_id="d" * 32)
        game._poll_slot_load()
        self.assertEqual(game.slot_load_state, "failed")
        self.assertIn("不兼容", game.slot_load_error)
        self.assertEqual(backend.quarantine_calls, 0)

    def test_failed_quarantine_cannot_confirm_overwrite(self):
        import pygame
        from client.games.game_2048 import Game2048
        ruleset = GAME_BY_ID["2048"].ruleset_version
        backend = self.Backend({
            "state": self._state(
                won=False,
                grid=[[2048, 4, 0, 0], [0, 0, 0, 0],
                      [0, 0, 0, 0], [0, 0, 0, 0]]),
            "ruleset_version": ruleset})
        game = Game2048(backend=backend, profile_id="e" * 32)
        game._poll_slot_load()
        backend.quarantine.set_result(False)
        game._poll_slot_quarantine()
        before = game._score_attempt_uuid
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_n))
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_n))
        self.assertEqual(game.slot_load_state, "quarantine_failed")
        self.assertEqual(game._score_attempt_uuid, before)


if __name__ == "__main__":
    unittest.main()
