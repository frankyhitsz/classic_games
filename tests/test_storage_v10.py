"""Regression coverage for the twelfth local-first review."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from game_service.data_cli import (export_data, import_data, inspect_data,
                                   preview_import, restore_replace_data,
                                   _safe_evidence_relative)
from game_service.import_transaction import (FileOperation, ImportTransaction,
                                             recover_import_transactions)
from game_service.local_backend import (MAX_PENDING_ATTEMPTS,
                                        LocalBackendClient,
                                        PendingSaveEnvelope,
                                        PersistentSaveOutbox,
                                        PersistentStateOutbox)
from game_service.maintenance import MaintenanceBusyError
from game_service.mutation import canonical_json, normalize_score_mutation
from game_service.store import LocalGameStore, StoreError


def completed(value):
    future = Future()
    future.set_result(value)
    return future


class PendingBoundaryTests(unittest.TestCase):
    def test_score_attempt_count_is_bounded_and_restored_once(self):
        mutation = normalize_score_mutation(
            "snake", "player", 10, profile_id="1" * 32,
            request_id="bounded-request-000001",
            attempt_uuid="bounded-attempt-000001", revision=1)
        value = PendingSaveEnvelope.from_mutation(mutation).to_dict()
        value["attempt_count"] = MAX_PENDING_ATTEMPTS + 1
        with self.assertRaises(StoreError):
            PendingSaveEnvelope.parse(value)

        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentSaveOutbox(Path(directory) / "pending")
            outbox.add_mutation(mutation)
            with patch.object(
                    PersistentSaveOutbox, "_write_bytes",
                    wraps=PersistentSaveOutbox._write_bytes) as writes:
                outbox.set_attempt_count_max(mutation.request_id, 9000)
            self.assertEqual(writes.call_count, 1)
            envelope, _ = outbox.list_envelopes()[0]
            self.assertEqual(envelope.attempt_count, 9000)

    def test_state_revision_and_timestamp_use_store_bounds(self):
        operation = PersistentStateOutbox._operation(
            f"setting:{'2' * 32}:volume", "set_setting",
            ("2" * 32, "volume", 0.5), 1, "bounded-state")
        for field, value in (("logical_revision", 1 << 63),
                             ("updated_at", 1e20 * 2)):
            invalid = dict(operation)
            invalid[field] = value
            invalid["payload_hash"] = PersistentStateOutbox._digest({
                key: child for key, child in invalid.items()
                if key != "payload_hash"})
            with self.assertRaises(StoreError):
                PersistentStateOutbox._parse(
                    canonical_json(invalid).encode("utf-8"))
        legacy_v2 = {
            key: child for key, child in operation.items()
            if key != "components"}
        legacy_v2["schema_version"] = 2
        legacy_v2["logical_revision"] = 1 << 63
        legacy_v2["payload_hash"] = PersistentStateOutbox._digest({
            key: child for key, child in legacy_v2.items()
            if key != "payload_hash"})
        with self.assertRaises(StoreError):
            PersistentStateOutbox._parse(
                canonical_json(legacy_v2).encode("utf-8"))

    def test_state_total_quota_never_quarantines_a_valid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            outbox = PersistentStateOutbox(path)
            for index in range(2):
                profile_id = str(index + 3) * 32
                outbox.put(
                    f"setting:{profile_id}:volume", "set_setting",
                    (profile_id, "volume", 0.5),
                    logical_revision=index + 1,
                    operation_id=f"quota-{index}")
            files = list(path.glob("*.json"))
            first_size = min(item.stat().st_size for item in files)
            with patch(
                    "game_service.local_backend.MAX_SPOOL_TOTAL_BYTES",
                    first_size):
                entries = outbox.list_entries(10)
            self.assertLess(len(entries), 2)
            self.assertEqual(len(list(path.glob("*.json"))), 2)
            self.assertFalse(outbox.quarantine_path.exists())


class RejectRecoveryTests(unittest.TestCase):
    def test_reject_marker_recovers_previous_operation_on_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            outbox = PersistentStateOutbox(path)
            profile_id = "5" * 32
            key = f"setting:{profile_id}:volume"
            old = outbox.put(
                key, "set_setting", (profile_id, "volume", 0.2),
                logical_revision=1, operation_id="old")
            new = outbox.put(
                key, "set_setting", (profile_id, "volume", 0.8),
                logical_revision=2, operation_id="new")
            with patch.object(
                    outbox, "_complete_reject_transaction",
                    return_value=False):
                self.assertFalse(outbox.reject_and_restore_if_current(
                    key, new["payload_hash"], old["operation"], "rejected"))
            self.assertTrue(list(path.glob(".reject-*.txn")))
            reopened = PersistentStateOutbox(path)
            restored = reopened.read_key(key)
            self.assertEqual(restored["payload_hash"], old["payload_hash"])
            self.assertFalse(list(path.glob(".reject-*.txn")))

    def test_failed_reject_restore_keeps_previous_non_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile_id = backend.ensure_profile_async(
                "player", "6" * 32).result(timeout=5)["profile_id"]
            key = f"setting:{profile_id}:volume"
            old = backend.state_outbox.put(
                key, "set_setting", (profile_id, "volume", 0.1),
                logical_revision=1, operation_id="previous")
            new = backend._new_state_operation(
                key, "set_setting", (profile_id, "volume", 0.9))
            with backend._lock:
                backend._unpublished_state.add(new["operation_id"])
            with (patch.object(
                    backend.store, "apply_state_operation",
                    side_effect=StoreError("invalid_setting", "rejected")),
                  patch.object(
                    backend.state_outbox, "reject_and_restore_if_current",
                    return_value=False)):
                result = backend._durable_state_write(new)
            self.assertFalse(result["previous_pending_restored"])
            self.assertEqual(
                backend._non_durable_state[key]["payload_hash"],
                old["payload_hash"])
            backend.close()


class ImportTransactionTests(unittest.TestCase):
    def test_post_commit_publish_failure_rolls_database_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.db"
            target_path = root / "target.db"
            archive = root / "archive.json"
            LocalGameStore(source_path).ensure_profile("source", "7" * 32)
            LocalGameStore(target_path)
            export_data(source_path, archive)
            with patch.object(
                    ImportTransaction, "publish_files",
                    side_effect=OSError("simulated ENOSPC")):
                with self.assertRaises(StoreError) as raised:
                    import_data(target_path, archive)
            self.assertEqual(raised.exception.code, "import_rolled_back")
            self.assertEqual(LocalGameStore(target_path).list_profiles(), [])

    def test_interrupted_phase_is_rolled_back_before_next_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            store = LocalGameStore(database)
            store.ensure_profile("before", "8" * 32)
            transaction = ImportTransaction.prepare(database, [
                FileOperation(root / "pending" / "intent.json", b"intent")])
            store.ensure_profile("after", "9" * 32)
            transaction.mark("DB_APPLIED")
            recovered = recover_import_transactions(database)
            self.assertEqual(len(recovered), 1)
            profiles = LocalGameStore(database).list_profiles()
            self.assertEqual([item["display_name"] for item in profiles], ["before"])
            self.assertFalse((root / "pending" / "intent.json").exists())

    def test_tampered_phase_journal_is_preserved_for_manual_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            transaction = ImportTransaction.prepare(database, [])
            journal_path = transaction.root / "journal.json"
            journal = json.loads(journal_path.read_text())
            journal["phase"] = "DB_APPLIED"
            journal_path.write_text(json.dumps(journal), encoding="utf-8")
            with self.assertRaises(StoreError) as raised:
                recover_import_transactions(database)
            self.assertEqual(
                raised.exception.code, "import_recovery_required")
            self.assertTrue(transaction.root.is_dir())

    def test_pending_conflict_with_committed_receipt_is_previewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source" / "games.db"
            target_path = root / "target" / "games.db"
            archive = root / "archive.json"
            source = LocalGameStore(source_path)
            target = LocalGameStore(target_path)
            request_id = "committed-request-000001"
            target.record_mutation(normalize_score_mutation(
                "snake", "target", 10, profile_id="a" * 32,
                request_id=request_id,
                attempt_uuid="committed-attempt-000001", revision=1))
            profile_id = source.ensure_profile("source", "b" * 32)["profile_id"]
            PersistentSaveOutbox(
                source_path.parent / "pending").add_mutation(
                    normalize_score_mutation(
                        "snake", "source", 20, profile_id=profile_id,
                        request_id=request_id,
                        attempt_uuid="pending-attempt-0000001", revision=1))
            export_data(source_path, archive)
            preview = preview_import(target_path, archive)
            self.assertFalse(preview["ok"])
            self.assertIn(
                "pending transactional validation failed",
                "\n".join(preview["errors"]))

    def test_replace_restore_removes_newer_tables_and_active_journals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source" / "games.db"
            target_path = root / "target" / "games.db"
            archive = root / "archive.json"
            source = LocalGameStore(source_path)
            source.ensure_profile("archived", "1a" * 16)
            source_mutation = normalize_score_mutation(
                "snake", "archived", 5, profile_id="1a" * 16,
                request_id="archive-pending-000001",
                attempt_uuid="archive-attempt-000001", revision=1)
            PersistentSaveOutbox(
                source_path.parent / "pending").add_mutation(source_mutation)
            export_data(source_path, archive)

            target = LocalGameStore(target_path)
            target.ensure_profile("newer", "2b" * 16)
            target_mutation = normalize_score_mutation(
                "snake", "newer", 9, profile_id="2b" * 16,
                request_id="target-pending-0000001",
                attempt_uuid="target-attempt-0000001", revision=1)
            PersistentSaveOutbox(
                target_path.parent / "pending").add_mutation(target_mutation)

            restored = restore_replace_data(target_path, archive)
            self.assertEqual(restored["mode"], "replace")
            profiles = LocalGameStore(target_path).list_profiles()
            self.assertEqual([item["display_name"] for item in profiles],
                             ["archived"])
            pending = PersistentSaveOutbox(
                target_path.parent / "pending").list_envelopes()
            self.assertEqual([item[0].request_id for item in pending],
                             ["archive-pending-000001"])


class ArchiveReadOnlyTests(unittest.TestCase):
    def test_recovery_paths_reject_windows_forms_and_symlink_parents(self):
        for raw in ("C:relative", "folder/name:stream", "CON/file", "a\\b"):
            with self.assertRaises(StoreError):
                _safe_evidence_relative(raw)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source" / "games.db"
            target_path = root / "target" / "games.db"
            archive = root / "archive.json"
            LocalGameStore(source_path)
            LocalGameStore(target_path)
            export_data(source_path, archive)
            value = json.loads(archive.read_text())
            raw = b"evidence"
            value["recovery_evidence"] = [{
                "path": "backup/file.bin", "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }]
            value["manifest"]["recovery"].update({
                "count": 1, "source_count": 1, "included_count": 1})
            value["manifest_hash"] = hashlib.sha256(canonical_json({
                key: child for key, child in value.items()
                if key != "manifest_hash"}).encode("utf-8")).hexdigest()
            archive.write_text(json.dumps(value), encoding="utf-8")
            outside = root / "outside"
            outside.mkdir()
            (target_path.parent / "imported-recovery").symlink_to(
                outside, target_is_directory=True)
            preview = preview_import(target_path, archive)
            self.assertFalse(preview["ok"])

    def test_export_requires_the_active_backend_to_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            archive = root / "archive.json"
            backend = LocalBackendClient(
                db_path=database, outbox_path=root / "pending")
            with self.assertRaises(StoreError) as raised:
                export_data(database, archive)
            self.assertEqual(raised.exception.code, "maintenance_busy")
            backend.close()
            export_data(database, archive)
            self.assertTrue(archive.is_file())

    def test_export_refuses_incomplete_journal_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            archive = root / "archive.json"
            LocalGameStore(database)
            pending = root / "pending"
            pending.mkdir()
            poisoned = pending / "bad.json"
            poisoned.write_bytes(b"{bad")
            with self.assertRaises(StoreError) as raised:
                export_data(database, archive)
            self.assertEqual(
                raised.exception.code, "incomplete_pending_snapshot")
            self.assertEqual(poisoned.read_bytes(), b"{bad")
            self.assertFalse((root / "pending-quarantine").exists())
            export_data(database, archive, allow_partial=True)
            manifest = json.loads(archive.read_text())["manifest"]
            self.assertFalse(manifest["complete"])
            self.assertEqual(manifest["pending"]["score"]["omitted_count"], 1)

    def test_newer_database_schema_is_rejected_after_hash_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            archive = root / "archive.json"
            LocalGameStore(database)
            export_data(database, archive)
            value = json.loads(archive.read_text())
            value["schema_version"] += 1
            value["manifest_hash"] = hashlib.sha256(canonical_json({
                key: child for key, child in value.items()
                if key != "manifest_hash"}).encode("utf-8")).hexdigest()
            archive.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(StoreError) as raised:
                preview_import(database, archive)
            self.assertEqual(raised.exception.code, "unsupported_archive_schema")

    def test_status_reports_legacy_database_without_migrating(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE scores(id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()
            before = database.stat().st_mtime_ns
            status = inspect_data(database)
            self.assertTrue(status["migration_needed"])
            self.assertEqual(status["schema_version"], 0)
            self.assertIn("attempts", status["missing_tables"])
            self.assertEqual(database.stat().st_mtime_ns, before)


class BackendAndGameTests(unittest.TestCase):
    class Backend:
        is_local = True
        pending_saves_are_durable = True

        def __init__(self):
            self.saves = []
            self.block = False

        def load_slot_async(self, *_args):
            return completed(None)

        def save_slot_async(self, *args):
            self.saves.append(args)
            if self.block:
                return Future()
            value = args[3]
            return completed({
                "ok": True, "state_apply": "committed",
                "value": value,
                "value_hash": LocalGameStore._state_value_hash({
                    "state": value, "state_version": value["version"],
                    "ruleset_version": args[4]})})

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

    def test_continuous_2048_moves_flush_and_coalesce_inflight(self):
        from client.games.game_2048 import Game2048

        backend = self.Backend()
        game = Game2048(backend=backend, profile_id="c" * 32)
        game._poll_slot_load()
        game._poll_slot_save()
        backend.saves.clear()
        backend.block = True
        with patch("pygame.time.get_ticks", return_value=1000):
            game._queue_autosave_slot()
        with patch("pygame.time.get_ticks", return_value=2000):
            game._queue_autosave_slot()
        with patch("pygame.time.get_ticks", return_value=2501):
            game._flush_autosave_if_due()
        self.assertEqual(len(backend.saves), 1)
        first = game._slot_save_future
        game.score = 32
        game._save_autosave_slot()
        self.assertEqual(len(backend.saves), 1)
        first.set_result({"ok": True})
        game._poll_slot_save()
        self.assertEqual(len(backend.saves), 2)
        self.assertEqual(backend.saves[-1][3]["score"], 32)

    def test_score_maintenance_timeout_becomes_non_durable_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(
                db_path=Path(directory) / "games.db",
                outbox_path=Path(directory) / "pending")
            with patch(
                    "game_service.local_backend.maintenance_lock",
                    side_effect=MaintenanceBusyError("busy")):
                future = backend.submit_score_async(
                    "snake", "player", 1, profile_id="d" * 32)
                with self.assertRaises(MaintenanceBusyError):
                    future.result(timeout=5)
            deadline = time.monotonic() + 1.0
            while not backend.failed_save_count() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(backend.failed_save_count(), 1)
            backend.close()


class MergeReceiptMaintenanceTests(unittest.TestCase):
    def test_more_than_one_thousand_components_remain_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            protected = tuple(f"component-{index}" for index in range(1005))
            old = time.time() - 1000 * 86400
            with store.connection() as connection:
                connection.executemany(
                    "INSERT INTO state_merge_receipts "
                    "(operation_id,semantic_key,payload_hash,applied_at) "
                    "VALUES(?,?,?,?)",
                    ((item, "key", "e" * 64, old) for item in protected))
                connection.commit()
            store.maintenance(protected)
            with store.connection() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM state_merge_receipts").fetchone()[0]
            self.assertEqual(count, len(protected))


if __name__ == "__main__":
    unittest.main()
