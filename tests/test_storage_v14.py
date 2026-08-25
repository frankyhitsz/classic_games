"""Regression coverage for the sixteenth local-first review."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from client.games.game_2048 import Game2048
from client.games.sokoban import Sokoban
from game_service.data_cli import (export_data, import_data, inspect_archive,
                                   preview_import, _guard_export_target,
                                   _require_import_space)
from game_service.import_transaction import (ImportTransaction,
                                             has_import_transaction_roots,
                                             recover_import_transactions)
from game_service.local_backend import (LocalBackendClient,
                                        PendingSaveEnvelope,
                                        PersistentSaveOutbox,
                                        PersistentStateOutbox)
from game_service.maintenance import inactive_application_lock
from game_service.mutation import canonical_json, normalize_score_mutation
from game_service.service import SaveState
from game_service.store import LocalGameStore, StoreError


PROFILE_ID = "abcdef0123456789abcdef0123456789"


def _mutation(request_id: str = "sixteenth-request-000001"):
    return normalize_score_mutation(
        "snake", "review", 12, profile_id=PROFILE_ID,
        request_id=request_id, attempt_uuid="sixteenth-attempt-000001",
        revision=1)


def _rewrite_archive(path: Path, mutate) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    value["manifest_hash"] = hashlib.sha256(canonical_json({
        key: child for key, child in value.items()
        if key != "manifest_hash"
    }).encode("utf-8")).hexdigest()
    path.write_text(canonical_json(value), encoding="utf-8")
    return value


class JournalPublicationTests(unittest.TestCase):
    def test_score_publish_never_creates_a_hard_link_window(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentSaveOutbox(Path(directory) / "pending")
            with patch("game_service.local_backend.os.link",
                       side_effect=AssertionError("hard link publication")):
                envelope = outbox.add_mutation(_mutation())
            target = outbox.path / f"{envelope.request_id}.json"
            self.assertEqual(os.lstat(target).st_nlink, 1)
            self.assertEqual(outbox.list_envelopes()[0][0], envelope)

    def test_score_orphan_lock_timeout_preserves_the_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentSaveOutbox(
                Path(directory) / "pending", maintain=False)
            outbox.path.mkdir()
            envelope = PendingSaveEnvelope.from_mutation(_mutation())
            temporary = outbox.path / ".waiting.tmp"
            temporary.write_text(
                canonical_json(envelope.to_dict()), encoding="utf-8")
            os.utime(temporary, (1, 1))
            with patch.object(
                    outbox, "_request_lock",
                    side_effect=StoreError(
                        "spool_lock_timeout", "busy", 503, retryable=True)):
                outbox._recover_orphan_temps()
            self.assertTrue(temporary.is_file())
            self.assertFalse(outbox._target(envelope.request_id).exists())

    def test_corrupt_state_is_not_overwritten_if_quarantine_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(
                Path(directory) / "pending-state", recover=False)
            outbox.path.mkdir()
            key = f"setting:{PROFILE_ID}:volume"
            target = outbox._target(key)
            target.write_bytes(b"damaged")
            with patch.object(outbox, "_quarantine_locked", return_value=False):
                with self.assertRaises(StoreError) as raised:
                    outbox.put(
                        key, "set_setting", (PROFILE_ID, "volume", 0.5),
                        logical_revision=1, operation_id="safe-replacement")
            self.assertEqual(raised.exception.code, "state_quarantine_failed")
            self.assertEqual(target.read_bytes(), b"damaged")

    def test_state_clock_symlink_is_quarantined_without_following_it(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outbox = PersistentStateOutbox(root / "pending-state", recover=False)
            outbox.path.mkdir()
            outside = root / "outside"
            outside.write_text("123", encoding="ascii")
            clock = outbox.path / ".state-clock"
            clock.symlink_to(outside)
            revision = outbox.next_revision()
            self.assertGreater(revision, 0)
            self.assertEqual(outside.read_text(encoding="ascii"), "123")
            self.assertFalse(clock.is_symlink())

    def test_state_orphan_lock_timeout_preserves_the_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(
                Path(directory) / "pending-state", recover=False)
            outbox.path.mkdir()
            key = f"setting:{PROFILE_ID}:volume"
            operation = outbox._operation(
                key, "set_setting", (PROFILE_ID, "volume", 0.5),
                1, "waiting-state")
            target = outbox._target(key)
            temporary = outbox.path / f".{target.name}.waiting.tmp"
            temporary.write_text(canonical_json(operation), encoding="utf-8")
            os.utime(temporary, (1, 1))
            with patch.object(
                    outbox, "_key_lock",
                    side_effect=StoreError(
                        "state_lock_timeout", "busy", 503, retryable=True)):
                outbox._recover_orphan_temps()
            self.assertTrue(temporary.is_file())
            self.assertFalse(target.exists())

    def test_orphan_recovery_preserves_corrupt_target_before_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentSaveOutbox(
                Path(directory) / "pending", maintain=False)
            outbox.path.mkdir()
            envelope = PendingSaveEnvelope.from_mutation(_mutation())
            target = outbox._target(envelope.request_id)
            target.write_bytes(b"damaged")
            temporary = outbox.path / ".valid.tmp"
            temporary.write_text(
                canonical_json(envelope.to_dict()), encoding="utf-8")
            os.utime(temporary, (1, 1))
            outbox._recover_orphan_temps()
            self.assertEqual(outbox._read_file(target)[0], envelope)
            self.assertEqual(len(list(outbox.quarantine_path.iterdir())), 1)


class StateResolutionTests(unittest.TestCase):
    @staticmethod
    def _operation(method: str, value: dict, revision: int, identity: str):
        key = f"progress:{PROFILE_ID}:sokoban:sokoban-campaign-2:campaign"
        return PersistentStateOutbox._operation(
            key, method,
            (PROFILE_ID, "sokoban", "campaign", value,
             "sokoban-campaign-2"),
            revision, identity, updated_at=time.time())

    def test_two_set_progress_operations_use_lww_not_component_merge(self):
        older = self._operation("set_progress", {"unlocked_level": 5}, 1, "a")
        newer = self._operation("set_progress", {"unlocked_level": 2}, 2, "b")
        winner, resolution = PersistentStateOutbox.resolve_operations(
            older, newer)
        self.assertEqual(resolution, "incoming")
        self.assertEqual(winner["method"], "set_progress")
        self.assertEqual(winner["args"][3]["unlocked_level"], 2)

    def test_set_then_merge_creates_valid_monotonic_components(self):
        baseline = self._operation(
            "set_progress", {"unlocked_level": 3}, 1, "baseline")
        delta = self._operation(
            "merge_progress", {"unlocked_level": 6}, 2, "delta")
        winner, resolution = PersistentStateOutbox.resolve_operations(
            baseline, delta)
        self.assertEqual((resolution, winner["method"]),
                         ("merged", "merge_progress"))
        self.assertEqual(winner["args"][3]["unlocked_level"], 6)
        self.assertEqual(len(winner["components"]), 2)
        self.assertEqual(PersistentStateOutbox._parse(
            canonical_json(winner).encode("utf-8")), winner)

    def test_future_state_timestamp_is_rejected(self):
        key = f"progress:{PROFILE_ID}:sokoban:sokoban-campaign-2:campaign"
        with self.assertRaises(StoreError) as raised:
            PersistentStateOutbox._operation(
                key, "set_progress",
                (PROFILE_ID, "sokoban", "campaign",
                 {"unlocked_level": 3}, "sokoban-campaign-2"),
                1, "future", updated_at=time.time() + 2 * 24 * 60 * 60)
        self.assertEqual(raised.exception.code, "invalid_state_timestamp")

    def test_state_event_identity_breaks_equal_revision_ties(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = LocalBackendClient(db_path=Path(directory) / "games.db")
            try:
                high = {"key": "setting:x:k", "kind": "setting",
                        "logical_revision": 9, "operation_id": "z",
                        "payload_hash": "1" * 64}
                low = {**high, "operation_id": "a", "payload_hash": "2" * 64}
                backend._emit_local_state_event(
                    high, SaveState.DURABLE_PENDING, {})
                backend._emit_local_state_event(low, SaveState.COMMITTED, {})
                event = backend._local_state_status[high["key"]]
                self.assertEqual((event.operation_id, event.payload_hash),
                                 ("z", "1" * 64))
            finally:
                backend.close()


class ImportRecoveryTests(unittest.TestCase):
    def test_terminal_transaction_root_does_not_block_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            transaction = ImportTransaction.prepare(database, [])
            transaction.mark("COMPLETED")
            self.assertTrue(transaction.root.is_dir())
            self.assertFalse(has_import_transaction_roots(database))
            recover_import_transactions(database)
            self.assertFalse(transaction.root.exists())

    def test_regular_file_transaction_root_requires_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            unsafe = root / ".games.db.import-file"
            unsafe.write_text("not a directory", encoding="utf-8")
            self.assertTrue(has_import_transaction_roots(database))
            with self.assertRaises(StoreError):
                recover_import_transactions(database)
            self.assertTrue(unsafe.is_file())

    def test_raw_database_fallback_refuses_untracked_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            database.write_bytes(b"not sqlite")
            Path(f"{database}-wal").write_bytes(b"untracked")
            with self.assertRaises(StoreError) as raised:
                ImportTransaction.prepare(
                    database, [], allow_raw_database_fallback=True)
            self.assertEqual(
                raised.exception.code,
                "import_raw_sidecars_require_manual_recovery")


class ArchiveBoundaryTests(unittest.TestCase):
    def test_export_is_snapshot_only_unless_repair_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            LocalGameStore(database)
            outbox = PersistentSaveOutbox(root / "pending", maintain=False)
            outbox.path.mkdir()
            envelope = PendingSaveEnvelope.from_mutation(_mutation())
            temporary = outbox.path / ".old.tmp"
            temporary.write_text(
                canonical_json(envelope.to_dict()), encoding="utf-8")
            os.utime(temporary, (1, 1))
            with self.assertRaises(StoreError):
                export_data(database, root / "snapshot.json")
            self.assertTrue(temporary.is_file())
            result = export_data(
                database, root / "repaired.json", repair_before_export=True)
            self.assertTrue(result["repair_before_export"])
            self.assertEqual(result["repair_report"]["resolved_active_paths"],
                             ["pending/.old.tmp"])
            self.assertFalse(temporary.exists())

    def test_archive_inspection_has_no_local_recovery_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            archive = root / "archive.json"
            LocalGameStore(database)
            export_data(database, archive)
            with patch("game_service.data_cli.recover_import_transactions",
                       side_effect=AssertionError("unexpected repair")):
                result = inspect_archive(archive)
            self.assertTrue(result["ok"])

    def test_import_space_preflight_fails_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            archive = root / "archive.json"
            database.write_bytes(b"database")
            archive.write_bytes(b"archive")
            usage = type("Usage", (), {"free": 1})()
            with patch("game_service.data_cli.shutil.disk_usage",
                       return_value=usage):
                with self.assertRaises(StoreError) as raised:
                    _require_import_space(
                        database, archive, replacement=True)
            self.assertEqual(raised.exception.code, "insufficient_import_space")

    def test_nested_nonexistent_reserved_export_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "games.db"
            with self.assertRaises(StoreError) as raised:
                _guard_export_target(
                    database,
                    root / ".games.db.fresh-replace-future" / "archive.json",
                    force=False)
            self.assertEqual(raised.exception.code, "unsafe_export_target")

    def test_historical_pending_is_preserved_as_evidence_not_activated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            target_root = root / "target"
            source_root.mkdir()
            target_root.mkdir()
            source = source_root / "games.db"
            target = target_root / "games.db"
            archive = root / "archive.json"
            LocalGameStore(source)
            PersistentSaveOutbox(source_root / "pending").add_mutation(_mutation())
            export_data(source, archive)

            def make_historical(value):
                pending = value["pending_scores"][0]
                pending["payload"]["game_id"] = "retired-game"
                pending["payload"]["ruleset_version"] = "retired-1"

            _rewrite_archive(archive, make_historical)
            LocalGameStore(target)
            preview = preview_import(target, archive)
            self.assertTrue(preview["ok"], preview)
            result = import_data(target, archive)
            self.assertTrue(result["ok"])
            self.assertEqual(result["pending_restored"]["scores"], 0)
            self.assertEqual(
                result["pending_restored"]["historical_evidence_only"][
                    "scores"], 1)
            self.assertFalse((target_root / "pending" / (
                _mutation().request_id + ".json")).exists())
            evidence = list(
                (target_root / "imported-recovery").rglob("*.json"))
            self.assertEqual(len(evidence), 1)
            self.assertIn("retired-game", evidence[0].read_text(encoding="utf-8"))


class GameAndAdapterTests(unittest.TestCase):
    def test_2048_uses_an_injected_random_source(self):
        class FixedRandom:
            def __init__(self):
                self.choices = 0

            def choice(self, values):
                self.choices += 1
                return values[-1]

            @staticmethod
            def random():
                return 0.95

        rng = FixedRandom()
        game = Game2048(backend=object(), profile_id=PROFILE_ID, rng=rng)
        self.assertEqual(rng.choices, 2)
        self.assertTrue(all(tile.value == 4 for tile in game.tiles))

    def test_sokoban_keeps_completed_future_until_it_is_polled(self):
        first = Future()
        first.set_result({"ok": True})
        second = Future()

        class Backend:
            def __init__(self):
                self.calls = []

            def merge_progress_async(self, *args):
                self.calls.append(args)
                return first if len(self.calls) == 1 else second

        game = Sokoban.__new__(Sokoban)
        game.backend = Backend()
        game.profile_id = PROFILE_ID
        game._progress_generation = {"campaign": 0, "practice": 0}
        game._progress_write_futures = {"campaign": None, "practice": None}
        game._progress_write_queued = {"campaign": None, "practice": None}
        game._progress_save_messages = {"campaign": "", "practice": ""}
        game._queue_progress_write("campaign", {"unlocked_level": 2})
        original = game._progress_write_futures["campaign"]
        game._queue_progress_write("campaign", {"unlocked_level": 3})
        self.assertIs(game._progress_write_futures["campaign"], original)
        self.assertEqual(
            game._progress_write_queued["campaign"][1]["unlocked_level"], 3)

    def test_sokoban_restores_durable_practice_return_session(self):
        saved = {}

        class Backend:
            pending_saves_are_durable = True

            def publish_slot_intent(self, _profile, _game, _slot, state, *_args):
                saved.clear()
                saved.update(state)
                return {"ok": True, "durable_pending": True}

            def load_slot_async(self, *_args):
                future = Future()
                future.set_result({"state": dict(saved)} if saved else None)
                return future

            @staticmethod
            def failed_save_count():
                return 0

        first = Sokoban(backend=Backend(), profile_id=PROFILE_ID)
        first.load_level(1, practice=False, new_campaign=False)
        first.player_pos = next(
            point for point in first.floors
            if point not in first.boxes and point != first.player_pos)
        first.moves = 7
        expected = (first.level_idx, first.player_pos, first.moves)
        first._capture_campaign_session()
        self.assertTrue(saved.get("active"))

        restored = Sokoban(backend=Backend(), profile_id=PROFILE_ID)
        restored._poll_campaign_session()
        self.assertEqual(
            (restored.level_idx, restored.player_pos, restored.moves), expected)
        self.assertFalse(restored.practice_mode)

    def test_store_paths_are_canonical_and_prebuilt_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            self.assertTrue(store.db_path.is_absolute())
            with self.assertRaises(ValueError):
                LocalBackendClient(store=store, db_path=root / "other.db")

    def test_backend_constructor_closes_first_worker_if_second_fails(self):
        class FirstWorker:
            def __init__(self):
                self.closed = False

            def close(self, **_kwargs):
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            first = FirstWorker()
            with patch("game_service.local_backend.LocalWriteWorker",
                       side_effect=[first, RuntimeError("second worker")]):
                with self.assertRaises(RuntimeError):
                    LocalBackendClient(db_path=Path(directory) / "games.db")
            self.assertTrue(first.closed)

    def test_backend_constructor_releases_session_if_first_worker_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            with patch("game_service.local_backend.LocalWriteWorker",
                       side_effect=RuntimeError("first worker")):
                with self.assertRaises(RuntimeError):
                    LocalBackendClient(db_path=database)
            with inactive_application_lock(database, timeout=0.2):
                pass

    def test_non_loopback_api_requires_explicit_unsafe_confirmation(self):
        from server import app as server_app

        with patch.dict(os.environ, {"GAMES_HOST": "0.0.0.0"}, clear=False):
            os.environ.pop("GAMES_UNSAFE_EXPOSE", None)
            with patch.object(
                    server_app, "create_app",
                    side_effect=AssertionError("must reject before app setup")):
                with self.assertRaises(SystemExit):
                    server_app.main()


if __name__ == "__main__":
    unittest.main()
