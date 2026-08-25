"""Regression coverage added while resolving the fourteenth review."""

from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from game_service.data_cli import (cleanup_recovery_data, export_data,
                                   import_data, inspect_data, preview_import,
                                   restore_replace_data, upgrade_archive)
from game_service.import_transaction import (FileOperation, ImportTransaction,
                                             recover_import_transactions)
from game_service.local_backend import (PersistentStateOutbox,
                                        _read_regular_nofollow)
from game_service.maintenance import (ApplicationSession, MaintenanceBusyError,
                                      _open_control_file,
                                      inactive_application_lock,
                                      recovered_application_session)
from game_service.mutation import canonical_json, normalize_score_mutation
from game_service.service import LocalStateEvent, SaveState
from game_service.store import LocalGameStore, StoreError
from game_service.version import __version__


PROFILE_ID = "1234567890abcdef1234567890abcdef"


def _completed(value):
    future = Future()
    future.set_result(value)
    return future


def _rewrite_archive(path: Path, mutate) -> dict:
    archive = json.loads(path.read_text(encoding="utf-8"))
    mutate(archive)
    payload = {key: value for key, value in archive.items()
               if key != "manifest_hash"}
    archive["manifest_hash"] = hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()
    path.write_text(canonical_json(archive), encoding="utf-8")
    return archive


class StartupHandoffTests(unittest.TestCase):
    def test_recovery_handoff_does_not_reacquire_after_unlock(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            with patch.object(
                    ApplicationSession, "acquire",
                    side_effect=AssertionError("unlock/reacquire window")):
                session = recovered_application_session(database)
            try:
                with self.assertRaises(MaintenanceBusyError):
                    with inactive_application_lock(database, timeout=0):
                        pass
            finally:
                session.close()

    def test_multiple_unfinished_transactions_require_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            ImportTransaction.prepare(database, [
                FileOperation(root / "pending" / "one.json", b"one")])
            ImportTransaction.prepare(database, [
                FileOperation(root / "pending" / "two.json", b"two")])
            with self.assertRaises(StoreError) as raised:
                recover_import_transactions(database)
            self.assertEqual(raised.exception.code, "import_recovery_required")
            self.assertEqual(len(list(root.glob(".games.db.import-*"))), 2)

    def test_legacy_transaction_requires_explicit_manual_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            transaction = ImportTransaction.prepare(database, [
                FileOperation(root / "pending" / "legacy.json", b"new")])
            transaction.journal["version"] = 1
            transaction.journal.pop("database_before")
            transaction.journal["operations"] = [{
                key: value for key, value in record.items()
                if key in {"target", "staged", "before", "had_before"}
            } for record in transaction.journal["operations"]]
            transaction._write_journal()
            with self.assertRaises(StoreError) as raised:
                recover_import_transactions(database)
            self.assertEqual(
                raised.exception.code, "legacy_import_recovery_required")
            recovered = recover_import_transactions(
                database, allow_legacy_v1=True)
            self.assertEqual(recovered, [transaction.root.name])

    def test_transaction_uses_the_bytes_it_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            target = root / "pending" / "state.json"
            target.parent.mkdir()
            target.write_bytes(b"old")
            transaction = ImportTransaction.prepare(
                database, [FileOperation(target, b"new")])
            with patch.object(
                    Path, "read_bytes",
                    side_effect=AssertionError("verified path was reread")):
                transaction.publish_files()
                self.assertEqual(
                    _read_regular_nofollow(target, 1024), b"new")
                transaction.rollback()
            self.assertEqual(target.read_bytes(), b"old")
            transaction.finish()


class RejectTransactionTests(unittest.TestCase):
    def _outbox_with_two_values(self, root: Path):
        outbox = PersistentStateOutbox(root / "pending-state")
        key = f"setting:{PROFILE_ID}:volume"
        old = outbox.put(
            key, "set_setting", (PROFILE_ID, "volume", 0.2),
            logical_revision=1, operation_id="old")
        new = outbox.put(
            key, "set_setting", (PROFILE_ID, "volume", 0.8),
            logical_revision=2, operation_id="new")
        return outbox, key, old, new

    def test_previous_is_durable_before_incoming_replaces_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outbox = PersistentStateOutbox(root / "pending-state")
            key = f"setting:{PROFILE_ID}:volume"
            old = outbox.put(
                key, "set_setting", (PROFILE_ID, "volume", 0.2),
                logical_revision=1, operation_id="old")
            original_replace = os.replace

            def fail_canonical(source, target):
                if Path(target) == outbox._target(key):
                    raise OSError("crash before incoming publish")
                return original_replace(source, target)

            with patch("game_service.local_backend.os.replace", fail_canonical):
                with self.assertRaises(OSError):
                    outbox.put(
                        key, "set_setting", (PROFILE_ID, "volume", 0.8),
                        logical_revision=2, operation_id="new")
            reopened = PersistentStateOutbox(root / "pending-state")
            self.assertEqual(
                reopened.read_key(key)["payload_hash"], old["payload_hash"])
            self.assertFalse(list(reopened.path.glob(".reject-*.txn")))

    def test_valid_rejected_temp_is_promoted_and_restores_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outbox, key, old, new = self._outbox_with_two_values(root)
            marker = outbox._reject_marker_path(key, new["payload_hash"])
            transaction = outbox._reject_transaction(
                key, new["payload_hash"], old["operation"],
                phase="rejected", reason="slot-in-use")
            marker.with_suffix(".tmp").write_text(
                canonical_json(transaction), encoding="utf-8")
            marker.unlink()
            reopened = PersistentStateOutbox(root / "pending-state")
            self.assertEqual(
                reopened.read_key(key)["payload_hash"], old["payload_hash"])
            self.assertFalse(marker.exists())

    def test_partial_reject_temp_and_invalid_restore_are_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending-state"
            path.mkdir()
            (path / ".reject-bad.tmp").write_bytes(b"{")
            (path / ".old.restore").write_bytes(b"not-json")
            outbox = PersistentStateOutbox(path)
            self.assertFalse(list(path.glob(".reject-*.tmp")))
            self.assertFalse(list(path.glob(".*.restore")))
            self.assertGreaterEqual(outbox.quarantined_count, 2)

    def test_replay_can_reject_prepared_marker_without_in_memory_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outbox, key, old, new = self._outbox_with_two_values(root)
            reopened = PersistentStateOutbox(root / "pending-state")
            self.assertTrue(reopened.reject_and_restore_if_current(
                key, new["payload_hash"], None, "slot_in_use"))
            self.assertEqual(
                reopened.read_key(key)["payload_hash"], old["payload_hash"])


class OwnerClaimTests(unittest.TestCase):
    class Backend:
        pending_saves_are_durable = True

        def __init__(self):
            self.first_load = _completed(None)
            self.reload = Future()
            self.loads = 0
            self.claim = Future()
            self.claim_state = None
            self.releases = []
            self.event = None

        def load_slot_async(self, *_args):
            self.loads += 1
            return self.first_load if self.loads == 1 else self.reload

        def save_slot_async(self, *_args):
            self.claim_state = _args[3]
            return self.claim

        def publish_slot_intent(self, *_args):
            self.releases.append(_args[3])
            return {"ok": True, "durable_pending": True}

        def get_local_state_status(self, _key):
            return self.event

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

    def test_superseded_claim_never_enters_ready_or_accepts_move(self):
        import pygame

        from client.games.game_2048 import Game2048

        backend = self.Backend()
        game = Game2048(backend=backend, profile_id=PROFILE_ID)
        game._poll_slot_load()
        before = [[game.grid[row][col].value if game.grid[row][col] else 0
                   for col in range(4)] for row in range(4)]
        backend.claim.set_result({
            "ok": True, "superseded": True,
            "state_apply": "superseded"})
        game._poll_slot_save()
        self.assertEqual(game.slot_load_state, "loading")
        game.handle_event(pygame.event.Event(
            pygame.KEYDOWN, key=pygame.K_LEFT))
        after = [[game.grid[row][col].value if game.grid[row][col] else 0
                  for col in range(4)] for row in range(4)]
        self.assertEqual(after, before)

    def test_recovery_required_claim_has_an_explicit_blocked_state(self):
        from client.games.game_2048 import Game2048

        backend = self.Backend()
        game = Game2048(backend=backend, profile_id=PROFILE_ID)
        game._poll_slot_load()
        backend.claim.set_result({
            "ok": False, "durable_pending": True,
            "error": "pending"})
        game._poll_slot_save()
        backend.event = LocalStateEvent(
            key=f"slot:{PROFILE_ID}:2048:autosave", kind="slot",
            logical_revision=1, state=SaveState.RECOVERY_REQUIRED,
            result={"ok": False})
        game._poll_slot_save_status()
        self.assertEqual(game.slot_load_state, "failed")
        self.assertIn("恢复", game.slot_save_error)

    def test_close_during_claim_publishes_newer_release_intent(self):
        from client.games.game_2048 import Game2048

        backend = self.Backend()
        game = Game2048(backend=backend, profile_id=PROFILE_ID)
        game._poll_slot_load()
        self.assertEqual(game.slot_load_state, "claiming")
        game.before_close()
        self.assertEqual(backend.releases[-1]["owner_status"], "released")
        self.assertGreater(
            backend.releases[-1]["slot_revision"],
            backend.claim_state["slot_revision"])


class ArchiveCompatibilityTests(unittest.TestCase):
    def test_archive_v3_records_package_and_reader_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            archive = root / "archive.json"
            LocalGameStore(database)
            export_data(database, archive)
            value = json.loads(archive.read_text(encoding="utf-8"))
            self.assertEqual(value["archive_version"], 3)
            self.assertEqual(
                value["manifest"]["application"]["version"], __version__)
            self.assertEqual(value["manifest"]["reader"]["min_version"], 3)

    def test_export_no_clobber_falls_back_without_hard_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            archive = root / "archive.json"
            LocalGameStore(database)
            with patch("game_service.data_cli.os.link", side_effect=OSError):
                export_data(database, archive)
            self.assertEqual(
                json.loads(archive.read_text(encoding="utf-8"))[
                    "archive_version"], 3)

    def test_cleanup_detects_a_file_added_after_fingerprinting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            recovery = root / "pending-quarantine"
            recovery.mkdir()
            evidence = recovery / "old.json"
            evidence.write_text("preserve", encoding="utf-8")
            os.utime(evidence, (1, 1))
            os.utime(recovery, (1, 1))
            LocalGameStore(database)
            archive = root / "archive.json"
            export_data(database, archive, include_recovery=True)
            from game_service import data_cli

            original = data_cli._read_regular_nofollow
            injected = False

            def add_after_first_read(path, limit):
                nonlocal injected
                raw = original(path, limit)
                if Path(path).resolve() == evidence.resolve() and not injected:
                    injected = True
                    (recovery / "appeared.json").write_text(
                        "new", encoding="utf-8")
                return raw

            with patch(
                    "game_service.data_cli._read_regular_nofollow",
                    side_effect=add_after_first_read):
                with self.assertRaises(StoreError) as raised:
                    cleanup_recovery_data(
                        database, older_than_days=0,
                        archive_path=archive, apply=True)
            self.assertEqual(raised.exception.code, "cleanup_target_changed")
            self.assertTrue(evidence.exists())

    def test_format2_archive_upgrades_for_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_db = root / "source.db"
            target_db = root / "target.db"
            old = root / "old.json"
            upgraded = root / "upgraded.json"
            source = LocalGameStore(source_db)
            source.ensure_profile("player", PROFILE_ID)
            LocalGameStore(target_db)
            export_data(source_db, old)

            def downgrade(value):
                value["archive_version"] = 2
                value["manifest"]["format_version"] = 2
                value["manifest"].pop("reader")
                value["manifest"]["application"].pop("version")

            _rewrite_archive(old, downgrade)
            result = upgrade_archive(target_db, old, upgraded)
            self.assertTrue(result["replace_eligible"])
            restored = restore_replace_data(target_db, upgraded)
            self.assertTrue(restored["ok"])
            self.assertEqual(
                LocalGameStore(target_db).last_profile()["profile_id"],
                PROFILE_ID)

    def test_formatless_v2_upgrade_reports_unprovable_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            old = root / "old.json"
            upgraded = root / "upgraded.json"
            LocalGameStore(database)
            export_data(database, old)

            def downgrade(value):
                value["archive_version"] = 2
                value["manifest"].pop("format_version")
                value["manifest"].pop("reader")
                value["manifest"]["application"].pop("version")
                value["manifest"]["pending"].pop("transactions")

            _rewrite_archive(old, downgrade)
            result = upgrade_archive(database, old, upgraded)
            self.assertFalse(result["replace_eligible"])
            with self.assertRaises(StoreError) as raised:
                restore_replace_data(database, upgraded)
            self.assertEqual(raised.exception.code, "incomplete_archive")

    def test_historical_ruleset_rows_remain_importable_and_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_db = root / "source.db"
            target_db = root / "target.db"
            archive = root / "archive.json"
            source = LocalGameStore(source_db)
            source.ensure_profile("player", PROFILE_ID)
            source.record_mutation(normalize_score_mutation(
                "tetris", "player", 123, profile_id=PROFILE_ID,
                ruleset_version="tetris-assist-2",
                request_id="historical-request-0001",
                attempt_uuid="historical-attempt-0001"))
            LocalGameStore(target_db)
            export_data(source_db, archive)
            _rewrite_archive(
                archive,
                lambda value: value["manifest"]["application"]["rulesets"]
                .__setitem__("tetris", "tetris-assist-2"))
            self.assertTrue(preview_import(target_db, archive)["ok"])
            import_data(target_db, archive)
            target = LocalGameStore(target_db)
            self.assertEqual(target.leaderboard("tetris"), [])
            self.assertEqual(len(target.leaderboard(
                "tetris", ruleset_version="tetris-assist-2")), 1)

    def test_replace_removes_unknown_schema_and_migrated_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_db = root / "source.db"
            target_db = root / "target.db"
            archive = root / "archive.json"
            LocalGameStore(source_db)
            export_data(source_db, archive)
            LocalGameStore(target_db)
            connection = sqlite3.connect(target_db)
            try:
                connection.executescript(
                    "CREATE TABLE stray(value INTEGER);"
                    "CREATE INDEX stray_index ON stray(value);"
                    "CREATE VIEW stray_view AS SELECT value FROM stray;"
                    "CREATE TRIGGER stray_trigger AFTER INSERT ON profiles "
                    "BEGIN INSERT INTO stray VALUES(1); END;")
                connection.commit()
            finally:
                connection.close()
            migrated = root / "pending_saves.json.migrated-old"
            migrated.write_text("personal data", encoding="utf-8")
            restore_replace_data(target_db, archive)
            connection = sqlite3.connect(target_db)
            try:
                names = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE name LIKE 'stray%'")}
            finally:
                connection.close()
            self.assertEqual(names, set())
            self.assertFalse(migrated.exists())


class FilesystemAndGameTests(unittest.TestCase):
    def test_windows_reparse_control_file_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows reparse-point check")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            alias = root / "control.lock"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                capture_output=True, text=True, check=False)
            if created.returncode != 0:
                self.skipTest("junction creation is unavailable")
            with self.assertRaises(MaintenanceBusyError):
                _open_control_file(alias)

    def test_pending_root_symlink_is_rejected(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            alias = root / "pending-state"
            alias.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(StoreError) as raised:
                PersistentStateOutbox(alias)
            self.assertEqual(raised.exception.code, "unsafe_data_directory")

    def test_migrated_legacy_score_file_is_inventory_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            evidence = root / "pending_saves.json.migrated-old"
            evidence.write_text("[]", encoding="utf-8")
            status = inspect_data(database)
            sources = {item["source"] for item in status["recovery"]}
            self.assertIn("legacy_score_migration", sources)

    def test_server_init_helper_holds_application_lease(self):
        try:
            from server.app import init_db
        except ImportError:
            self.skipTest("optional Flask dependency is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            store = init_db(database)
            try:
                with self.assertRaises(MaintenanceBusyError):
                    with inactive_application_lock(database, timeout=0):
                        pass
            finally:
                store._application_session.close()

    @classmethod
    def setUpClass(cls):
        import pygame

        pygame.init()

    @classmethod
    def tearDownClass(cls):
        import pygame

        pygame.display.quit()

    class SokobanBackend:
        pending_saves_are_durable = True

        def __init__(self):
            self.progress = []

        def merge_progress_async(self, *_args):
            self.progress.append(_args)
            return _completed({"ok": True, "value": _args[3]})

        def failed_save_count(self):
            return 0

    def test_sokoban_practice_does_not_change_campaign_ledger_or_unlock(self):
        from client.games.sokoban import Sokoban

        backend = self.SokobanBackend()
        game = Sokoban(backend=backend, profile_id=PROFILE_ID)
        game.unlocked_level = 2
        game.completed_levels = {0}
        game.level_scores = {0: 900}
        game.total_score = 900
        self.assertTrue(game._select_practice_level(1))
        game.boxes = set(game.targets)
        game._check_win()
        self.assertEqual(game.unlocked_level, 2)
        self.assertEqual(game.completed_levels, {0})
        self.assertEqual(game.level_scores, {0: 900})
        self.assertEqual(game.total_score, 900)
        self.assertEqual(backend.progress[-1][2], "practice")

    def test_sokoban_selector_is_gated_and_level_one_preserves_run(self):
        from client.games.sokoban import Sokoban

        game = Sokoban(backend=self.SokobanBackend(), profile_id=PROFILE_ID)
        game.unlocked_level = 3
        game.completed_levels = {0, 1}
        game.level_scores = {0: 900, 1: 800}
        game.total_score = 1700
        game.state = "paused"
        self.assertFalse(game._select_practice_level(2))
        self.assertEqual(game.level_idx, 0)
        game.state = "playing"
        self.assertTrue(game._select_practice_level(0))
        self.assertTrue(game.practice_mode)
        self.assertEqual(game.completed_levels, {0, 1})
        self.assertEqual(game.total_score, 1700)

    def test_practice_write_does_not_discard_pending_campaign_load(self):
        from client.games.sokoban import Sokoban

        class Backend(self.SokobanBackend):
            def __init__(self):
                super().__init__()
                self.loads = {"campaign": Future(), "practice": Future()}

            def get_progress_async(self, _profile, _game, key, _default):
                return self.loads[key]

        backend = Backend()
        game = Sokoban(backend=backend, profile_id=PROFILE_ID)
        self.assertTrue(game._select_practice_level(0))
        game.boxes = set(game.targets)
        game._check_win()
        backend.loads["campaign"].set_result({"unlocked_level": 4})
        game._poll_progress()
        self.assertEqual(game.unlocked_level, 4)

    def test_tetris_ten_thousand_bags_are_complete_and_seeded(self):
        from client.games.tetris import SHAPE_KEYS, Tetris

        game = Tetris(
            backend=self.SokobanBackend(), profile_id=PROFILE_ID,
            rng=random.Random(20260825))
        game._piece_bag = []
        draws = [game._draw_bag_kind() for _ in range(70_000)]
        expected = set(SHAPE_KEYS)
        self.assertTrue(all(
            set(draws[index:index + 7]) == expected
            for index in range(0, len(draws), 7)))

    def test_tetris_ghost_matches_hard_drop_landing(self):
        from client.games.tetris import Tetris

        game = Tetris(
            backend=self.SokobanBackend(), profile_id=PROFILE_ID,
            rng=random.Random(7))
        ghost = game._ghost_cells()
        kind = game.piece.kind
        game._hard_drop()
        self.assertTrue(all(
            y < 0 or game.board[y][x] == kind for x, y in ghost))
