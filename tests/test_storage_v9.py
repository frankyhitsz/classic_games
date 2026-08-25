"""Regression coverage for the eleventh local-first review."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from game_service.catalog import GAME_BY_ID
from game_service.data_cli import (export_data, import_data, preview_import)
from game_service.local_backend import (LocalBackendClient,
                                        PersistentSaveOutbox,
                                        PersistentStateOutbox)
from game_service.mutation import normalize_score_mutation
from game_service.store import LocalGameStore, StoreError


def completed(value):
    future = Future()
    future.set_result(value)
    return future


class ArchiveSafetyTests(unittest.TestCase):
    def test_export_refuses_database_sidecar_and_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            for target in (database, Path(f"{database}-wal"),
                           Path(f"{database}-shm"),
                           root / "pending" / "archive.json"):
                with self.assertRaises(StoreError) as raised:
                    export_data(database, target)
                self.assertEqual(raised.exception.code, "unsafe_export_target")
            archive = root / "archive.json"
            archive.write_text("keep", encoding="utf-8")
            with self.assertRaises(StoreError) as raised:
                export_data(database, archive)
            self.assertEqual(raised.exception.code, "export_target_exists")
            export_data(database, archive, force=True)
            self.assertEqual(json.loads(archive.read_text())["archive_version"], 3)

    def test_active_pending_round_trip_preserves_envelopes_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source" / "games.db"
            target_path = root / "target" / "games.db"
            archive = root / "archive.json"
            source = LocalGameStore(source_path)
            profile_id = source.ensure_profile("player", "a" * 32)["profile_id"]
            score_outbox = PersistentSaveOutbox(source_path.parent / "pending")
            mutation = normalize_score_mutation(
                "snake", "player", 22, profile_id=profile_id,
                request_id="pending-request-00000001",
                attempt_uuid="pending-attempt-00000001", revision=3)
            score_outbox.add_mutation(mutation, created_at=1_700_000_000.0)
            score_outbox.increment_attempt(mutation.request_id)
            state_outbox = PersistentStateOutbox(
                source_path.parent / "pending-state")
            key = f"setting:{profile_id}:volume"
            state_outbox.put(
                key, "set_setting", (profile_id, "volume", 0.6),
                logical_revision=99, operation_id="pending-state-op")

            exported = export_data(source_path, archive)
            self.assertEqual(exported["pending_scores"], 1)
            self.assertEqual(exported["pending_state"], 1)
            LocalGameStore(target_path)
            restored = import_data(target_path, archive)
            self.assertEqual(restored["pending_restored"], {
                "scores": 1, "state": 1})
            envelope, restored_mutation = PersistentSaveOutbox(
                target_path.parent / "pending").list_envelopes()[0]
            self.assertEqual(envelope.attempt_count, 1)
            self.assertEqual(restored_mutation.payload_hash, mutation.payload_hash)
            restored_state = PersistentStateOutbox(
                target_path.parent / "pending-state").read_key(key)
            self.assertEqual(
                restored_state["logical_revision"], 99)

    def test_attempt_ids_are_remapped_and_alternate_collisions_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.db"
            target_path = root / "target.db"
            archive = root / "archive.json"
            source = LocalGameStore(source_path)
            target = LocalGameStore(target_path)
            source.record_mutation(normalize_score_mutation(
                "snake", "source", 10, profile_id="1" * 32,
                request_id="request-source-00000001",
                attempt_uuid="attempt-source-00000001", revision=1))
            target.record_mutation(normalize_score_mutation(
                "snake", "target", 20, profile_id="2" * 32,
                request_id="request-target-00000001",
                attempt_uuid="attempt-target-00000001", revision=1))
            export_data(source_path, archive)
            self.assertTrue(preview_import(target_path, archive)["ok"])
            import_data(target_path, archive)
            self.assertEqual(LocalGameStore(target_path).attempt_count(), 2)

            collision_source = root / "collision.db"
            collision_archive = root / "collision.json"
            LocalGameStore(collision_source).record_mutation(
                normalize_score_mutation(
                    "snake", "other", 30, profile_id="3" * 32,
                    request_id="request-target-00000001",
                    attempt_uuid="attempt-other-000000001", revision=1))
            export_data(collision_source, collision_archive)
            preview = preview_import(target_path, collision_archive)
            self.assertFalse(preview["ok"])
            self.assertEqual(preview["tables"]["attempts"]["conflicts"], 1)

    def test_pending_request_conflict_is_found_before_database_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source" / "games.db"
            target_path = root / "target" / "games.db"
            archive = root / "archive.json"
            source = LocalGameStore(source_path)
            source_profile = source.ensure_profile(
                "source", "31" * 16)["profile_id"]
            target = LocalGameStore(target_path)
            target.ensure_profile("target", "32" * 16)
            request_id = "shared-pending-request-001"
            PersistentSaveOutbox(source_path.parent / "pending").add_mutation(
                normalize_score_mutation(
                    "snake", "source", 10, profile_id=source_profile,
                    request_id=request_id,
                    attempt_uuid="source-pending-attempt-01", revision=1))
            PersistentSaveOutbox(target_path.parent / "pending").add_mutation(
                normalize_score_mutation(
                    "snake", "target", 99, profile_id="32" * 16,
                    request_id=request_id,
                    attempt_uuid="target-pending-attempt-01", revision=1))
            export_data(source_path, archive)
            preview = preview_import(target_path, archive)
            self.assertFalse(preview["ok"])
            self.assertIn("pending_scores[0]", "\n".join(preview["errors"]))
            before = target.attempt_count()
            with self.assertRaises(StoreError):
                import_data(target_path, archive)
            self.assertEqual(target.attempt_count(), before)

    def test_archive_hash_depth_and_nan_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            archive = root / "archive.json"
            LocalGameStore(database)
            export_data(database, archive)
            value = json.loads(archive.read_text(encoding="utf-8"))
            value["schema_version"] += 1
            archive.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(StoreError) as raised:
                preview_import(database, archive)
            self.assertEqual(raised.exception.code, "archive_hash_mismatch")

            deep = []
            cursor = deep
            for _ in range(40):
                child = []
                cursor.append(child)
                cursor = child
            archive.write_text(json.dumps(deep), encoding="utf-8")
            with self.assertRaises(StoreError) as raised:
                preview_import(database, archive)
            self.assertIn(raised.exception.code, {
                "archive_too_complex", "invalid_archive"})

            archive.write_text('{"archive_version":2,"tables":{},"x":NaN}',
                               encoding="utf-8")
            with self.assertRaises(StoreError) as raised:
                preview_import(database, archive)
            self.assertEqual(raised.exception.code, "invalid_archive")

    def test_import_only_seeds_missing_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.db"
            target_path = root / "target.db"
            archive = root / "archive.json"
            source = LocalGameStore(source_path)
            source.ensure_profile("source", "4" * 32)
            target = LocalGameStore(target_path)
            profile_id = target.ensure_profile("target", "5" * 32)["profile_id"]
            target.set_setting(profile_id, "volume", 0.5)
            key = f"setting:{profile_id}:volume"
            before = target.get_state_receipt(key)
            export_data(source_path, archive)
            import_data(target_path, archive)
            after = target.get_state_receipt(key)
            self.assertEqual(after["logical_revision"], before["logical_revision"])
            self.assertEqual(after["operation_id"], before["operation_id"])


class StateOrderingTests(unittest.TestCase):
    def test_direct_baseline_supersedes_older_high_revision_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            profile_id = store.ensure_profile("player", "6" * 32)["profile_id"]
            store.set_setting(profile_id, "volume", 0.8)
            key = f"setting:{profile_id}:volume"
            baseline = store.get_state_receipt(key)
            outbox = PersistentStateOutbox(root / "state")
            stale = outbox._operation(
                key, "set_setting", (profile_id, "volume", 0.1),
                baseline["logical_revision"] + 1000, "old-high-revision",
                updated_at=baseline["occurred_at"] - 1.0)
            result = store.apply_state_operation(stale)
            self.assertTrue(result["superseded"])
            self.assertEqual(store.get_setting(profile_id, "volume"), 0.8)

    def test_permanent_rejection_restores_previous_pending_and_quarantines_new(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile_id = backend.ensure_profile_async(
                "player", "7" * 32).result(timeout=5)["profile_id"]
            key = f"setting:{profile_id}:volume"
            old = backend.state_outbox.put(
                key, "set_setting", (profile_id, "volume", 0.2),
                logical_revision=10, operation_id="old-pending")
            operation = backend._new_state_operation(
                key, "set_setting", (profile_id, "volume", 0.9))
            with backend._lock:
                backend._unpublished_state.add(operation["operation_id"])
            original = backend.store.apply_state_operation

            def reject(value):
                if value["operation_id"] == operation["operation_id"]:
                    raise StoreError("invalid_setting", "rejected")
                return original(value)

            with patch.object(backend.store, "apply_state_operation", reject):
                result = backend._durable_state_write(operation)
            self.assertFalse(result["ok"])
            self.assertTrue(result["previous_pending_restored"])
            restored = backend.state_outbox.read_key(key)
            self.assertEqual(restored["payload_hash"], old["payload_hash"])
            self.assertGreater(backend.state_outbox.quarantined_count, 0)
            backend.close()

    def test_healthy_receipt_read_does_not_request_writer_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            store = LocalGameStore(database, busy_timeout_ms=20)
            profile_id = store.ensure_profile("player", "8" * 32)["profile_id"]
            store.set_setting(profile_id, "volume", 0.4)
            key = f"setting:{profile_id}:volume"
            writer = sqlite3.connect(database, timeout=0.02)
            writer.execute("BEGIN IMMEDIATE")
            try:
                self.assertIsNotNone(store.get_state_receipt(key))
            finally:
                writer.rollback()
                writer.close()

    def test_local_backend_startup_does_not_parse_score_spool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                    PersistentSaveOutbox, "list",
                    side_effect=AssertionError("synchronous parse")):
                backend = LocalBackendClient(
                    db_path=root / "games.db", outbox_path=root / "pending")
                backend.close()


class GameAndReleaseTests(unittest.TestCase):
    class Backend:
        is_local = True
        pending_saves_are_durable = True

        def __init__(self, saved=None):
            self.saved = saved
            self.saves = []
            self.submissions = []

        def load_slot_async(self, *_args):
            return completed(self.saved)

        def save_slot_async(self, *args):
            self.saves.append(args)
            value = args[3]
            return completed({
                "ok": True, "state_apply": "committed",
                "value": value,
                "value_hash": LocalGameStore._state_value_hash({
                    "state": value, "state_version": value["version"],
                    "ruleset_version": args[4]})})

        def submit_score_reliable_async(self, *args, **kwargs):
            self.submissions.append((args, kwargs))
            return completed({"ok": True, "id": 1, "score": args[2]})

        submit_score_async = submit_score_reliable_async

        def leaderboard(self, *_args, **_kwargs):
            return []

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

    def test_2048_v4_unannounced_terminal_restores_overlay_and_score(self):
        from client.games.game_2048 import Game2048

        state = {
            "version": 4, "game_state": "playing", "score": 4096,
            "won": True, "won_announced": False,
            "attempt_uuid": "restored-attempt-0000001",
            "attempt_revision": 2, "slot_revision": 3,
            "confirmed_score": None,
            "owner_token": "released-owner-0000001",
            "owner_status": "released", "takeover_from": None,
            "grid": [[2048, 4, 0, 0], [0, 0, 0, 0],
                     [0, 0, 0, 0], [0, 0, 0, 0]],
        }
        backend = self.Backend({
            "state": state,
            "ruleset_version": GAME_BY_ID["2048"].ruleset_version,
            "value_hash": "a" * 64,
        })
        game = Game2048(backend=backend, profile_id="9" * 32)
        game._poll_slot_load()
        self.assertEqual(game.state, "won")
        self.assertTrue(game._won_announced)
        self.assertEqual(len(backend.submissions), 1)
        self.assertEqual(backend.submissions[0][0][2], 4096)

    def test_2048_move_autosave_is_debounced_but_close_flushes(self):
        from client.games.game_2048 import Game2048

        backend = self.Backend(None)
        game = Game2048(backend=backend, profile_id="a1" * 16)
        game._poll_slot_load()
        backend.saves.clear()
        with patch("pygame.time.get_ticks", return_value=1000):
            game._queue_autosave_slot()
            game._flush_autosave_if_due()
        self.assertEqual(backend.saves, [])
        with patch("pygame.time.get_ticks", return_value=1200):
            game._flush_autosave_if_due()
        self.assertEqual(len(backend.saves), 1)
        backend.saves.clear()
        game._queue_autosave_slot()
        game.before_close()
        self.assertEqual(backend.saves[-1][3]["owner_status"], "released")

    def test_tetris_aliases_trigger_one_logical_edge(self):
        import pygame
        from client.games.tetris import Tetris

        game = Tetris(backend=self.Backend())
        start_x = game.piece.x
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_LEFT))
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_a))
        self.assertEqual(game.piece.x, start_x - 1)
        start_y = game.piece.y
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_DOWN))
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_s))
        self.assertEqual(game.piece.y, start_y + 1)

    def test_release_stage_timeout_is_machine_readable(self):
        from tests import release

        timeout = subprocess.TimeoutExpired(
            ["python", "hang.py"], 1, output=b"partial")
        with patch("tests.release.subprocess.run", side_effect=timeout):
            result = release._run_stage(
                "storage", ["python", "hang.py"], cwd=Path.cwd(),
                environment={}, timeout=1)
        self.assertEqual(result["returncode"], 124)
        self.assertEqual(result["timeout_seconds"], 1)
        self.assertIn("partial", result["output"])

    def test_release_profile_contains_wheel_smoke(self):
        from tests.release import _commands

        self.assertIn("wheel-smoke", dict(_commands("release")))


if __name__ == "__main__":
    unittest.main()
