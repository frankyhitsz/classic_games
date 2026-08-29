"""Seventeenth-review protocol and settled-state regression checks."""

from __future__ import annotations

import os
import random
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from game_service.catalog import GAME_BY_ID
from game_service.data_cli import (MAX_JSON_NODES, _active_protocol_report,
                                   _load_archive, _parser,
                                   _planned_file_operations,
                                   cleanup_score_locks,
                                   cleanup_recovery_data, export_data,
                                   import_data, inspect_archive, verify_archive)
from game_service.import_transaction import (ImportTransaction,
                                             MAX_TRANSACTION_OPERATIONS,
                                             has_import_transaction_roots)
from game_service.local_backend import (LocalBackendClient, PendingSaveEnvelope,
                                        PersistentSaveOutbox,
                                        PersistentStateOutbox)
from game_service.maintenance import (MaintenanceBusyError,
                                      inactive_application_lock)
from game_service.mutation import canonical_json, normalize_score_mutation
from game_service.save_slot_validation import validate_2048_state
from game_service.service import LocalStateEvent, SaveState
from game_service.store import LocalGameStore, StoreError

PROFILE_ID = "7" * 32


def completed(value):
    future = Future()
    future.set_result(value)
    return future


class ProgressAggregateTests(unittest.TestCase):
    def setUp(self):
        self.ruleset = GAME_BY_ID["sokoban"].ruleset_version
        self.key = f"progress:{PROFILE_ID}:sokoban:{self.ruleset}:campaign"

    def operation(self, method, value, revision, operation_id):
        return PersistentStateOutbox._operation(
            self.key, method,
            (PROFILE_ID, "sokoban", "campaign", value, self.ruleset),
            revision, operation_id)

    def test_absorbed_set_replay_is_duplicate_and_hash_conflict_fails(self):
        baseline = self.operation(
            "set_progress", {"unlocked_level": 3}, 10, "f-component")
        delta = self.operation(
            "merge_progress", {"unlocked_level": 8}, 5, "delta")
        aggregate, _resolution = PersistentStateOutbox.resolve_operations(
            baseline, delta)
        winner, resolution = PersistentStateOutbox.resolve_operations(
            aggregate, baseline)
        self.assertEqual(resolution, "duplicate")
        self.assertEqual(winner["payload_hash"], aggregate["payload_hash"])
        conflicting = self.operation(
            "set_progress", {"unlocked_level": 4}, 10, "f-component")
        with self.assertRaisesRegex(StoreError, "reused"):
            PersistentStateOutbox.resolve_operations(aggregate, conflicting)

    def test_store_rejects_set_already_absorbed_by_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            profile = PersistentStateOutbox._operation(
                f"profile:{PROFILE_ID}", "ensure_profile",
                ("player", PROFILE_ID), 1, "profile")
            store.apply_state_operation(profile)
            baseline = self.operation(
                "set_progress", {"unlocked_level": 3}, 10, "f-component")
            delta = self.operation(
                "merge_progress", {"unlocked_level": 8}, 5, "delta")
            aggregate, _resolution = PersistentStateOutbox.resolve_operations(
                baseline, delta)
            store.apply_state_operation(aggregate)
            replay = store.apply_state_operation(baseline)
            self.assertTrue(replay["duplicate_operation"])
            self.assertTrue(replay["absorbed_component"])
            value = store.get_progress(
                PROFILE_ID, "sokoban", "campaign", {})
            self.assertEqual(value["unlocked_level"], 8)
            with store.connection() as connection:
                connection.execute(
                    "UPDATE state_merge_receipts SET applied_at=1")
                connection.commit()
            store.maintenance()
            with store.connection() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM state_merge_receipts"
                ).fetchone()[0], 2)

    def test_legacy_upgrade_uses_method_matrix_instead_of_forced_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            outbox.path.mkdir(exist_ok=True)
            existing = self.operation(
                "set_progress", {"unlocked_level": 7}, 20, "newer")
            target = outbox._target(self.key)
            outbox._rewrite_locked(target, existing)
            legacy = self.operation(
                "set_progress", {"unlocked_level": 2}, 10, "legacy")
            source = outbox.path / "legacy.json"
            raw = canonical_json(legacy).encode()
            PersistentSaveOutbox._write_bytes(source, raw)
            winner = outbox._upgrade_v1_locked(source, raw, legacy)
            self.assertEqual(winner["method"], "set_progress")
            self.assertEqual(winner["payload_hash"], existing["payload_hash"])


class StateBusyAndEventTests(unittest.TestCase):
    def test_list_entries_lock_timeout_preserves_valid_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            receipt = outbox.put(
                f"setting:{PROFILE_ID}:volume", "set_setting",
                (PROFILE_ID, "volume", 0.5), logical_revision=1,
                operation_id="volume")
            target = outbox._target(receipt["operation"]["key"])

            def busy(_digest):
                from contextlib import contextmanager

                @contextmanager
                def context():
                    raise StoreError(
                        "state_lock_timeout", "busy", 503, retryable=True)
                    yield

                return context()

            with patch.object(outbox, "_digest_lock", side_effect=busy):
                self.assertEqual(outbox.list_entries(), [])
            self.assertTrue(target.is_file())
            self.assertFalse(outbox.quarantine_path.exists())

    def test_reject_marker_lock_timeout_is_busy_not_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            outbox.path.mkdir(exist_ok=True)
            key = f"setting:{PROFILE_ID}:music"
            operation = outbox._operation(
                key, "set_setting", (PROFILE_ID, "music", True), 1, "music")
            marker = outbox._reject_marker_path(key, operation["payload_hash"])
            transaction = outbox._reject_transaction(
                key, operation["payload_hash"], None,
                phase="prepared", reason="test")
            outbox._write_reject_marker_locked(marker, transaction)
            with patch.object(
                    outbox, "_key_lock",
                    side_effect=StoreError(
                        "state_lock_timeout", "busy", 503, retryable=True)):
                outbox._recover_reject_transactions()
            self.assertTrue(marker.is_file())

    def test_restore_lock_timeout_preserves_restore_file(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            key = f"setting:{PROFILE_ID}:restore"
            operation = outbox._operation(
                key, "set_setting", (PROFILE_ID, "restore", True), 1,
                "restore")
            outbox.path.mkdir(exist_ok=True)
            restore = outbox.path / ".legacy.restore"
            restore.write_bytes(canonical_json(operation).encode())
            with patch.object(
                    outbox, "_key_lock",
                    side_effect=StoreError(
                        "state_lock_timeout", "busy", 503, retryable=True)):
                outbox._recover_reject_transactions()
            self.assertTrue(restore.is_file())

    def test_reject_writer_uses_unique_temporary_names(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            outbox.path.mkdir(exist_ok=True)
            key = f"setting:{PROFILE_ID}:marker"
            transaction = outbox._reject_transaction(
                key, "a" * 64, None, phase="prepared", reason="test")
            marker = outbox._reject_marker_path(key, "a" * 64)
            names = []
            original = PersistentSaveOutbox._write_bytes

            def capture(path, data):
                names.append(Path(path).name)
                return original(path, data)

            with patch.object(PersistentSaveOutbox, "_write_bytes",
                              side_effect=capture):
                outbox._write_reject_marker_locked(marker, transaction)
                outbox._write_reject_marker_locked(marker, transaction)
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(all(name.startswith(".reject-")
                                and name.endswith(".tmp") for name in names))

    def test_clock_quarantine_failure_does_not_overwrite_bad_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            outbox.path.mkdir(exist_ok=True)
            clock = outbox.path / ".state-clock"
            clock.write_bytes(b"bad")
            with patch.object(outbox, "_quarantine_clock_locked",
                              return_value=False):
                with self.assertRaises(StoreError) as raised:
                    outbox.next_revision()
            self.assertEqual(
                raised.exception.code, "state_clock_recovery_required")
            self.assertEqual(clock.read_bytes(), b"bad")

    def test_parser_validity_does_not_depend_on_current_clock(self):
        outbox = PersistentStateOutbox
        key = f"setting:{PROFILE_ID}:clock"
        future = time.time() + 10 * 24 * 60 * 60
        operation = outbox._operation(
            key, "set_setting", (PROFILE_ID, "clock", True), 1, "future",
            updated_at=future, allow_future=True)
        raw = canonical_json(operation).encode()
        with patch("game_service.local_backend.time.time", return_value=1.0):
            self.assertEqual(outbox._parse(raw), operation)

    def test_recovery_control_scans_share_one_total_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(
                Path(directory) / "state", recover=False)
            outbox.path.mkdir(exist_ok=True)
            (outbox.path / ".reject-placeholder.tmp").write_bytes(b"{}")
            outbox._recovery_scan_deadline = time.monotonic() - 1
            paths = outbox._bounded_control_paths(
                lambda name: name.endswith(".tmp"))
            self.assertEqual(paths, [])
            self.assertTrue(outbox._recovery_scan_limited)
            self.assertIn("扫描已达到", outbox.recovery_notice)

    def test_equal_identity_terminal_event_never_regresses(self):
        committed = LocalStateEvent(
            "key", "setting", 1, SaveState.COMMITTED,
            {"authoritative_receipt": True}, "operation", "a" * 64)
        pending = LocalStateEvent(
            "key", "setting", 1, SaveState.DURABLE_PENDING,
            {"reconstructed": True}, "operation", "a" * 64)
        self.assertFalse(LocalBackendClient._state_event_replaces(
            committed, pending))
        self.assertTrue(LocalBackendClient._state_event_replaces(
            pending, committed))
        superseded = LocalStateEvent(
            "key", "setting", 1, SaveState.SUPERSEDED, {},
            "operation", "a" * 64)
        self.assertTrue(LocalBackendClient._state_event_replaces(
            superseded, committed))
        self.assertFalse(LocalBackendClient._state_event_replaces(
            committed, superseded))


class ArchiveAndTransactionTests(unittest.TestCase):
    def test_upgrade_archive_help_names_current_v4_contract(self):
        help_text = " ".join(_parser().format_help().split())
        self.assertIn("v4 reader contract", help_text)

    def test_successful_export_self_reads_and_deep_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            archive = root / "backup.json"
            result = export_data(database, archive)
            self.assertLess(result["archive_budget"]["nodes"], MAX_JSON_NODES)
            self.assertEqual(_load_archive(archive)["manifest_hash"],
                             result["manifest_hash"])
            self.assertEqual(inspect_archive(archive)["validation"].split(",")[0],
                             "bounded structure")
            self.assertTrue(verify_archive(archive)["ok"])

    def test_export_over_node_budget_fails_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            store = LocalGameStore(database)
            with store.connection() as connection:
                connection.executemany(
                    "INSERT INTO invalid_attempts(original_id,reason,row_json,"
                    "quarantined_at) VALUES(?,?,?,?)",
                    ((index, "test", "{}", 1.0)
                     for index in range(30_000)))
                connection.commit()
            archive = root / "too-complex.json"
            with self.assertRaises(StoreError) as raised:
                export_data(database, archive)
            self.assertEqual(raised.exception.code, "archive_too_complex")
            self.assertFalse(archive.exists())

    @unittest.skipIf(os.name == "nt", "symlink setup differs on Windows")
    def test_inspect_rejects_final_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            archive = root / "backup.json"
            export_data(database, archive)
            link = root / "link.json"
            link.symlink_to(archive)
            with self.assertRaises(StoreError):
                inspect_archive(link)

    def test_terminal_cleanup_failure_leaves_no_active_import_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            transaction = ImportTransaction.prepare(database, [])
            active_root = transaction.root
            with patch("game_service.import_transaction.shutil.rmtree",
                       side_effect=OSError("locked")):
                transaction.finish()
            self.assertFalse(active_root.exists())
            cleanup_roots = list(root.glob(
                f".{database.name}.transaction-cleanup-*"))
            self.assertEqual(len(cleanup_roots), 1)
            self.assertFalse(has_import_transaction_roots(database))
            self.assertTrue(_active_protocol_report(database)["complete"])

    def test_transaction_writer_refuses_unreadable_operation_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            transaction = ImportTransaction.prepare(database, [])
            transaction.journal["operations"] = [
                {} for _ in range(MAX_TRANSACTION_OPERATIONS + 1)]
            with self.assertRaises(StoreError) as raised:
                transaction._write_journal()
            self.assertEqual(
                raised.exception.code, "import_transaction_too_large")

    def test_large_recovery_uses_hash_only_cleanup_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            evidence = root / "games.db.backup-large"
            evidence.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
            os.utime(evidence, (1, 1))
            archive = root / "backup.json"
            result = export_data(database, archive, include_recovery=True)
            self.assertTrue(result["complete"])
            value = _load_archive(archive)
            item = next(item for item in value["recovery_evidence"]
                        if item["path"] == evidence.name)
            self.assertEqual(item["omitted"], "content_too_large")
            self.assertEqual(len(item["sha256"]), 64)
            self.assertTrue(value["manifest"]["completeness"]
                            ["forensic_evidence_complete"])
            self.assertFalse(value["manifest"]["completeness"]
                             ["forensic_content_complete"])
            cleaned = cleanup_recovery_data(
                database, older_than_days=0,
                archive_path=archive, apply=True)
            self.assertIn(evidence.name, cleaned["removed"])

    def test_imported_evidence_is_not_recursively_reembedded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source" / "games.db"
            target = root / "target" / "games.db"
            LocalGameStore(source)
            LocalGameStore(target)
            imported = source.parent / "imported-recovery" / ("a" * 24)
            imported.mkdir(parents=True)
            (imported / "old.bin").write_bytes(b"old evidence")
            archive = root / "backup.json"
            export_data(source, archive, include_recovery=True)
            value = _load_archive(archive)
            item = next(item for item in value["recovery_evidence"]
                        if item["path"].endswith("old.bin"))
            self.assertEqual(item["omitted"], "previously_imported")
            result = import_data(target, archive)
            self.assertTrue(result["ok"])
            restored_root = target.parent / "imported-recovery"
            self.assertFalse(restored_root.exists())

    def test_old_ruleset_pending_is_classified_before_current_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            archive_path = root / "backup.json"
            export_data(database, archive_path)
            archive = _load_archive(archive_path)
            mutation = normalize_score_mutation(
                "snake", "player", 4, profile_id=PROFILE_ID,
                ruleset_version="snake-old-rules",
                request_id="historical-score-request",
                attempt_uuid="historical-score-attempt")
            score = PendingSaveEnvelope.from_mutation(mutation).to_dict()
            ruleset = "sokoban-old-rules"
            key = f"progress:{PROFILE_ID}:sokoban:{ruleset}:campaign"
            state = PersistentStateOutbox._operation(
                key, "set_progress",
                (PROFILE_ID, "sokoban", "campaign",
                 {"unlocked_level": 2}, ruleset), 1, "historical-state")
            archive["pending_scores"] = [score]
            archive["pending_state"] = [state]
            operations = _planned_file_operations(database, archive)
            self.assertEqual(len(operations), 2)
            self.assertTrue(all("historical-pending" in str(item.target)
                                for item in operations))
            self.assertFalse(any(item.target.parent.name in {
                "pending", "pending-state"} for item in operations))

    def test_transaction_reader_rejects_nonstandard_json_constant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            transaction = ImportTransaction.prepare(database, [])
            journal = transaction.root / "journal.json"
            journal.write_text('{"value":NaN}', encoding="utf-8")
            with self.assertRaises(StoreError) as raised:
                ImportTransaction.open(database, transaction.root)
            self.assertEqual(raised.exception.code, "import_recovery_required")


class CommitLockAndLifecycleTests(unittest.TestCase):
    def test_score_commit_survives_cleanup_lock_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(
                db_path=Path(directory) / "games.db",
                outbox_path=Path(directory) / "pending")
            mutation = normalize_score_mutation(
                "snake", "player", 3, profile_id=PROFILE_ID,
                request_id="request-cleanup-timeout",
                attempt_uuid="attempt-cleanup-timeout")
            backend.store.ensure_profile("player", PROFILE_ID)
            with patch.object(
                    backend.outbox, "remove",
                    side_effect=StoreError(
                        "spool_lock_timeout", "busy", 503, retryable=True)):
                result = backend._save_mutation(mutation)
            self.assertTrue(result["ok"])
            self.assertTrue(result["cleanup_pending"])
            backend.close()

    def test_score_lock_paths_are_bounded_and_legacy_cleanup_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            outbox = PersistentSaveOutbox(root / "pending")
            outbox.path.mkdir(exist_ok=True)
            paths = {outbox._request_lock_path(f"request-{index}")
                     for index in range(10_000)}
            self.assertLessEqual(len(paths), 256)
            legacy = outbox.path / ".old-request.lock"
            PersistentSaveOutbox._write_bytes(legacy, b"\0")
            plan = cleanup_score_locks(database)
            self.assertEqual(plan["before"]["legacy"], 1)
            applied = cleanup_score_locks(database, apply=True)
            self.assertEqual(applied["removed_count"], 1)
            self.assertFalse(legacy.exists())

    def test_state_commit_survives_cleanup_lock_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(
                db_path=Path(directory) / "games.db",
                outbox_path=Path(directory) / "pending")
            backend.store.ensure_profile("player", PROFILE_ID)
            operation = backend._new_state_operation(
                f"setting:{PROFILE_ID}:volume", "set_setting",
                (PROFILE_ID, "volume", 0.5))
            with patch.object(
                    backend.state_outbox, "remove_if_current",
                    side_effect=StoreError(
                        "state_lock_timeout", "busy", 503, retryable=True)):
                result = backend._durable_state_write(operation)
            self.assertTrue(result["ok"])
            self.assertTrue(result["cleanup_pending"])
            event = backend.get_local_state_status(operation["key"])
            self.assertEqual(event.state, SaveState.COMMITTED)
            backend.close()

    def test_lease_is_not_released_while_read_worker_is_running(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            backend = LocalBackendClient(db_path=database)
            started = threading.Event()
            release = threading.Event()

            def slow_read():
                started.set()
                release.wait(5)

            backend._read_worker.submit(slow_read)
            self.assertTrue(started.wait(1))
            timer = threading.Timer(2.2, release.set)
            timer.start()
            result = backend.close()
            self.assertFalse(result.lease_released)
            with self.assertRaises(MaintenanceBusyError):
                with inactive_application_lock(database, timeout=0.05):
                    pass
            timer.join()
            backend._background_close_thread.join(2)
            with inactive_application_lock(database, timeout=0.2):
                pass


class SaveSlotValidationTests(unittest.TestCase):
    def test_2048_attempt_revision_respects_shared_63_bit_bound(self):
        state = {
            "version": 2,
            "game_state": "playing",
            "score": 0,
            "won": False,
            "won_announced": False,
            "attempt_uuid": "attempt-00000001",
            "attempt_revision": 1 << 63,
            "slot_revision": 0,
            "confirmed_score": None,
            "grid": [[2, 0, 0, 0], [0, 0, 0, 0],
                     [0, 0, 0, 0], [0, 0, 0, 0]],
        }
        with self.assertRaises(StoreError):
            validate_2048_state(state)


@unittest.skipUnless(
    __import__("importlib").util.find_spec("pygame"), "pygame is optional")
class GameStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pygame

        pygame.init()

    @classmethod
    def tearDownClass(cls):
        import pygame

        pygame.display.quit()

    def test_2048_mid_animation_close_saves_pre_move_settled_state(self):
        from client.games.game_2048 import GRID, Game2048, Tile

        captured = []

        class Backend:
            capabilities = frozenset({"durable_slot_intent"})

            @staticmethod
            def failed_save_count():
                return 0

            def publish_slot_intent(self, _profile, _game, _slot, state, *_args):
                captured.append(state)
                return {"ok": True, "published": True,
                        "durable_pending": True}

        for direction, positions in {
                "left": [(0, 0), (0, 1)],
                "right": [(0, 2), (0, 3)],
                "up": [(0, 0), (1, 0)],
                "down": [(2, 0), (3, 0)],
        }.items():
            with self.subTest(direction=direction):
                game = Game2048(backend=Backend(), profile_id=PROFILE_ID)
                game.tiles = []
                game.grid = [[None] * GRID for _ in range(GRID)]
                for row, col in positions:
                    tile = Tile(2, row=row, col=col)
                    game.tiles.append(tile)
                    game.grid[row][col] = tile
                game.score = 0
                game.anim_t = 1.0
                before = [[game.grid[row][col].value
                           if game.grid[row][col] else 0
                           for col in range(GRID)] for row in range(GRID)]
                self.assertTrue(game._move(direction))
                self.assertEqual(game.score, 4)
                game.before_close()
                saved = captured[-1]
                self.assertEqual(saved["grid"], before)
                self.assertEqual(saved["score"], 0)
                self.assertNotIn(4, [value for row in saved["grid"]
                                     for value in row])

    def test_sokoban_restore_syncs_identity_and_keeps_active_checkpoint(self):
        from client.games.sokoban import Sokoban

        published = []

        class Backend:
            capabilities = frozenset({"durable_slot_intent"})

            def load_slot_async(self, *_args):
                state = {
                    "version": 1, "active": True, "level_idx": 0,
                    "boxes": [[2, 2], [4, 2]], "player_pos": [2, 3],
                    "moves": 0, "pushes": 0, "history": [],
                    "score": 0, "state": "playing", "total_score": 0,
                    "level_scores": {}, "completed_levels": [],
                    "attempt_uuid": "restored-attempt-0001",
                    "attempt_revision": 9,
                }
                return completed({
                    "state": state,
                    "ruleset_version": GAME_BY_ID["sokoban"].ruleset_version})

            def publish_slot_intent(self, *_args):
                published.append(_args[3])
                return {"ok": True, "durable_pending": True,
                        "published": True}

            @staticmethod
            def failed_save_count():
                return 0

        game = Sokoban(backend=Backend(), profile_id=PROFILE_ID)
        game._poll_campaign_session()
        self.assertEqual(game._score_attempt_uuid, "restored-attempt-0001")
        self.assertEqual(game._score_attempt_revision, 9)
        self.assertTrue(game._restored_campaign_session_active)
        self.assertEqual(published, [])

    def test_async_slot_future_creation_is_not_durable(self):
        from client.games.sokoban import Sokoban

        pending = Future()

        class Backend:
            def save_slot_async(self, *_args):
                return pending

            @staticmethod
            def failed_save_count():
                return 0

        game = Sokoban(backend=Backend(), profile_id=PROFILE_ID)
        game.moves = 1
        self.assertFalse(game._capture_campaign_session())
        self.assertIsNotNone(game._campaign_session_save_future)

    def test_2048_v6_slot_restores_rng_sequence(self):
        from client.games.game_2048 import Game2048
        from game_service.save_slot_validation import restore_2048_rng_state

        class Backend:
            @staticmethod
            def failed_save_count():
                return 0

        game = Game2048(
            backend=Backend(), profile_id=PROFILE_ID,
            rng=random.Random(20260829))
        state = game._build_autosave_state("released")
        self.assertEqual(state["version"], 6)
        expected = game.rng.random()
        restored = random.Random(1)
        restore_2048_rng_state(restored, state["rng_state"])
        self.assertEqual(restored.random(), expected)

    def test_2048_v6_slot_takeover_preserves_owner_epoch(self):
        from client.games.game_2048 import Game2048

        class Backend:
            @staticmethod
            def failed_save_count():
                return 0

        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            outbox = PersistentStateOutbox(Path(directory) / "state")
            ruleset = GAME_BY_ID["2048"].ruleset_version
            key = f"slot:{PROFILE_ID}:2048:autosave"
            store.apply_state_operation(outbox._operation(
                f"profile:{PROFILE_ID}", "ensure_profile",
                ("player", PROFILE_ID), 1, "profile"))

            first = Game2048(
                backend=Backend(), profile_id=PROFILE_ID,
                rng=random.Random(1))._build_autosave_state("released")
            store.apply_state_operation(outbox._operation(
                key, "save_slot",
                (PROFILE_ID, "2048", "autosave", first, ruleset),
                2, "first-owner"))
            current = store.load_slot(PROFILE_ID, "2048", "autosave")

            second = Game2048(
                backend=Backend(), profile_id=PROFILE_ID,
                rng=random.Random(2))._build_autosave_state("active")
            second.update({
                "owner_epoch": 1,
                "slot_revision": first["slot_revision"] + 1,
                "expected_owner_token": first["owner_token"],
                "expected_owner_epoch": 0,
                "expected_slot_revision": first["slot_revision"],
                "expected_value_hash": current["value_hash"],
            })
            store.apply_state_operation(outbox._operation(
                key, "save_slot",
                (PROFILE_ID, "2048", "autosave", second, ruleset),
                3, "second-owner"))
            self.assertEqual(
                store.load_slot(PROFILE_ID, "2048", "autosave")["state"]
                ["owner_epoch"], 1)

    def test_2048_old_ruleset_is_preserved_before_new_game(self):
        from client.games.game_2048 import Game2048

        quarantined = []

        class Backend:
            def load_slot_async(self, *_args):
                return completed({
                    # Historical rulesets are classified before the current
                    # state parser, even when their shape is now unsupported.
                    "state": {"version": 999, "historical": True},
                    "ruleset_version": "2048-legacy"})

            def quarantine_slot_async(self, *_args):
                quarantined.append(_args[-1])
                return completed(True)

            @staticmethod
            def failed_save_count():
                return 0

        game = Game2048(backend=Backend(), profile_id=PROFILE_ID)
        game._poll_slot_load()
        self.assertTrue(game._slot_incompatible_ruleset)
        game._confirm_new_game_after_load_failure()
        game._confirm_new_game_after_load_failure()
        game._poll_slot_quarantine()
        self.assertEqual(quarantined, ["historical_2048_ruleset"])
        self.assertEqual(game.slot_load_state, "ready")


@unittest.skipUnless(
    __import__("importlib").util.find_spec("flask"), "Flask is optional")
class OptionalApiBoundaryTests(unittest.TestCase):
    def test_app_factory_rejects_remote_without_explicit_exposure(self):
        from server.app import create_app

        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "APPLICATION_LEASE": False,
                "DB_PATH": str(Path(directory) / "games.db"),
                "ALLOW_REMOTE_API": False,
            })
            response = app.test_client().get(
                "/api/health", environ_base={"REMOTE_ADDR": "192.0.2.4"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.get_json()["code"], "remote_api_disabled")

    def test_remote_factory_requires_configured_token(self):
        from server.app import create_app

        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "APPLICATION_LEASE": False,
                "DB_PATH": str(Path(directory) / "games.db"),
                "ALLOW_REMOTE_API": True, "API_TOKEN": "secret-token",
            })
            client = app.test_client()
            denied = client.get(
                "/api/health", environ_base={"REMOTE_ADDR": "192.0.2.4"})
            allowed = client.get(
                "/api/health", environ_base={"REMOTE_ADDR": "192.0.2.4"},
                headers={"Authorization": "Bearer secret-token"})
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
