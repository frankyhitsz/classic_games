"""Protocol and product regressions found during the fifteenth review."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from game_service.data_cli import (_load_archive, _planned_file_operations,
                                   export_data, export_transaction_data,
                                   preview_import, recover_transactions_data,
                                   restore_replace_data)
from game_service.import_transaction import (FileOperation, ImportTransaction,
                                             has_import_transaction_roots,
                                             recover_import_transactions)
from game_service.local_backend import (LocalBackendClient,
                                        PendingSaveEnvelope,
                                        PersistentSaveOutbox,
                                        PersistentStateOutbox)
from game_service.maintenance import (InactiveApplicationLease,
                                      _open_control_file,
                                      application_lock_path,
                                      inactive_application_lock,
                                      recovered_application_session)
from game_service.mutation import canonical_json, normalize_score_mutation
from game_service.store import LocalGameStore, StoreError


PROFILE_ID = "1234567890abcdef1234567890abcdef"


def _exclusive_waiter(database: str, ready, acquired, release) -> None:
    ready.set()
    with inactive_application_lock(Path(database), timeout=10):
        acquired.set()
        release.wait(10)


def _rewrite_archive(path: Path, mutate) -> dict:
    archive = json.loads(path.read_text(encoding="utf-8"))
    mutate(archive)
    payload = {key: value for key, value in archive.items()
               if key != "manifest_hash"}
    archive["manifest_hash"] = hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()
    path.write_text(canonical_json(archive), encoding="utf-8")
    return archive


class StartupAndTransactionTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX flock protocol test")
    def test_transition_gate_blocks_real_exclusive_waiter_during_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            lease = InactiveApplicationLease.acquire(database)
            context = multiprocessing.get_context("spawn")
            ready, acquired, release = (context.Event() for _ in range(3))
            process = context.Process(
                target=_exclusive_waiter,
                args=(str(database), ready, acquired, release))
            process.start()
            try:
                self.assertTrue(ready.wait(5))
                time.sleep(0.1)
                session = lease.handoff()
                try:
                    self.assertFalse(acquired.wait(0.2))
                finally:
                    session.close()
                self.assertTrue(acquired.wait(5))
            finally:
                release.set()
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            self.assertEqual(process.exitcode, 0)

    def test_missing_journal_import_root_blocks_and_preserves_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            root = database.parent / f".{database.name}.import-orphan"
            root.mkdir()
            rollback = root / "database-before.sqlite"
            rollback.write_bytes(b"last rollback evidence")
            with self.assertRaises(StoreError) as raised:
                recover_import_transactions(database)
            self.assertEqual(raised.exception.code, "import_recovery_required")
            self.assertEqual(rollback.read_bytes(), b"last rollback evidence")
            self.assertTrue(has_import_transaction_roots(database))

    def test_unpublished_preparing_root_is_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            root = database.parent / f".{database.name}.preparing-abandoned"
            root.mkdir()
            (root / "staged-0.bin").write_bytes(b"not published")
            self.assertEqual(recover_import_transactions(database), [])
            self.assertFalse(root.exists())
            self.assertFalse(has_import_transaction_roots(database))

    def test_handoff_rechecks_roots_before_returning_session(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            with patch(
                    "game_service.import_transaction.has_import_transaction_roots",
                    side_effect=[True, False]) as scan:
                session = recovered_application_session(database)
            session.close()
            self.assertEqual(scan.call_count, 2)

    def test_legacy_v1_cli_recovery_is_bound_to_exported_evidence(self):
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
            evidence = root / "transaction-evidence.json"
            exported = export_transaction_data(
                database, transaction.root.name, evidence)
            with self.assertRaises(StoreError) as raised:
                recover_transactions_data(database, allow_legacy_v1=True)
            self.assertEqual(
                raised.exception.code, "transaction_evidence_required")
            recovered = recover_transactions_data(
                database, allow_legacy_v1=True, evidence=evidence,
                evidence_sha256=exported["sha256"])
            self.assertEqual(recovered["recovered_count"], 1)

    def test_raw_v3_rollback_restores_corrupt_database_and_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            database.write_bytes(b"corrupt original")
            pending = root / "pending" / "state.json"
            pending.parent.mkdir()
            pending.write_bytes(b"old pending")
            transaction = ImportTransaction.prepare(
                database, [FileOperation(pending, b"new pending")],
                allow_raw_database_fallback=True)
            self.assertEqual(transaction.journal["version"], 3)
            fresh = root / "fresh.db"
            LocalGameStore(fresh)
            os.replace(fresh, database)
            transaction.mark("DB_APPLIED")
            transaction.publish_files()
            self.assertEqual(pending.read_bytes(), b"new pending")
            recovered = recover_import_transactions(database)
            self.assertEqual(recovered, [transaction.root.name])
            self.assertEqual(database.read_bytes(), b"corrupt original")
            self.assertEqual(pending.read_bytes(), b"old pending")


class StateResolutionTests(unittest.TestCase):
    @staticmethod
    def _setting_operation(value: bool, revision: int, operation_id: str) -> dict:
        key = f"setting:{PROFILE_ID}:sound"
        return PersistentStateOutbox._operation(
            key, "set_setting", (PROFILE_ID, "sound", value),
            revision, operation_id)

    def test_semantic_journal_conflict_never_reaches_database(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(db_path=Path(directory) / "games.db")
            try:
                old = self._setting_operation(True, 7, "same-operation")
                old = backend.state_outbox.put(
                    old["key"], old["method"], tuple(old["args"]),
                    logical_revision=7,
                    operation_id="same-operation")["operation"]
                incoming = self._setting_operation(False, 7, "same-operation")
                with patch.object(
                        backend, "_write_store_method",
                        side_effect=AssertionError("conflict reached SQLite")):
                    result = backend._durable_state_write_locked(incoming)
                self.assertEqual(result["code"], "state_operation_conflict")
                self.assertTrue(result["database_unchanged"])
                winner = backend.state_outbox.read_key(old["key"])
                self.assertEqual(winner["payload_hash"], old["payload_hash"])
            finally:
                backend.close()

    def test_import_planner_rejects_same_identity_different_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            outbox = PersistentStateOutbox(Path(directory) / "pending-state")
            old = self._setting_operation(True, 7, "same-operation")
            outbox.put(old["key"], old["method"], tuple(old["args"]),
                       logical_revision=7, operation_id="same-operation")
            incoming = self._setting_operation(False, 7, "same-operation")
            archive = {"pending_scores": [], "pending_state": [incoming],
                       "recovery_evidence": []}
            with self.assertRaises(StoreError) as raised:
                _planned_file_operations(database, archive)
            self.assertEqual(raised.exception.code, "state_operation_conflict")

    def test_import_planner_reports_duplicate_and_superseded(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            outbox = PersistentStateOutbox(Path(directory) / "pending-state")
            winner = self._setting_operation(True, 9, "winner")
            winner = outbox.put(
                winner["key"], winner["method"], tuple(winner["args"]),
                logical_revision=9, operation_id="winner")["operation"]
            duplicate_report = {}
            _planned_file_operations(
                database, {"pending_scores": [], "pending_state": [winner],
                           "recovery_evidence": []}, duplicate_report)
            self.assertEqual(duplicate_report, {"duplicate": 1})
            older = self._setting_operation(False, 8, "older")
            superseded_report = {}
            operations = _planned_file_operations(
                database, {"pending_scores": [], "pending_state": [older],
                           "recovery_evidence": []}, superseded_report)
            self.assertEqual(superseded_report, {"superseded": 1})
            restored = PersistentStateOutbox._parse(operations[0].data)
            self.assertEqual(restored["payload_hash"], winner["payload_hash"])


class JournalRecoveryTests(unittest.TestCase):
    def test_prepared_marker_with_missing_target_restores_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending-state"
            outbox = PersistentStateOutbox(path)
            key = f"setting:{PROFILE_ID}:sound"
            old = outbox.put(
                key, "set_setting", (PROFILE_ID, "sound", True),
                logical_revision=1, operation_id="old")["operation"]
            outbox.put(
                key, "set_setting", (PROFILE_ID, "sound", False),
                logical_revision=2, operation_id="new")
            outbox._target(key).unlink()
            reopened = PersistentStateOutbox(path)
            self.assertEqual(
                reopened.read_key(key)["payload_hash"], old["payload_hash"])
            self.assertFalse(list(path.glob(".reject-*.txn")))

    def test_replay_reject_failure_emits_recovery_required_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(db_path=Path(directory) / "games.db")
            try:
                operation = backend.state_outbox.put(
                    f"setting:{PROFILE_ID}:sound", "set_setting",
                    (PROFILE_ID, "sound", True), logical_revision=1,
                    operation_id="permanent-replay")["operation"]
                with (patch.object(
                        backend, "_write_store_method",
                        side_effect=StoreError("invalid_state", "invalid")),
                      patch.object(
                          backend.state_outbox,
                          "reject_and_restore_if_current", return_value=False)):
                    completed, blocked, _repair = (
                        backend._replay_state_entries_locked())
                self.assertEqual(completed, 0)
                self.assertTrue(blocked)
                event = backend.poll_local_state_events()[-1]
                self.assertEqual(event.state.value, "recovery_required")
                self.assertEqual(event.key, operation["key"])
            finally:
                backend.close()

    def test_score_and_state_orphan_temps_are_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutation = normalize_score_mutation(
                game_id="snake", player="p", score=12,
                request_id="orphan-score-request-0001",
                attempt_uuid="orphan-score-attempt-0001",
                profile_id=PROFILE_ID)
            envelope = PendingSaveEnvelope.from_mutation(mutation)
            score_path = root / "pending"
            score_path.mkdir()
            score_outbox = PersistentSaveOutbox(score_path, maintain=False)
            score_temp = score_path / ".orphan-score-request-0001.dead.tmp"
            score_outbox._write_bytes(
                score_temp,
                canonical_json(envelope.to_dict()).encode("utf-8"))
            old_time = time.time() - 10
            os.utime(score_temp, (old_time, old_time))
            PersistentSaveOutbox(score_path)
            self.assertTrue((score_path / f"{mutation.request_id}.json").is_file())

            state_path = root / "pending-state"
            state_path.mkdir()
            state_outbox = PersistentStateOutbox(state_path, recover=False)
            operation = PersistentStateOutbox._operation(
                f"setting:{PROFILE_ID}:sound", "set_setting",
                (PROFILE_ID, "sound", True), 3, "orphan-state")
            target = state_outbox._target(operation["key"])
            state_temp = state_path / f".{target.name}.dead.tmp"
            PersistentSaveOutbox._write_bytes(
                state_temp, canonical_json(operation).encode("utf-8"))
            os.utime(state_temp, (old_time, old_time))
            reopened = PersistentStateOutbox(state_path)
            self.assertEqual(
                reopened.read_key(operation["key"])["payload_hash"],
                operation["payload_hash"])

    @unittest.skipIf(os.name == "nt", "symlink creation requires privileges")
    def test_legacy_pending_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text("[]", encoding="utf-8")
            legacy = root / "pending_saves.json"
            legacy.symlink_to(outside)
            PersistentSaveOutbox(root / "pending", legacy_path=legacy)
            self.assertEqual(outside.read_text(encoding="utf-8"), "[]")
            self.assertFalse(legacy.is_symlink())

    def test_constructor_failure_releases_application_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            with patch(
                    "game_service.local_backend.PersistentSaveOutbox",
                    side_effect=StoreError("outbox_failed", "failed")):
                with self.assertRaises(StoreError):
                    LocalBackendClient(db_path=database)
            with inactive_application_lock(database, timeout=0):
                pass

    @unittest.skipIf(os.name == "nt", "symlink creation requires privileges")
    def test_database_symlink_uses_one_canonical_data_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_parent = root / "real"
            alias_parent = root / "alias"
            real_parent.mkdir()
            alias_parent.mkdir()
            real_database = real_parent / "games.db"
            LocalGameStore(real_database)
            alias_database = alias_parent / "games.db"
            alias_database.symlink_to(real_database)
            backend = LocalBackendClient(db_path=alias_database)
            try:
                canonical_parent = real_parent.resolve()
                self.assertEqual(
                    backend._selected_db_path, real_database.resolve())
                self.assertEqual(backend.outbox.path.parent, canonical_parent)
                self.assertEqual(
                    backend.state_outbox.path.parent, canonical_parent)
            finally:
                backend.close()

    @unittest.skipIf(os.name == "nt", "hard-link creation differs on Windows")
    def test_control_file_with_another_hard_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            lock = application_lock_path(database)
            lock.write_bytes(b"\0")
            os.link(lock, lock.with_name("duplicate-lock"))
            with self.assertRaises(OSError):
                _open_control_file(lock)


class ArchiveEvolutionTests(unittest.TestCase):
    def test_future_reader_dispatches_manifest_v4_by_archive_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            archive_path = root / "archive.json"
            export_data(database, archive_path)
            with patch("game_service.data_cli.MANIFEST_FORMAT_VERSION", 5):
                archive = _load_archive(archive_path)
            self.assertEqual(archive["manifest"]["format_version"], 4)

    def test_added_current_game_does_not_invalidate_old_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            archive_path = root / "archive.json"
            export_data(database, archive_path)
            archive = _rewrite_archive(
                archive_path,
                lambda value: value["manifest"]["application"]["rulesets"].pop(
                    "zuma"))
            self.assertNotIn(
                "zuma", _load_archive(archive_path)["manifest"]
                ["application"]["rulesets"])
            self.assertEqual(archive["archive_version"], 4)

    def test_row_game_must_be_declared_by_archive_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            store = LocalGameStore(source)
            store.ensure_profile("P", PROFILE_ID)
            store.set_progress(
                PROFILE_ID, "sokoban", "campaign", {"unlocked_level": 2})
            archive_path = root / "archive.json"
            export_data(source, archive_path)
            _rewrite_archive(
                archive_path,
                lambda value: value["manifest"]["application"]
                ["rulesets"].pop("sokoban"))
            target = root / "target.db"
            LocalGameStore(target)
            preview = preview_import(target, archive_path)
            self.assertFalse(preview["ok"])
            self.assertEqual(preview["tables"]["progress"]["invalid"], 1)

    def test_removed_game_rows_round_trip_with_historical_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            store = LocalGameStore(source)
            store.ensure_profile("P", PROFILE_ID)
            with store.connection() as connection:
                connection.execute(
                    "INSERT INTO progress(profile_id,game_id,ruleset_version,"
                    "key,value_json,value_version,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (PROFILE_ID, "retired-game", "retired-rules-1", "campaign",
                     canonical_json({"legacy_marker": True}), 1, time.time()))
                connection.commit()
            archive_path = root / "archive.json"
            export_data(source, archive_path)
            archive = _load_archive(archive_path)
            self.assertEqual(
                archive["manifest"]["application"]["rulesets"]
                ["retired-game"],
                "retired-rules-1")
            target = root / "target.db"
            LocalGameStore(target)
            preview = preview_import(target, archive_path)
            self.assertTrue(preview["ok"], preview)

    def test_historical_progress_uses_preserve_only_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            store = LocalGameStore(source)
            store.ensure_profile("P", PROFILE_ID)
            store.set_progress(
                PROFILE_ID, "sokoban", "campaign", {"unlocked_level": 2})
            archive_path = root / "archive.json"
            export_data(source, archive_path)

            def historical(value):
                row = value["tables"]["progress"][0]
                row["ruleset_version"] = "sokoban-campaign-1"
                row["value_json"] = canonical_json({"legacy_marker": True})

            _rewrite_archive(archive_path, historical)
            target = root / "target.db"
            LocalGameStore(target)
            preview = preview_import(target, archive_path)
            self.assertTrue(preview["ok"], preview)

    def test_replace_restore_works_over_corrupt_target_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            source_store = LocalGameStore(source)
            source_store.ensure_profile("Recovered", PROFILE_ID)
            archive_path = root / "archive.json"
            export_data(source, archive_path)
            target = root / "target.db"
            target.write_bytes(b"not a sqlite database")
            result = restore_replace_data(target, archive_path)
            self.assertEqual(
                LocalGameStore(target).last_profile()["display_name"],
                "Recovered")
            self.assertEqual(Path(result["backup"]).read_bytes(),
                             b"not a sqlite database")

    def test_replace_backup_failure_rolls_back_published_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            LocalGameStore(source)
            archive_path = root / "archive.json"
            export_data(source, archive_path)
            target = root / "target.db"
            original = b"corrupt original"
            target.write_bytes(original)
            with patch(
                    "game_service.data_cli._publish_output",
                    side_effect=OSError("backup publication failed")):
                with self.assertRaises(StoreError) as raised:
                    restore_replace_data(target, archive_path)
            self.assertEqual(raised.exception.code, "restore_rolled_back")
            self.assertEqual(target.read_bytes(), original)
            self.assertFalse(has_import_transaction_roots(target))


class ProgressSchemaTests(unittest.TestCase):
    def test_practice_progress_rejects_campaign_unlock(self):
        from game_service.progress import ProgressPolicyError, validate_progress

        with self.assertRaises(ProgressPolicyError):
            validate_progress("sokoban", "practice", {"unlocked_level": 16})


class SokobanPracticeSessionTests(unittest.TestCase):
    class Backend:
        pending_saves_are_durable = True

        def merge_progress_async(self, *_args):
            future = Future()
            future.set_result({"ok": True, "value": _args[3]})
            return future

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

    def test_practice_can_return_to_exact_campaign_board(self):
        from client.games.sokoban import Sokoban

        game = Sokoban(backend=self.Backend(), profile_id=PROFILE_ID)
        game.load_level(1, practice=False, new_campaign=False)
        game.completed_levels = {0}
        game.level_scores = {0: 900}
        game.total_score = 900
        original = (game.level_idx, game.player_pos, set(game.boxes),
                    list(game.history), game.total_score)
        self.assertFalse(game._select_practice_level(0))
        self.assertTrue(game._select_practice_level(0))
        game.player_pos = (999, 999)
        self.assertTrue(game._return_to_campaign())
        restored = (game.level_idx, game.player_pos, set(game.boxes),
                    list(game.history), game.total_score)
        self.assertEqual(restored, original)
        self.assertFalse(game.practice_mode)

    def test_campaign_and_practice_write_futures_are_independent(self):
        from client.games.sokoban import Sokoban

        game = Sokoban(backend=self.Backend(), profile_id=PROFILE_ID)
        campaign, practice = Future(), Future()
        game._progress_write_futures["campaign"] = (
            campaign, game._progress_generation["campaign"], "campaign")
        game._progress_write_futures["practice"] = (
            practice, game._progress_generation["practice"], "practice")
        practice.set_result({"ok": True, "value": {"completed_levels": [0]}})
        game._poll_progress()
        self.assertIsNotNone(game._progress_write_futures["campaign"])
        self.assertIsNone(game._progress_write_futures["practice"])

    def test_practice_result_uses_practice_score_not_campaign_total(self):
        from client.games.sokoban import Sokoban

        game = Sokoban(backend=self.Backend(), profile_id=PROFILE_ID)
        game.total_score = 900
        self.assertFalse(game._select_practice_level(0))
        self.assertTrue(game._select_practice_level(0))
        game.boxes = set(game.targets)
        game._check_win()
        self.assertEqual(game.total_score, 900)
        self.assertEqual(game.score, game.extra["practice_score"])
        self.assertNotEqual(game.score, game.total_score)


if __name__ == "__main__":
    unittest.main()
