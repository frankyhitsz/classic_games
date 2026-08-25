"""Regression coverage added while resolving the thirteenth review."""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from game_service.data_cli import (cleanup_recovery_data, export_data,
                                   export_transaction_data, inspect_transactions,
                                   preview_import, recover_transactions_data,
                                   restore_replace_data)
from game_service.import_transaction import (FileOperation, ImportTransaction,
                                             recover_import_transactions,
                                             validate_file_operations)
from game_service.local_backend import (LocalBackendClient,
                                        PersistentStateOutbox)
from game_service.maintenance import (ApplicationSession, MaintenanceBusyError,
                                      application_lock_path,
                                      inactive_application_lock, lock_path,
                                      maintenance_lock)
from game_service.mutation import canonical_json
from game_service.store import LocalGameStore, StoreError


PROFILE_ID = "1234567890abcdef1234567890abcdef"


def _rehash_archive(path: Path, mutate) -> dict:
    archive = json.loads(path.read_text(encoding="utf-8"))
    mutate(archive)
    payload = {key: value for key, value in archive.items()
               if key != "manifest_hash"}
    archive["manifest_hash"] = hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()
    path.write_text(canonical_json(archive), encoding="utf-8")
    return archive


class StartupAndLockTests(unittest.TestCase):
    def test_local_backend_recovers_import_before_opening_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            store = LocalGameStore(database)
            target = root / "pending" / "intent.json"
            transaction = ImportTransaction.prepare(
                database, [FileOperation(target, b"new")])
            with store.connection() as connection:
                connection.execute(
                    "INSERT INTO profiles VALUES(?,?,?,?)",
                    (PROFILE_ID, "unfinished", 1.0, 1.0))
                connection.commit()
            transaction.mark("DB_APPLIED")

            backend = LocalBackendClient(db_path=database)
            try:
                with backend.store.connection() as connection:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM profiles WHERE profile_id=?",
                        (PROFILE_ID,)).fetchone()[0]
                self.assertEqual(count, 0)
                self.assertFalse(target.exists())
                self.assertFalse(transaction.root.exists())
            finally:
                backend.close()

    def test_corrupt_recovery_blocks_startup_without_opening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            store = LocalGameStore(database)
            transaction = ImportTransaction.prepare(database, [])
            with store.connection() as connection:
                connection.execute(
                    "INSERT INTO profiles VALUES(?,?,?,?)",
                    (PROFILE_ID, "preserved", 1.0, 1.0))
                connection.commit()
            transaction.mark("DB_APPLIED")
            (transaction.root / "database-before.sqlite").write_bytes(b"bad")
            with self.assertRaises(StoreError) as raised:
                LocalBackendClient(db_path=database)
            self.assertEqual(raised.exception.code, "import_recovery_required")
            self.assertTrue(transaction.root.exists())
            with store.connection() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM profiles WHERE profile_id=?",
                    (PROFILE_ID,)).fetchone()[0], 1)

    @unittest.skipIf(os.name != "posix", "symbolic-link semantics are POSIX")
    def test_lock_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            outside = root / "outside.txt"
            outside.write_text("keep", encoding="utf-8")
            application_lock_path(database).symlink_to(outside)
            with self.assertRaises(MaintenanceBusyError):
                ApplicationSession.acquire(database, timeout=0)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

            application_lock_path(database).unlink()
            lock_path(database).symlink_to(outside)
            with self.assertRaises(MaintenanceBusyError):
                with maintenance_lock(
                        database, exclusive=True, timeout=0):
                    pass
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")


class ImportIntegrityTests(unittest.TestCase):
    def test_symlink_alias_and_conflicting_duplicate_targets_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            outside = root / "outside"
            outside.mkdir()
            (root / "pending").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(StoreError) as raised:
                validate_file_operations(database, [
                    FileOperation(root / "pending" / "x.json", b"x")])
            self.assertEqual(raised.exception.code, "unsafe_import_target")
            (root / "pending").unlink()
            with self.assertRaises(StoreError):
                validate_file_operations(database, [
                    FileOperation(root / "pending_saves.json", b"overwrite")])
            state = root / "pending-state"
            with self.assertRaises(StoreError):
                validate_file_operations(database, [
                    FileOperation(state / ".reject-x.txn", b"fabricated")])
            target = root / "pending" / "x.json"
            with self.assertRaises(StoreError) as duplicate:
                ImportTransaction.prepare(database, [
                    FileOperation(target, b"one"),
                    FileOperation(target, b"two"),
                ])
            self.assertEqual(duplicate.exception.code, "duplicate_import_target")
            transaction = ImportTransaction.prepare(database, [
                FileOperation(target, b"same"),
                FileOperation(target, b"same"),
            ])
            self.assertEqual(len(transaction.journal["operations"]), 1)
            transaction.rollback()
            transaction.finish()

    def test_staged_and_database_images_are_content_authenticated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            target = root / "pending" / "x.json"
            transaction = ImportTransaction.prepare(
                database, [FileOperation(target, b"safe")])
            (transaction.root / "staged-0.bin").write_bytes(b"evil")
            with self.assertRaises(StoreError) as staged:
                transaction.publish_files()
            self.assertEqual(staged.exception.code, "import_recovery_required")
            self.assertFalse(target.exists())
            transaction.rollback()
            transaction.finish()

            transaction = ImportTransaction.prepare(database, [])
            transaction.mark("DB_APPLIED")
            (transaction.root / "database-before.sqlite").write_bytes(b"evil")
            with self.assertRaises(StoreError):
                recover_import_transactions(database)
            self.assertTrue(transaction.root.exists())

    def test_publish_refuses_a_target_changed_after_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            target = root / "pending" / "x.json"
            target.parent.mkdir()
            target.write_bytes(b"before")
            transaction = ImportTransaction.prepare(
                database, [FileOperation(target, b"after")])
            target.write_bytes(b"racing-writer")
            with self.assertRaises(StoreError) as raised:
                transaction.publish_files()
            self.assertEqual(raised.exception.code, "import_target_changed")
            self.assertEqual(target.read_bytes(), b"racing-writer")
            transaction.rollback()
            transaction.finish()

    def test_transaction_cli_lists_then_recovers_valid_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            transaction = ImportTransaction.prepare(database, [])
            listing = inspect_transactions(database)
            self.assertEqual(listing["count"], 1)
            self.assertEqual(listing["transactions"][0]["phase"], "PREPARED")
            evidence = root / "transaction.json"
            exported = export_transaction_data(
                database, transaction.root.name, evidence)
            self.assertTrue(exported["complete"])
            value = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(value["transaction"], transaction.root.name)
            self.assertTrue(any(item["path"] == "journal.json"
                                for item in value["files"]))
            result = recover_transactions_data(database)
            self.assertEqual(result["recovered_count"], 1)
            self.assertFalse(transaction.root.exists())


class ArchiveBoundaryTests(unittest.TestCase):
    def test_export_protects_control_files_and_never_clobbers_racing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            for protected in (
                    lock_path(database), application_lock_path(database),
                    root / "pending_saves.json"):
                with self.assertRaises(StoreError) as raised:
                    export_data(database, protected, force=True)
                self.assertEqual(raised.exception.code, "unsafe_export_target")

            output = root / "backup.json"

            def racing_link(_source, destination):
                Path(destination).write_bytes(b"winner")
                raise FileExistsError

            with patch("game_service.data_cli.os.link", side_effect=racing_link):
                with self.assertRaises(StoreError) as raised:
                    export_data(database, output)
            self.assertEqual(raised.exception.code, "export_target_exists")
            self.assertEqual(output.read_bytes(), b"winner")

    @unittest.skipIf(os.name != "posix", "symbolic-link semantics are POSIX")
    def test_recovery_export_never_follows_symlinks_and_uses_posix_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            outside = root / "secret.txt"
            outside.write_text("do-not-export", encoding="utf-8")
            recovery = root / "pending-quarantine"
            recovery.mkdir()
            (recovery / "nested").mkdir()
            (recovery / "nested" / "safe.bin").write_bytes(b"safe")
            (recovery / "leak.bin").symlink_to(outside)
            output = root / "backup.json"
            result = export_data(
                database, output, include_recovery=True, allow_partial=True)
            self.assertFalse(result["complete"])
            archive = json.loads(output.read_text(encoding="utf-8"))
            paths = [item["path"] for item in archive["recovery_evidence"]]
            self.assertIn("pending-quarantine/nested/safe.bin", paths)
            self.assertTrue(any(item.get("omitted") == "unsafe_file_type"
                                for item in archive["recovery_evidence"]))
            self.assertNotIn("do-not-export", output.read_text(encoding="utf-8"))

    def test_manifest_counts_and_evidence_metadata_are_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            archive_path = root / "backup.json"
            export_data(database, archive_path)
            _rehash_archive(
                archive_path,
                lambda archive: archive["manifest"]["table_counts"].__setitem__(
                    "profiles", 99))
            with self.assertRaises(StoreError) as raised:
                preview_import(database, archive_path)
            self.assertEqual(raised.exception.code, "invalid_archive")

            export_data(database, archive_path, force=True)

            def add_bad_evidence(archive):
                archive["recovery_evidence"] = [{
                    "path": "evidence.bin", "size": 9,
                    "sha256": hashlib.sha256(b"x").hexdigest(),
                    "content_base64": "eA==",
                }]
                recovery = archive["manifest"]["recovery"]
                recovery.update({"count": 1, "source_count": 1,
                                 "included_count": 1, "omitted_count": 0,
                                 "complete": True})

            _rehash_archive(archive_path, add_bad_evidence)
            preview = preview_import(database, archive_path)
            self.assertFalse(preview["ok"])
            self.assertTrue(any("recovery evidence" in error
                                for error in preview["errors"]))

    def test_schema_version_comes_from_export_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            with patch.object(
                    LocalGameStore, "schema_version",
                    side_effect=AssertionError("outside snapshot")):
                result = export_data(database, root / "backup.json")
            self.assertTrue(result["ok"])

    def test_cleanup_requires_complete_archive_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            backup = root / "games.db.backup-old"
            backup.write_bytes(b"recovery-copy")
            os.utime(backup, (1, 1))
            plan = cleanup_recovery_data(database, older_than_days=0)
            candidate = next(item for item in plan["candidates"]
                             if item["path"] == backup.name)
            self.assertFalse(candidate["eligible"])
            self.assertEqual(candidate["reason"], "archive_proof_not_supplied")
            with self.assertRaises(StoreError) as raised:
                cleanup_recovery_data(
                    database, older_than_days=0, apply=True)
            self.assertEqual(raised.exception.code, "cleanup_archive_required")
            archive = root / "backup.json"
            export_data(database, archive, include_recovery=True)
            result = cleanup_recovery_data(
                database, older_than_days=0,
                archive_path=archive, apply=True)
            self.assertIn(backup.name, result["removed"])
            self.assertFalse(backup.exists())

    def test_replace_removes_unknown_table_and_active_protocol_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "target.db"
            LocalGameStore(source)
            archive = root / "backup.json"
            export_data(source, archive)
            target_store = LocalGameStore(target)
            with target_store.connection() as connection:
                connection.execute("CREATE TABLE obsolete(secret TEXT)")
                connection.execute("INSERT INTO obsolete VALUES('old')")
                connection.commit()
            state = root / "pending-state"
            state.mkdir()
            (state / ".reject-old.tmp").write_bytes(b"partial")
            (state / ".old.restore").write_bytes(b"old")
            (root / "pending_saves.json").write_text("[]", encoding="utf-8")
            result = restore_replace_data(target, archive)
            self.assertTrue(result["ok"])
            with target_store.connection() as connection:
                names = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("obsolete", names)
            self.assertFalse((state / ".reject-old.tmp").exists())
            self.assertFalse((state / ".old.restore").exists())
            self.assertFalse((root / "pending_saves.json").exists())


class StateAndGameTests(unittest.TestCase):
    def test_same_state_order_with_different_payload_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "pending-state")
            outbox.put(
                f"setting:{PROFILE_ID}:music", "set_setting",
                (PROFILE_ID, "music", True),
                logical_revision=7, operation_id="same-op", updated_at=1.0)
            with self.assertRaises(StoreError) as raised:
                outbox.put(
                    f"setting:{PROFILE_ID}:music", "set_setting",
                    (PROFILE_ID, "music", False),
                    logical_revision=7, operation_id="same-op", updated_at=1.0)
            self.assertEqual(raised.exception.code, "state_operation_conflict")

    def test_corrupt_reject_marker_is_quarantined_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "pending-state"
            state.mkdir()
            marker = state / ".reject-bad.txn"
            marker.write_text('{"version":2,"key":"x"}', encoding="utf-8")
            outbox = PersistentStateOutbox(state)
            self.assertFalse(marker.exists())
            self.assertGreaterEqual(outbox.quarantined_count, 1)
            self.assertTrue(any(outbox.quarantine_path.iterdir()))

    def test_release_intent_wins_an_older_inflight_save(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(
                db_path=Path(directory) / "games.db",
                outbox_path=Path(directory) / "pending")
            try:
                backend.ensure_profile_async("player", PROFILE_ID).result(5)
                entered = threading.Event()
                resume = threading.Event()
                original_write = backend._write_store_method

                def delayed_write(method, *args):
                    if (method == "apply_state_operation"
                            and args[0]["method"] == "save_slot"
                            and args[0]["args"][3]["owner_status"] == "active"):
                        entered.set()
                        self.assertTrue(resume.wait(5))
                    return original_write(method, *args)

                backend._write_store_method = delayed_write
                base = {
                    "version": 5, "game_state": "playing", "score": 8,
                    "won": False, "won_announced": False,
                    "attempt_uuid": "attempt-1234567890",
                    "attempt_revision": 0, "confirmed_score": None,
                    "owner_token": "owner-token-123456",
                    "owner_epoch": 0,
                    "expected_owner_token": None,
                    "expected_owner_epoch": None,
                    "expected_slot_revision": None,
                    "expected_value_hash": None,
                    "grid": [[2, 0, 0, 0], [0, 0, 0, 0],
                             [0, 0, 0, 0], [0, 0, 0, 4]],
                }
                active = {**base, "owner_status": "active",
                          "slot_revision": 1}
                backend.save_slot_async(
                    PROFILE_ID, "2048", "autosave", active)
                self.assertTrue(entered.wait(5))
                released = {**base, "owner_status": "released",
                            "slot_revision": 2}
                with patch(
                        "game_service.local_backend.maintenance_lock",
                        side_effect=AssertionError(
                            "slot intent must use the application lease")):
                    result = backend.publish_slot_intent(
                        PROFILE_ID, "2048", "autosave", released)
                self.assertTrue(result["durable_pending"])
                resume.set()
                self.assertTrue(backend.drain(5))
                saved = backend.load_slot_async(
                    PROFILE_ID, "2048", "autosave").result(5)
                self.assertEqual(saved["state"]["owner_status"], "released")
                self.assertEqual(saved["state"]["slot_revision"], 2)
            finally:
                resume.set()
                backend.close()

    def test_flask_adapter_holds_application_lease(self):
        try:
            from server.app import create_app
        except ImportError:
            self.skipTest("optional Flask dependency is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "server.db"
            app = create_app({"TESTING": True, "DB_PATH": str(database),
                              "APPLICATION_LEASE": True})
            try:
                with self.assertRaises(MaintenanceBusyError):
                    with inactive_application_lock(database, timeout=0):
                        pass
            finally:
                app.extensions["application_session"].close()

    def test_2048_blocks_input_until_claim_ack(self):
        import pygame

        from client.games.game_2048 import Game2048
        from game_service.catalog import GAME_BY_ID
        from game_service.service import SlotLoadResult, SlotLoadStatus

        pygame.init()

        class Backend:
            def __init__(self):
                self.claim = Future()
                self.claim_state = None
                self.saved = {
                    "state": {
                        "version": 5, "game_state": "playing", "score": 0,
                        "won": False, "won_announced": False,
                        "attempt_uuid": "attempt-1234567890",
                        "attempt_revision": 0, "slot_revision": 1,
                        "confirmed_score": None,
                        "owner_token": "released-owner-1234",
                        "owner_status": "released", "owner_epoch": 0,
                        "expected_owner_token": None,
                        "expected_owner_epoch": None,
                        "expected_slot_revision": None,
                        "expected_value_hash": None,
                        "grid": [[2, 0, 0, 0], [0, 0, 0, 0],
                                 [0, 0, 0, 0], [0, 0, 0, 4]],
                    },
                    "ruleset_version": GAME_BY_ID["2048"].ruleset_version,
                    "value_hash": "a" * 64,
                }

            def load_slot_async(self, *_args):
                future = Future()
                future.set_result(SlotLoadResult(
                    SlotLoadStatus.LOADED, slot=self.saved))
                return future

            def save_slot_async(self, *_args):
                self.claim_state = _args[3]
                return self.claim

            def failed_save_count(self):
                return 0

        backend = Backend()
        game = Game2048(backend=backend, profile_id=PROFILE_ID)
        game._poll_slot_load()
        self.assertEqual(game.slot_load_state, "claiming")
        before = [[game.grid[row][col].value if game.grid[row][col] else 0
                   for col in range(4)] for row in range(4)]
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_LEFT))
        after = [[game.grid[row][col].value if game.grid[row][col] else 0
                  for col in range(4)] for row in range(4)]
        self.assertEqual(after, before)
        backend.claim.set_result({
            "ok": True, "state_apply": "committed",
            "value": backend.claim_state})
        game._poll_slot_save()
        self.assertEqual(game.slot_load_state, "ready")
        pygame.display.quit()

    def test_tetris_seven_bag_hold_and_ghost(self):
        import pygame

        from client.games.tetris import SHAPE_KEYS, Tetris

        pygame.init()

        class Backend:
            def failed_save_count(self):
                return 0

        game = Tetris(backend=Backend(), rng=random.Random(7))
        sequence = [game.piece.kind]
        for _ in range(13):
            game._spawn()
            sequence.append(game.piece.kind)
        self.assertEqual(set(sequence[:7]), set(SHAPE_KEYS))
        self.assertEqual(set(sequence[7:14]), set(SHAPE_KEYS))

        game.reset()
        first = game.piece.kind
        expected = game.next_kind
        game._hold_piece()
        self.assertEqual(game.held_kind, first)
        self.assertEqual(game.piece.kind, expected)
        unchanged = game.piece.kind
        game._hold_piece()
        self.assertEqual(game.piece.kind, unchanged)
        ghost = game._ghost_cells()
        self.assertEqual(max(y for _x, y in ghost), 19)
        pygame.display.quit()

    def test_sokoban_selector_only_opens_unlocked_practice_levels(self):
        import pygame

        from client.games.sokoban import Sokoban

        pygame.init()

        class Backend:
            def failed_save_count(self):
                return 0

        game = Sokoban(backend=Backend())
        game.unlocked_level = 3
        self.assertTrue(game._select_practice_level(2))
        self.assertEqual(game.level_idx, 2)
        self.assertTrue(game.practice_mode)
        self.assertFalse(game._select_practice_level(3))
        self.assertEqual(game.level_idx, 2)
        pygame.display.quit()


if __name__ == "__main__":
    unittest.main()
