"""Eighth-review concurrency, migration and recovery checks."""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import subprocess
import sys
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
                                        PersistentStateOutbox)
from game_service.profile import ProfileIdentity
from game_service.service import SaveState, SlotLoadStatus
from game_service.store import LocalGameStore, StoreError


class StateJournalV2Tests(unittest.TestCase):
    def test_same_key_lock_serializes_another_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            marker = root / "started"
            finished = root / "finished"
            key = "setting:" + "f" * 32 + ":volume"
            script = (
                "from pathlib import Path\n"
                "import sys\n"
                "from game_service.local_backend import PersistentStateOutbox\n"
                "state,marker,finished,key=sys.argv[1:]\n"
                "Path(marker).write_text('started')\n"
                "PersistentStateOutbox(Path(state)).put("
                "key,'set_setting',('f'*32,'volume',0.5),"
                "logical_revision=2,operation_id='child')\n"
                "Path(finished).write_text('finished')\n")
            outbox = PersistentStateOutbox(state)
            with outbox._key_lock(key):
                process = subprocess.Popen(
                    [sys.executable, "-c", script, str(state), str(marker),
                     str(finished), key], cwd=Path(__file__).resolve().parents[1])
                deadline = time.monotonic() + 5.0
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                time.sleep(0.05)
                self.assertFalse(finished.exists())
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertTrue(finished.exists())

    def test_old_worker_cannot_delete_or_overwrite_new_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            first = PersistentStateOutbox(Path(directory) / "state")
            second = PersistentStateOutbox(Path(directory) / "state")
            key = "setting:" + "a" * 32 + ":volume"
            old = first.put(
                key, "set_setting", ("a" * 32, "volume", 0.2),
                logical_revision=10, operation_id="old")
            new = second.put(
                key, "set_setting", ("a" * 32, "volume", 0.8),
                logical_revision=20, operation_id="new")
            stale = first.put(
                key, "set_setting", ("a" * 32, "volume", 0.1),
                logical_revision=5, operation_id="stale")
            self.assertFalse(stale["published"])
            self.assertFalse(first.remove_if_current(
                key, old["payload_hash"]))
            self.assertEqual(
                second.list_entries()[0]["args"][2], 0.8)
            self.assertTrue(second.remove_if_current(
                key, new["payload_hash"]))

    def test_progress_journal_merges_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            profile_id = "b" * 32
            ruleset = GAME_BY_ID["sokoban"].ruleset_version
            key = f"progress:{profile_id}:sokoban:{ruleset}:campaign"
            PersistentStateOutbox(path).put(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 8, "completed_levels": [0]}, ruleset),
                logical_revision=20, operation_id="later")
            PersistentStateOutbox(path).put(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 2, "completed_levels": [1]}, ruleset),
                logical_revision=10, operation_id="earlier")
            value = PersistentStateOutbox(path).list_entries()[0]
            self.assertEqual(value["args"][3]["unlocked_level"], 8)
            self.assertEqual(value["args"][3]["completed_levels"], [0, 1])
            self.assertEqual(value["ruleset_version"], ruleset)

    def test_v1_journal_upgrades_and_freezes_ruleset(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            profile_id = "c" * 32
            key = f"progress:{profile_id}:zuma:current:campaign"
            legacy = {
                "schema_version": 1, "key": key,
                "method": "merge_progress",
                "args": [profile_id, "zuma", "campaign",
                         {"unlocked_level": 2}, None],
                "updated_at": time.time(),
            }
            legacy["payload_hash"] = outbox._digest(legacy)
            outbox.path.mkdir()
            target = outbox._target(key)
            target.write_text(
                json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            outbox.refresh_count()
            entry = outbox.list_entries()[0]
            ruleset = GAME_BY_ID["zuma"].ruleset_version
            self.assertEqual(entry["schema_version"], 3)
            self.assertEqual(entry["ruleset_version"], ruleset)
            self.assertEqual(entry["args"][4], ruleset)
            canonical = outbox._target(entry["key"])
            self.assertFalse(target.exists())
            self.assertEqual(
                json.loads(canonical.read_text(
                    encoding="utf-8"))["schema_version"], 3)
            self.assertTrue(any(outbox.migration_backup_path.iterdir()))

    def test_corrupt_state_is_quarantined_with_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            outbox.path.mkdir()
            (outbox.path / ("0" * 64 + ".json")).write_text(
                "{", encoding="utf-8")
            outbox.refresh_count()
            self.assertEqual(outbox.list_entries(), [])
            self.assertEqual(outbox.count(), 0)
            self.assertIn("已隔离 1 条", outbox.recovery_notice)
            self.assertEqual(len(list(outbox.quarantine_path.iterdir())), 1)


class StateFailureAndLoadTests(unittest.TestCase):
    def test_journal_and_database_failure_keeps_memory_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile = backend.ensure_profile_async("p").result(timeout=5)
            full = sqlite3.OperationalError("full")
            full.sqlite_errorcode = sqlite3.SQLITE_FULL
            with (mock.patch.object(
                    backend.state_outbox, "put",
                    side_effect=OSError(errno.ENOSPC, "full")),
                  mock.patch.object(
                    backend.store, "apply_state_operation", side_effect=full)):
                result = backend.set_setting_async(
                    profile["profile_id"], "volume", 0.4).result(timeout=5)
            self.assertFalse(result["ok"])
            self.assertFalse(result["durable_pending"])
            self.assertFalse(backend.pending_saves_are_durable)
            self.assertEqual(backend.failed_save_count(), 1)
            event = backend.poll_local_state_events()[-1]
            self.assertEqual(event.state, SaveState.NON_DURABLE_PENDING)

            backend.retry_failed_saves().result(timeout=5)
            self.assertTrue(backend.drain(5))
            self.assertEqual(
                backend.store.get_setting(profile["profile_id"], "volume"),
                0.4)
            self.assertTrue(backend.pending_saves_are_durable)
            backend.close()

    def test_slot_load_reports_temporary_failure_not_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile_id = "d" * 32
            error = StoreError(
                "database_busy", "busy", 503, retryable=True)
            with mock.patch.object(
                    backend.store, "load_slot", side_effect=error):
                result = backend.ensure_profile_and_load_slot_async(
                    "p", profile_id, "2048", "autosave").result(timeout=5)
            self.assertEqual(result.status, SlotLoadStatus.TEMPORARY_FAILURE)
            self.assertTrue(result.retryable)
            backend.close()

    def test_committed_receipt_reconstructs_after_memory_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            request_id = "receipt-rebuild-request-0001"
            result = backend.submit_score(
                "snake", "p", 7, request_id=request_id,
                attempt_uuid="receipt-rebuild-attempt-0001")
            backend._save_status.clear()
            event = backend.get_save_status(request_id)
            deadline = time.monotonic() + 2
            while event is None and time.monotonic() < deadline:
                time.sleep(0.01)
                event = backend.get_save_status(request_id)
            self.assertEqual(event.state, SaveState.COMMITTED)
            self.assertEqual(event.result["id"], result["id"])
            backend.close()


class ProfileAndMigrationV6Tests(unittest.TestCase):
    def test_profile_controller_discards_stale_future_and_launch(self):
        controller = ProfileController("a" * 32)
        controller.queue_launch("tetris")
        controller.resolve("c" * 32)
        self.assertEqual(
            controller.pop_ready_launch(ready=True), "tetris")
        old = Future()
        operation = controller.bind("save", old)
        controller.queue_launch("2048")
        controller.select("b" * 32)
        old.set_result({"profile_id": "a" * 32})
        self.assertFalse(controller.is_current(operation))
        self.assertIsNone(controller.pop_ready_launch(ready=True))

        current = Future()
        current.set_result({"profile_id": "b" * 32})
        operation = controller.bind("save", current)
        self.assertTrue(controller.is_current(
            controller.completed("save")))
        controller.queue_launch("zuma")
        self.assertEqual(controller.pop_ready_launch(ready=True), "zuma")

    def test_default_profile_maps_guest_and_anonymous_together(self):
        self.assertEqual(
            ProfileIdentity.from_legacy_name("guest").profile_id,
            ProfileIdentity.from_legacy_name("anonymous").profile_id)
        self.assertEqual(
            ProfileIdentity.default().profile_id,
            ProfileIdentity.from_legacy_name("guest").profile_id)

    def test_malformed_v5_state_primary_key_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            store = LocalGameStore(database)
            profile_id = store.ensure_profile("p")["profile_id"]
            with store.connection() as connection:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute("DROP TABLE settings")
                connection.execute(
                    "CREATE TABLE settings(profile_id TEXT NOT NULL, "
                    "key TEXT NOT NULL, value_json TEXT NOT NULL, "
                    "value_version INTEGER NOT NULL, updated_at REAL NOT NULL, "
                    "FOREIGN KEY(profile_id) REFERENCES profiles(profile_id) "
                    "ON DELETE CASCADE)")
                connection.execute(
                    "INSERT INTO settings VALUES(?,?,?,?,?)",
                    (profile_id, "volume", "0.6", 1, 10.0))
                connection.commit()
            repaired = LocalGameStore(database)
            self.assertEqual(repaired.get_setting(profile_id, "volume"), 0.6)
            with repaired.connection() as connection:
                indexes = repaired._table_indexes(connection, "settings")
            self.assertTrue(any(
                unique and columns == ("profile_id", "key")
                for unique, columns in indexes.values()))

    def test_profile_normalization_collision_merges_children(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "games.db"
            LocalGameStore(database)
            connection = sqlite3.connect(database)
            try:
                connection.execute("PRAGMA foreign_keys=OFF")
                for table in ("settings", "progress", "save_slots"):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute(
                    "CREATE TABLE settings(profile_id TEXT NOT NULL, "
                    "key TEXT NOT NULL, value_json TEXT NOT NULL, "
                    "value_version INTEGER NOT NULL, updated_at REAL NOT NULL, "
                    "PRIMARY KEY(profile_id,key))")
                connection.execute(
                    "CREATE TABLE progress(profile_id TEXT NOT NULL, "
                    "game_id TEXT NOT NULL, ruleset_version TEXT NOT NULL, "
                    "key TEXT NOT NULL, value_json TEXT NOT NULL, "
                    "value_version INTEGER NOT NULL, updated_at REAL NOT NULL, "
                    "PRIMARY KEY(profile_id,game_id,ruleset_version,key))")
                connection.execute(
                    "CREATE TABLE save_slots(profile_id TEXT NOT NULL, "
                    "game_id TEXT NOT NULL, slot_id TEXT NOT NULL, "
                    "state_json TEXT NOT NULL, state_version INTEGER NOT NULL, "
                    "ruleset_version TEXT NOT NULL, updated_at REAL NOT NULL, "
                    "PRIMARY KEY(profile_id,game_id,slot_id))")
                connection.execute(
                    "UPDATE schema_meta SET value='5' WHERE key='version'")
                connection.executemany(
                    "INSERT INTO profiles VALUES(?,?,?,?)",
                    [("guest", "guest", 1.0, 2.0),
                     ("anonymous", "anonymous", 1.0, 3.0)])
                connection.executemany(
                    "INSERT INTO settings VALUES(?,?,?,?,?)",
                    [("guest", "volume", "0.2", 1, 10.0),
                     ("anonymous", "volume", "0.8", 1, 20.0),
                     ("guest", "brightness", "{", 1, 30.0),
                     ("anonymous", "brightness", "0.7", 1, 20.0)])
                ruleset = GAME_BY_ID["sokoban"].ruleset_version
                connection.executemany(
                    "INSERT INTO progress VALUES(?,?,?,?,?,?,?)",
                    [("guest", "sokoban", ruleset, "campaign",
                      json.dumps({"unlocked_level": 8}), 1, 10.0),
                     ("anonymous", "sokoban", ruleset, "campaign",
                      json.dumps({"unlocked_level": 2}), 1, 20.0),
                     ("guest", "sokoban", ruleset, "practice",
                      json.dumps({"unlocked_level": 99}), 1, 30.0),
                     ("anonymous", "sokoban", ruleset, "practice",
                      json.dumps({"unlocked_level": 3}), 1, 20.0)])
                connection.executemany(
                    "INSERT INTO save_slots VALUES(?,?,?,?,?,?,?)",
                    [("guest", "2048", "autosave",
                      json.dumps({"version": 3, "slot_revision": 9}),
                      3, GAME_BY_ID["2048"].ruleset_version, 10.0),
                     ("anonymous", "2048", "autosave",
                      json.dumps({"version": 3, "slot_revision": 2}),
                      3, GAME_BY_ID["2048"].ruleset_version, 20.0),
                     ("guest", "2048", "backup", "{", 3,
                      GAME_BY_ID["2048"].ruleset_version, 30.0),
                     ("anonymous", "2048", "backup",
                      json.dumps({"version": 3, "slot_revision": 1}),
                      3, GAME_BY_ID["2048"].ruleset_version, 20.0)])
                connection.commit()
            finally:
                connection.close()

            store = LocalGameStore(database)
            profile_id = ProfileIdentity.default().profile_id
            self.assertEqual(store.get_setting(profile_id, "volume"), 0.8)
            self.assertEqual(store.get_setting(profile_id, "brightness"), 0.7)
            self.assertEqual(
                store.get_progress(profile_id, "sokoban", "campaign")
                ["unlocked_level"], 8)
            self.assertEqual(
                store.get_progress(profile_id, "sokoban", "practice")
                ["unlocked_level"], 3)
            self.assertEqual(
                store.load_slot(profile_id, "2048", "autosave")
                ["state"]["slot_revision"], 9)
            self.assertEqual(
                store.load_slot(profile_id, "2048", "backup")
                ["state"]["slot_revision"], 1)
            with store.connection() as connection:
                evidence = connection.execute(
                    "SELECT COUNT(*) FROM invalid_local_state WHERE "
                    "reason='profile_normalization_collision'").fetchone()[0]
            self.assertGreaterEqual(evidence, 2)

    def test_renamed_profile_can_continue_same_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            profile_id = "e" * 32
            first = store.record_score(
                "2048", "旧名", 32, replace=True, profile_id=profile_id,
                request_id="rename-resume-request-0001",
                attempt_uuid="rename-resume-attempt-0001", revision=1)
            store.ensure_profile("新名", profile_id)
            second = store.record_score(
                "2048", "新名", 64, replace=True,
                submission_id=first["id"], profile_id=profile_id,
                request_id="rename-resume-request-0002",
                attempt_uuid="rename-resume-attempt-0001", revision=2)
            self.assertEqual(second["id"], first["id"])
            self.assertEqual(second["score"], 64)
            self.assertEqual(store.leaderboard("2048")[0]["player"], "新名")


if __name__ == "__main__":
    unittest.main()
