"""Eighteenth-review composition, crash and compatibility checks."""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import os
import multiprocessing
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from game_service.catalog import GAME_BY_ID
from game_service.data_cli import (export_data, export_transaction_data, import_data,
                                   inspect_archive, preview_replace, restore_replace_data,
                                   upgrade_archive, verify_archive,
                                   _validated_transaction_evidence)
from game_service.import_transaction import (ImportTransaction, FileOperation,
                                             has_import_transaction_roots,
                                             recover_import_transactions)
from game_service.local_backend import (LocalBackendClient, PersistentStateOutbox,
                                        PersistentSaveOutbox)
from game_service.mutation import canonical_json
from game_service.safe_fs import is_safe_directory, is_safe_regular, rename_noreplace
from game_service.save_slot_validation import validate_2048_state
from game_service.sokoban_history import decode_history, encode_history
from game_service.store import LocalGameStore, StoreError
from game_service.service import SaveState

PROFILE = "8" * 32
RULESET = GAME_BY_ID["sokoban"].ruleset_version
KEY = f"progress:{PROFILE}:sokoban:{RULESET}:campaign"


def legacy_lock_holder(directory, request_id, ready, release):
    from game_service.maintenance import _open_control_file
    descriptor = _open_control_file(Path(directory) / f".{request_id}.lock")
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        if not PersistentSaveOutbox._try_lock_descriptor(descriptor):
            raise RuntimeError("fixture lock unavailable")
        ready.set()
        release.wait(10)
        PersistentSaveOutbox._unlock_descriptor(descriptor)
    finally:
        os.close(descriptor)


def progress(method, value, revision, identity):
    return PersistentStateOutbox._operation(
        KEY, method, (PROFILE, "sokoban", "campaign", {"unlocked_level": value}, RULESET),
        revision, identity, updated_at=100 + revision)


def rewrite_archive(path, mutate):
    value = json.loads(path.read_text())
    mutate(value)
    value.pop("manifest_hash")
    value["manifest_hash"] = hashlib.sha256(canonical_json(value).encode()).hexdigest()
    path.write_text(canonical_json(value))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "games.db"
        self.store = LocalGameStore(self.database)
        self.store.ensure_profile("player", PROFILE)


class ComponentTests(Fixture):
    def test_component_result_does_not_take_semantic_winner_identity(self):
        backend = LocalBackendClient(db_path=self.database, outbox_path=self.root / "pending")
        try:
            component = progress("set_progress", 3, 10, "zz-component")
            backend._emit_local_state_event(component, SaveState.SAVING, {})
            backend._emit_local_state_event(component, SaveState.COMMITTED, {
                "ok": True, "absorbed_component": True,
                "winning_logical_revision": 10, "winning_operation_id": "aa-aggregate",
                "winning_payload_hash": "a" * 64, "winning_value_hash": "b" * 64})
            self.assertEqual(backend.get_local_state_status(KEY).operation_id, "aa-aggregate")
            self.assertEqual(backend.poll_local_state_events()[-1].operation_id, "zz-component")
        finally:
            backend.close()

    def test_later_merge_after_import_baseline_is_allowed(self):
        self.store.set_progress(PROFILE, "sokoban", "campaign", {"unlocked_level": 2})
        operation = PersistentStateOutbox._operation(
            KEY, "merge_progress", (PROFILE, "sokoban", "campaign", {"unlocked_level": 3}, RULESET),
            2, "later", updated_at=time.time() + 1)
        self.assertEqual(self.store.apply_state_operation(operation)["state_apply"], "committed")
        self.assertEqual(self.store.get_progress(PROFILE, "sokoban", "campaign", {})[
            "unlocked_level"], 3)

    def test_raw_merge_replay_subset_superset_and_partial_union(self):
        a, b, c = [progress("merge_progress", i + 2, i + 1, str(i)) for i in range(3)]
        ab, _ = PersistentStateOutbox.resolve_operations(a, b)
        bc, _ = PersistentStateOutbox.resolve_operations(b, c)
        self.assertEqual(PersistentStateOutbox.resolve_operations(ab, a), (ab, "duplicate"))
        self.assertEqual(PersistentStateOutbox.resolve_operations(a, ab), (ab, "incoming"))
        abc, _ = PersistentStateOutbox.resolve_operations(ab, bc)
        self.assertEqual(len(abc["components"]), 3)
        self.assertEqual(PersistentStateOutbox.resolve_operations(abc, ab)[1], "duplicate")

    def test_merge_permutations_are_idempotent_commutative_associative(self):
        operations = [progress("merge_progress", i + 2, i + 1, str(i)) for i in range(4)]
        identities = set()
        for permutation in itertools.permutations(operations):
            winner = permutation[0]
            for operation in permutation[1:]:
                winner, _ = PersistentStateOutbox.resolve_operations(winner, operation)
            identities.add(winner["payload_hash"])
            for operation in operations:
                self.assertEqual(PersistentStateOutbox.resolve_operations(winner, operation)[1],
                                 "duplicate")
        self.assertEqual(len(identities), 1)

    def test_component_hash_conflict_is_stable(self):
        a = progress("merge_progress", 2, 1, "a")
        b = progress("merge_progress", 3, 2, "a")
        for left, right in ((a, b), (b, a)):
            with self.assertRaises(StoreError):
                PersistentStateOutbox.resolve_operations(left, right)

    def test_older_merge_cannot_cross_reset_even_after_later_merge_and_restart(self):
        old = progress("merge_progress", 15, 10, "old")
        reset = progress("set_progress", 2, 20, "reset")
        new = progress("merge_progress", 3, 30, "new")
        for operation in (old, reset, new):
            self.store.apply_state_operation(operation)
        restarted = LocalGameStore(self.database)
        self.assertTrue(restarted.apply_state_operation(old)["superseded"])
        self.assertEqual(restarted.get_progress(PROFILE, "sokoban", "campaign", {})[
            "unlocked_level"], 3)

    def test_live_and_store_set_merge_order_matrix(self):
        reset = progress("set_progress", 2, 20, "reset")
        old = progress("merge_progress", 12, 10, "old")
        for sequence in ((reset, old), (old, reset)):
            with tempfile.TemporaryDirectory() as directory:
                store = LocalGameStore(Path(directory) / "db")
                store.ensure_profile("player", PROFILE)
                for operation in sequence:
                    store.apply_state_operation(operation)
                winner, _ = PersistentStateOutbox.resolve_operations(*sequence)
                self.assertEqual(store.get_progress(PROFILE, "sokoban", "campaign", {}),
                                 winner["args"][3])


class SlotTests(Fixture):
    def save(self, value, revision):
        operation = PersistentStateOutbox._operation(
            f"slot:{PROFILE}:sokoban:return", "save_slot",
            (PROFILE, "sokoban", "return", {"version": 1, "value": value}, RULESET),
            revision, str(revision))
        self.store.apply_state_operation(operation)
        return operation, self.store.load_slot(PROFILE, "sokoban", "return")

    def quarantine(self, loaded):
        return self.store.quarantine_slot(
            PROFILE, "sokoban", "return", "test",
            expected_value_hash=loaded["value_hash"],
            expected_ruleset=loaded["ruleset_version"],
            expected_state_version=loaded["state_version"])

    def test_changed_slot_and_metadata_are_never_deleted(self):
        _, loaded = self.save("first", 10)
        self.save("second", 20)
        self.assertEqual(self.quarantine(loaded)["status"], "CHANGED")
        current = self.store.load_slot(PROFILE, "sokoban", "return")
        for field, value in (("ruleset_version", "old"), ("state_version", 999)):
            self.assertEqual(self.quarantine({**current, field: value})["status"], "CHANGED")
        self.assertEqual(self.store.load_slot(PROFILE, "sokoban", "return")["state"]["value"],
                         "second")

    def test_tombstone_survives_restart_and_rejects_pending_replay(self):
        old, loaded = self.save("old", 10)
        receipt = self.quarantine(loaded)
        self.assertEqual(receipt["status"], "QUARANTINED")
        self.store = LocalGameStore(self.database)
        self.assertTrue(self.store.apply_state_operation(old)["superseded"])
        self.assertIsNone(self.store.load_slot(PROFILE, "sokoban", "return"))
        self.assertEqual(self.quarantine(loaded)["status"], "ABSENT")
        self.save("new", receipt["logical_revision"] + 1)
        self.assertTrue(self.store.apply_state_operation(old)["superseded"])

    def test_deleted_slot_pending_is_not_exported_back_into_active_state(self):
        old, loaded = self.save("old", 10)
        outbox = PersistentStateOutbox(self.root / "pending-state")
        outbox.put(old["key"], old["method"], tuple(old["args"]),
                   logical_revision=old["logical_revision"], operation_id=old["operation_id"],
                   updated_at=old["updated_at"])
        self.assertEqual(self.quarantine(loaded)["status"], "QUARANTINED")
        output = self.root / "deleted.json"
        export_data(self.database, output)
        archive = json.loads(output.read_text())
        self.assertEqual(archive["pending_state"], [])
        self.assertEqual(archive["manifest"]["pending"]["retired_state_journals"], 1)
        restore_replace_data(self.database, output)
        self.assertIsNone(LocalGameStore(self.database).load_slot(PROFILE, "sokoban", "return"))

    def test_merge_import_cannot_cross_local_deletion(self):
        _, loaded = self.save("old", 10)
        output = self.root / "before-delete.json"
        export_data(self.database, output)
        self.quarantine(loaded)
        with self.assertRaises(StoreError):
            import_data(self.database, output)


class RecoveryTests(Fixture):
    def test_new_score_writer_excludes_legacy_process(self):
        outbox = PersistentSaveOutbox(self.root / "pending")
        outbox.path.mkdir(exist_ok=True)
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        process = context.Process(target=legacy_lock_holder,
                                  args=(str(outbox.path), "same-request", ready, release))
        process.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaises(StoreError) as raised:
                with outbox._request_lock("same-request"):
                    self.fail("legacy lock bypassed")
            self.assertEqual(raised.exception.code, "spool_lock_timeout")
        finally:
            release.set()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        with outbox._request_lock("same-request"):
            pass

    def test_final_marker_read_is_under_filename_lock(self):
        outbox = PersistentStateOutbox(self.root / "state")
        outbox.path.mkdir(exist_ok=True)
        op = progress("set_progress", 2, 1, "a")
        marker = outbox._reject_marker_path(KEY, op["payload_hash"])
        outbox._write_reject_marker_locked(marker, outbox._reject_transaction(
            KEY, op["payload_hash"], None, phase="prepared", reason="test"))
        held = []
        original_lock = outbox._digest_lock
        from game_service import local_backend
        original_read = local_backend._read_regular_nofollow

        @contextmanager
        def lock(digest):
            with original_lock(digest):
                held.append(digest)
                try:
                    yield
                finally:
                    held.pop()

        def read(path, limit):
            if path == marker:
                self.assertEqual(held, [hashlib.sha256(KEY.encode()).hexdigest()])
            return original_read(path, limit)

        with patch.object(outbox, "_digest_lock", side_effect=lock), patch.object(
                local_backend, "_read_regular_nofollow", side_effect=read):
            outbox.recover_transactions()
        self.assertFalse(marker.exists())

    def test_recovery_lock_wait_uses_remaining_budget(self):
        outbox = PersistentStateOutbox(self.root / "state")
        started = time.monotonic()
        outbox._recovery_scan_deadline = started + 0.025
        with patch.object(PersistentSaveOutbox, "_try_lock_descriptor", return_value=False):
            with self.assertRaises(StoreError):
                with outbox._digest_lock("a" * 64):
                    self.fail("busy lock entered")
        self.assertLess(time.monotonic() - started, 0.20)

    def test_terminal_cleanup_failure_preserves_committed_outcome(self):
        transaction = ImportTransaction.prepare(self.database, [])
        transaction.mark("FILES_PUBLISHED")
        real_replace = os.replace

        def fail_cleanup(source, destination):
            if "transaction-cleanup" in str(destination):
                raise PermissionError("locked")
            return real_replace(source, destination)

        with patch("game_service.import_transaction.os.replace", side_effect=fail_cleanup):
            result = transaction.finish()
            self.assertEqual(result["business_outcome"], "COMMITTED")
            self.assertEqual(result["cleanup_state"], "PENDING")
            self.assertFalse(has_import_transaction_roots(self.database))
            self.assertEqual(recover_import_transactions(self.database), [])
        recover_import_transactions(self.database)

    def test_rollback_finish_never_rewrites_completed(self):
        transaction = ImportTransaction.prepare(self.database, [])
        transaction.rollback()
        with patch.object(transaction, "cleanup_terminal", return_value=transaction.root):
            self.assertEqual(transaction.finish()["business_outcome"], "ROLLED_BACK")
        self.assertEqual(ImportTransaction.open(self.database, transaction.root).journal["phase"],
                         "ROLLED_BACK")
        with self.assertRaises(ValueError):
            transaction.mark("COMPLETED")

    def test_preparing_partial_delete_is_nonactive(self):
        root = self.root / ".games.db.preparing-test"
        root.mkdir()
        (root / "staged").write_bytes(b"bytes")
        with patch("game_service.import_transaction.shutil.rmtree", side_effect=PermissionError):
            recover_import_transactions(self.database)
        self.assertFalse(root.exists())
        self.assertFalse(has_import_transaction_roots(self.database))
        self.assertTrue(list(self.root.glob(".games.db.transaction-cleanup-*")))


class ArchiveTests(Fixture):
    def test_binary_snapshot_and_digest_preserve_all_bytes(self):
        from game_service.data_cli import _hash_regular_nofollow, _read_regular_nofollow
        from game_service.import_transaction import _read_file_snapshot
        raw = bytes(range(256)) * 8192 + b"\r\n\x1aafter-eof\r\n"
        source = self.root / "binary-evidence"
        source.write_bytes(raw)
        expected = (len(raw), hashlib.sha256(raw).hexdigest())
        self.assertEqual(_hash_regular_nofollow(source), expected)
        self.assertEqual(_read_regular_nofollow(source, len(raw)), raw)
        self.assertEqual(_read_file_snapshot(source, len(raw)), (raw, *expected))

    def test_lock_inventory_uses_fresh_link_counts(self):
        from game_service.data_cli import _score_lock_inventory
        root = self.root / "pending"
        root.mkdir()
        path = root / ".old-request.lock"
        path.write_bytes(b"\0")
        entry = SimpleNamespace(name=path.name, path=str(path))

        @contextmanager
        def entries(_root):
            yield iter([entry])

        with patch("game_service.data_cli.os.scandir", entries):
            inventory = _score_lock_inventory(root)
        self.assertEqual(inventory["legacy"], 1)
        self.assertEqual(inventory["unsafe"], 0)

    def test_memory_error_has_stable_public_error(self):
        from game_service import data_cli
        with patch.object(data_cli, "_archive_ruleset_catalog", side_effect=MemoryError):
            with self.assertRaises(StoreError) as raised:
                export_data(self.database, self.root / "too-big.json")
        self.assertEqual(raised.exception.code, "data_resource_limit")
        self.assertFalse((self.root / "too-big.json").exists())

    def test_publication_crash_exposes_whole_file_or_no_file(self):
        for stage in ("before", "after"):
            output = self.root / f"crash-{stage}.json"
            script = '''
import os, sys
from pathlib import Path
from game_service import data_cli
original = data_cli.rename_noreplace
def publish(source, destination):
    if sys.argv[3] == "before":
        os._exit(72)
    original(source, destination)
    os._exit(72)
data_cli.rename_noreplace = publish
data_cli._publish_output(Path(sys.argv[1]), Path(sys.argv[2]), b"complete", force=False)
'''
            result = subprocess.run([sys.executable, "-c", script, str(self.database),
                                     str(output), stage], timeout=10)
            self.assertEqual(result.returncode, 72)
            if stage == "after":
                self.assertEqual(output.read_bytes(), b"complete")
                self.assertEqual(output.stat().st_nlink, 1)
            else:
                self.assertFalse(output.exists())

    def test_successful_export_self_verifies(self):
        output = self.root / "archive.json"
        export_data(self.database, output)
        self.assertTrue(inspect_archive(output)["ok"])
        self.assertTrue(verify_archive(output)["ok"])
        self.assertEqual(output.stat().st_nlink, 1)

    def test_invalid_current_data_is_rejected_before_publication(self):
        self.store.save_slot(PROFILE, "2048", "autosave", {"version": 1, "score": -1})
        output = self.root / "bad.json"
        with self.assertRaises(StoreError):
            export_data(self.database, output)
        self.assertFalse(output.exists())

    def test_complete_v3_upgrades_and_remains_replaceable(self):
        source, output = self.root / "v3.json", self.root / "v4.json"
        export_data(self.database, source)

        def downgrade(value):
            value["archive_version"] = 3
            value["manifest"]["format_version"] = 3
            value["manifest"]["reader"]["min_version"] = 3
            value["manifest"]["reader"]["max_version"] = 3

        rewrite_archive(source, downgrade)
        self.assertTrue(upgrade_archive(self.database, source, output)["replace_eligible"])
        self.assertTrue(verify_archive(output)["ok"])
        self.assertTrue(restore_replace_data(self.database, output)["ok"])

    def test_replace_preview_binds_current_database(self):
        output = self.root / "archive.json"
        export_data(self.database, output)
        preview = preview_replace(self.database, output)
        self.store.set_setting(PROFILE, "sound", False)
        with self.assertRaises(StoreError) as raised:
            restore_replace_data(self.database, output,
                                 plan_fingerprint=preview["plan_fingerprint"])
        self.assertEqual(raised.exception.code, "replace_plan_changed")

    def test_evidence_publisher_needs_no_hardlinks_and_verifies_files(self):
        transaction = ImportTransaction.prepare(self.database, [
            FileOperation(self.root / "pending" / "test.json", b"hello")])
        output = self.root / "evidence.json"
        with patch("os.link", side_effect=OSError("not supported")):
            result = export_transaction_data(self.database, transaction.root.name, output)
        self.assertEqual(_validated_transaction_evidence(
            self.database, output, result["sha256"]), transaction.root.name)
        payload = json.loads(output.read_text())
        payload["files"][0]["content_base64"] = base64.b64encode(b"tamper").decode()
        raw = canonical_json(payload).encode()
        output.write_bytes(raw)
        with self.assertRaises(StoreError):
            _validated_transaction_evidence(self.database, output, hashlib.sha256(raw).hexdigest())

    def test_import_cleanup_failure_reports_success(self):
        output = self.root / "archive.json"
        export_data(self.database, output)
        with patch.object(ImportTransaction, "cleanup_terminal", return_value=self.root / "pending"):
            result = import_data(self.database, output)
        self.assertTrue(result["ok"])
        self.assertEqual(result["business_outcome"], "COMMITTED")
        self.assertEqual(result["cleanup_state"], "PENDING")

    def test_atomic_noreplace_never_overwrites(self):
        source, destination = self.root / "temp", self.root / "output"
        source.write_bytes(b"new")
        destination.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            rename_noreplace(source, destination)
        self.assertEqual(destination.read_bytes(), b"original")


class CompatibilityTests(unittest.TestCase):
    def test_windows_reparse_metadata_rejected(self):
        for mode in (0o040700, 0o100600):
            metadata = SimpleNamespace(st_mode=mode, st_nlink=1, st_file_attributes=0x400)
            self.assertFalse(is_safe_directory(metadata))
            self.assertFalse(is_safe_regular(metadata))

    def test_future_slot_is_unsupported_not_corrupt(self):
        with self.assertRaises(StoreError) as raised:
            validate_2048_state({"version": 999})
        self.assertEqual(raised.exception.code, "unsupported_slot_version")

    def test_compact_history_ten_thousand_steps(self):
        floors = {(0, 0), (1, 0), (2, 0)}
        commands = "RL" * 5000
        history, player, boxes, pushes = decode_history(commands, (0, 0), {(2, 0)}, floors)
        self.assertEqual(encode_history(history, player), commands)
        self.assertEqual(boxes, {(2, 0)})
        self.assertEqual(pushes, 0)
        self.assertLess(len(canonical_json({"commands": commands}).encode()), 11000)


@unittest.skipUnless(__import__("importlib").util.find_spec("pygame"), "pygame optional")
class SessionTests(unittest.TestCase):
    def tearDown(self):
        import pygame
        pygame.quit()

    def test_async_none_does_not_enter_practice(self):
        from client.games.sokoban import Sokoban
        future = Future()

        class Backend:
            def save_slot_async(self, *_args):
                return future

        game = Sokoban(backend=Backend(), profile_id=PROFILE)
        self.assertFalse(game._select_practice_level(0))
        self.assertFalse(game.practice_mode)
        future.set_result(None)
        game._poll_campaign_session_save()
        self.assertFalse(game.practice_mode)
        self.assertIsNone(game._campaign_session_confirmed_signature)

    def test_failed_future_unlocks_campaign_controls(self):
        from client.games.sokoban import Sokoban
        future = Future()

        class Backend:
            def save_slot_async(self, *_args):
                return future

        game = Sokoban(backend=Backend(), profile_id=PROFILE)
        game._select_practice_level(0)
        future.set_exception(OSError("full"))
        game._poll_campaign_session_save()
        self.assertIsNone(game._pending_practice_level)
        self.assertIsNone(game._campaign_snapshot)
        self.assertFalse(game.practice_mode)

    def test_cancel_queues_tombstone_after_active_save(self):
        from client.games.sokoban import Sokoban
        futures, values = [], []

        class Backend:
            def save_slot_async(self, *_args):
                values.append(_args[3])
                future = Future()
                futures.append(future)
                return future

        game = Sokoban(backend=Backend(), profile_id=PROFILE)
        game._select_practice_level(0)
        game.before_close()
        self.assertEqual(len(futures), 1)
        futures[0].set_result({"ok": True, "state_apply": "committed"})
        game._poll_campaign_session_save()
        self.assertEqual(len(futures), 2)
        self.assertFalse(values[-1]["active"])
        self.assertFalse(game.practice_mode)

    def test_restored_campaign_checkpoint_follows_move_and_undo(self):
        from client.games.sokoban import Sokoban
        values = []

        class Backend:
            def save_slot_async(self, *_args):
                values.append(_args[3])
                result = Future()
                result.set_result({"ok": True, "durable_pending": True})
                return result

        game = Sokoban(backend=Backend(), profile_id=PROFILE)
        game._restored_campaign_session_active = True
        game._try_move((1, 0))
        self.assertEqual(values[-1]["moves"], 1)
        restored = Sokoban(backend=Backend(), profile_id=PROFILE)
        self.assertTrue(restored._restore_campaign_session(values[-1]))
        self.assertEqual(restored.player_pos, game.player_pos)
        game._undo()
        self.assertEqual(values[-1]["moves"], 0)
        self.assertEqual(values[-1]["commands"], "")

    def test_durable_async_ack_enters_requested_practice(self):
        from client.games.sokoban import Sokoban
        future = Future()

        class Backend:
            def save_slot_async(self, *_args):
                return future

        game = Sokoban(backend=Backend(), profile_id=PROFILE)
        self.assertFalse(game._select_practice_level(0))
        future.set_result({"ok": True, "state_apply": "committed"})
        game._poll_campaign_session_save()
        self.assertTrue(game.practice_mode)
        self.assertIsNotNone(game._campaign_snapshot)


@unittest.skipUnless(__import__("importlib").util.find_spec("flask")
                     and __import__("importlib").util.find_spec("requests"), "optional API")
class AdapterTests(Fixture):
    def test_remote_exposure_requires_token_for_loopback_proxy(self):
        from server.app import create_app
        app = create_app({"TESTING": True, "DB_PATH": str(self.database),
                          "ALLOW_REMOTE_API": True, "API_TOKEN": "test-secret"})
        try:
            self.assertEqual(app.extensions["game_store"].db_path, self.database.resolve())
            client = app.test_client()
            self.assertEqual(client.get("/api/health").status_code, 401)
            self.assertEqual(client.get("/api/health", headers={
                "Authorization": "Bearer test-secret"}).status_code, 200)
        finally:
            # App factories own an application session until explicitly closed.
            session = app.extensions.get("application_session")
            if session is not None:
                session.close()

    def test_http_close_drains_and_repeated_close_is_typed(self):
        from client.common.network import BackendClient
        for _ in range(10):
            backend = BackendClient()
            future = backend._run_async(lambda: 42)
            result = backend.close()
            self.assertTrue(result.read_drained and result.write_drained)
            self.assertEqual(future.result(), 42)
            self.assertTrue(backend.close().read_drained)
            with self.assertRaises(RuntimeError):
                backend._run_async(lambda: 42)


@unittest.skipUnless(os.name == "nt", "Windows junction integration")
class WindowsTreeTests(Fixture):
    def test_recovery_root_and_child_junction_are_not_followed(self):
        external = self.root / "outside"
        external.mkdir()
        (external / "private.txt").write_text("must not export")
        backup = self.root / "games.db.backup-directory"
        backup.mkdir()
        for junction in (self.root / "games.db.backup-junction", backup / "child"):
            subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(external)],
                           check=True, capture_output=True, timeout=10)
            self.addCleanup(os.rmdir, junction)
        output = self.root / "archive.json"
        export_data(self.database, output, include_recovery=True)
        archive = json.loads(output.read_text())
        evidence = archive["recovery_evidence"]
        self.assertFalse(any("private.txt" in item["path"] for item in evidence))
        self.assertGreaterEqual(sum(item.get("omitted") == "unsafe_file_type"
                                    for item in evidence), 2)


if __name__ == "__main__":
    unittest.main()
