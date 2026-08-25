"""Tenth-review state recovery, identity, and autosave ownership checks."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from client.games.sokoban import Sokoban
from client.games.zuma import Zuma
from client.launcher import game_profile_id
from game_service.catalog import GAME_BY_ID
from game_service.data_cli import export_data, import_data, preview_import
from game_service.local_backend import LocalBackendClient, PersistentStateOutbox
from game_service.mutation import canonical_json, normalize_score_mutation
from game_service.service import SaveState
from game_service.store import (STATE_MERGE_RECEIPT_RETENTION_DAYS,
                                LocalGameStore, StoreError)


class ProgressComponentTests(unittest.TestCase):
    def test_committed_component_and_late_merge_are_both_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            outbox = PersistentStateOutbox(root / "pending-state")
            profile_id = "a" * 32
            store.apply_state_operation(outbox._operation(
                f"profile:{profile_id}", "ensure_profile",
                ("player", profile_id), 1, "profile"))
            ruleset = GAME_BY_ID["sokoban"].ruleset_version
            key = f"progress:{profile_id}:sokoban:{ruleset}:campaign"
            newer = outbox.put(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 3, "completed_levels": [1]}, ruleset),
                logical_revision=20, operation_id="newer-component")
            store.apply_state_operation(newer["operation"])

            aggregate = outbox.put(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 8, "completed_levels": [0]}, ruleset),
                logical_revision=10, operation_id="older-component")["operation"]
            self.assertNotIn(
                aggregate["operation_id"], {"newer-component", "older-component"})
            self.assertEqual(
                {item["operation_id"] for item in aggregate["components"]},
                {"newer-component", "older-component"})
            result = store.apply_state_operation(aggregate)
            self.assertTrue(result["ok"])
            value = store.get_progress(profile_id, "sokoban", "campaign")
            self.assertEqual(value["unlocked_level"], 8)
            self.assertEqual(value["completed_levels"], [0, 1])

    def test_component_id_reuse_with_different_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = PersistentStateOutbox(Path(directory) / "state")
            profile_id = "b" * 32
            ruleset = GAME_BY_ID["zuma"].ruleset_version
            key = f"progress:{profile_id}:zuma:{ruleset}:campaign"
            outbox.put(
                key, "merge_progress",
                (profile_id, "zuma", "campaign", {"unlocked_level": 2},
                 ruleset), logical_revision=1, operation_id="same-component")
            with self.assertRaisesRegex(StoreError, "component ID"):
                outbox.put(
                    key, "merge_progress",
                    (profile_id, "zuma", "campaign", {"unlocked_level": 4},
                     ruleset), logical_revision=2,
                    operation_id="same-component")

    def test_winner_duplicate_returns_value_after_late_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            outbox = PersistentStateOutbox(root / "state")
            profile_id = "c" * 32
            store.apply_state_operation(outbox._operation(
                f"profile:{profile_id}", "ensure_profile",
                ("player", profile_id), 1, "profile"))
            ruleset = GAME_BY_ID["sokoban"].ruleset_version
            key = f"progress:{profile_id}:sokoban:{ruleset}:campaign"
            winner = outbox._operation(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 3, "completed_levels": [1]}, ruleset),
                30, "winner")
            late = outbox._operation(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 7, "completed_levels": [0]}, ruleset),
                20, "late")
            store.apply_state_operation(winner)
            store.apply_state_operation(late)
            duplicate = store.apply_state_operation(winner)
            self.assertEqual(duplicate["value"]["unlocked_level"], 7)
            self.assertEqual(duplicate["value"]["completed_levels"], [0, 1])


class ReceiptRecoveryTests(unittest.TestCase):
    def _profile_and_outbox(self, root: Path, profile_id: str):
        store = LocalGameStore(root / "games.db")
        outbox = PersistentStateOutbox(root / "state")
        store.apply_state_operation(outbox._operation(
            f"profile:{profile_id}", "ensure_profile",
            ("player", profile_id), 1, "profile"))
        return store, outbox

    def test_missing_setting_row_is_rebuilt_instead_of_fake_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            store, outbox = self._profile_and_outbox(
                Path(directory), "d" * 32)
            profile_id = "d" * 32
            operation = outbox._operation(
                f"setting:{profile_id}:volume", "set_setting",
                (profile_id, "volume", 0.6), 2, "setting")
            store.apply_state_operation(operation)
            with store.connection() as connection:
                connection.execute(
                    "DELETE FROM settings WHERE profile_id=? AND key='volume'",
                    (profile_id,))
                connection.commit()
            replay = store.apply_state_operation(operation)
            self.assertFalse(replay.get("duplicate_operation", False))
            self.assertEqual(store.get_setting(profile_id, "volume"), 0.6)

    def test_getter_quarantine_invalidates_receipts_before_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            store, outbox = self._profile_and_outbox(
                Path(directory), "e" * 32)
            profile_id = "e" * 32
            key = f"setting:{profile_id}:theme"
            operation = outbox._operation(
                key, "set_setting", (profile_id, "theme", "light"),
                2, "theme")
            store.apply_state_operation(operation)
            with store.connection() as connection:
                connection.execute(
                    "UPDATE settings SET value_json='not-json' "
                    "WHERE profile_id=? AND key='theme'", (profile_id,))
                connection.commit()
            self.assertEqual(store.get_setting(profile_id, "theme", "default"),
                             "default")
            self.assertIsNone(store.get_state_receipt(key))
            store.apply_state_operation(operation)
            self.assertEqual(store.get_setting(profile_id, "theme"), "light")

    def test_corrupt_progress_is_quarantined_and_incoming_merge_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            store, outbox = self._profile_and_outbox(
                Path(directory), "f" * 32)
            profile_id = "f" * 32
            ruleset = GAME_BY_ID["zuma"].ruleset_version
            key = f"progress:{profile_id}:zuma:{ruleset}:campaign"
            first = outbox._operation(
                key, "merge_progress",
                (profile_id, "zuma", "campaign", {"unlocked_level": 2},
                 ruleset), 2, "first")
            incoming = outbox._operation(
                key, "merge_progress",
                (profile_id, "zuma", "campaign", {"unlocked_level": 4},
                 ruleset), 3, "incoming")
            store.apply_state_operation(first)
            with store.connection() as connection:
                connection.execute(
                    "UPDATE progress SET value_json='broken' WHERE profile_id=?",
                    (profile_id,))
                connection.commit()
            store.apply_state_operation(incoming)
            self.assertEqual(
                store.get_progress(profile_id, "zuma", "campaign")
                ["unlocked_level"], 4)
            with store.connection() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM invalid_local_state WHERE "
                    "kind='progress'").fetchone()[0]
            self.assertEqual(count, 1)

    def test_corrupt_receipt_json_is_rebuilt_from_business_row(self):
        with tempfile.TemporaryDirectory() as directory:
            store, outbox = self._profile_and_outbox(
                Path(directory), "1" * 32)
            profile_id = "1" * 32
            key = f"setting:{profile_id}:volume"
            store.apply_state_operation(outbox._operation(
                key, "set_setting", (profile_id, "volume", 0.4),
                2, "volume"))
            with store.connection() as connection:
                connection.execute(
                    "UPDATE state_receipts SET result_json='{' "
                    "WHERE semantic_key=?", (key,))
                connection.commit()
            receipt = store.get_state_receipt(key)
            self.assertEqual(receipt["value"], 0.4)

    def test_duplicate_repairs_bad_result_without_changing_winner_or_row(self):
        with tempfile.TemporaryDirectory() as directory:
            store, outbox = self._profile_and_outbox(
                Path(directory), "0" * 32)
            profile_id = "0" * 32
            key = f"setting:{profile_id}:volume"
            operation = outbox._operation(
                key, "set_setting", (profile_id, "volume", 0.5),
                2, "winner-operation")
            store.apply_state_operation(operation)
            with store.connection() as connection:
                before = connection.execute(
                    "SELECT value_version FROM settings WHERE profile_id=?",
                    (profile_id,)).fetchone()[0]
                connection.execute(
                    "UPDATE state_receipts SET result_json='broken' "
                    "WHERE semantic_key=?", (key,))
                connection.commit()
            replay = store.apply_state_operation(operation)
            with store.connection() as connection:
                after = connection.execute(
                    "SELECT value_version FROM settings WHERE profile_id=?",
                    (profile_id,)).fetchone()[0]
            self.assertTrue(replay["duplicate_operation"])
            self.assertEqual(before, after)
            self.assertEqual(
                store.get_state_receipt(key)["operation_id"],
                "winner-operation")

    def test_explicit_slot_quarantine_removes_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            store, outbox = self._profile_and_outbox(
                Path(directory), "2" * 32)
            profile_id = "2" * 32
            key = f"slot:{profile_id}:2048:autosave"
            ruleset = GAME_BY_ID["2048"].ruleset_version
            operation = outbox._operation(
                key, "save_slot",
                (profile_id, "2048", "autosave", {"version": 3}, ruleset),
                2, "slot")
            store.apply_state_operation(operation)
            self.assertTrue(store.quarantine_slot(
                profile_id, "2048", "autosave", "manual_test"))
            self.assertIsNone(store.get_state_receipt(key))
            store.apply_state_operation(operation)
            self.assertIsNotNone(store.load_slot(profile_id, "2048", "autosave"))


class BaselineMigrationTests(unittest.TestCase):
    @staticmethod
    def _downgrade_receipts_to_v6(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            "DROP INDEX IF EXISTS idx_state_merge_receipts_semantic_applied;"
            "DROP TABLE state_receipts; DROP TABLE state_merge_receipts;"
            "CREATE TABLE state_receipts(semantic_key TEXT PRIMARY KEY,"
            "logical_revision INTEGER NOT NULL,operation_id TEXT NOT NULL,"
            "payload_hash TEXT NOT NULL,method TEXT NOT NULL,result_json TEXT NOT NULL,"
            "occurred_at REAL NOT NULL,applied_at REAL NOT NULL);"
            "CREATE TABLE state_merge_receipts(operation_id TEXT PRIMARY KEY,"
            "semantic_key TEXT NOT NULL,payload_hash TEXT NOT NULL,"
            "applied_at REAL NOT NULL);"
            "UPDATE schema_meta SET value='6' WHERE key='version';")
        connection.commit()
        connection.close()

    def test_v6_business_state_gets_baseline_and_rejects_old_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.db"
            store = LocalGameStore(path)
            profile_id = store.ensure_profile("new-name", "3" * 32)["profile_id"]
            store.set_setting(profile_id, "volume", 0.9)
            ruleset = GAME_BY_ID["2048"].ruleset_version
            store.save_slot(profile_id, "2048", "autosave",
                            {"version": 3, "marker": "new"}, ruleset)
            with store.connection() as connection:
                updated_at = connection.execute(
                    "SELECT updated_at FROM settings WHERE profile_id=?",
                    (profile_id,)).fetchone()[0]
            self._downgrade_receipts_to_v6(path)
            migrated = LocalGameStore(path)
            outbox = PersistentStateOutbox(Path(directory) / "state")
            old = outbox._operation(
                f"setting:{profile_id}:volume", "set_setting",
                (profile_id, "volume", 0.1),
                max(0, int(updated_at * 1_000_000_000) - 1), "old",
                updated_at=updated_at - 1)
            result = migrated.apply_state_operation(old)
            self.assertTrue(result["superseded"])
            self.assertEqual(migrated.get_setting(profile_id, "volume"), 0.9)
            profile_receipt = migrated.get_state_receipt(
                f"profile:{profile_id}")
            stale_profile = outbox._operation(
                f"profile:{profile_id}", "ensure_profile",
                ("old-name", profile_id),
                profile_receipt["logical_revision"] - 1, "old-profile",
                updated_at=profile_receipt["occurred_at"] - 1)
            self.assertTrue(
                migrated.apply_state_operation(stale_profile)["superseded"])
            self.assertEqual(migrated.last_profile()["display_name"], "new-name")
            slot_key = f"slot:{profile_id}:2048:autosave"
            slot_receipt = migrated.get_state_receipt(slot_key)
            stale_slot = outbox._operation(
                slot_key, "save_slot",
                (profile_id, "2048", "autosave",
                 {"version": 3, "marker": "old"}, ruleset),
                slot_receipt["logical_revision"] - 1, "old-slot",
                updated_at=slot_receipt["occurred_at"] - 1)
            self.assertTrue(
                migrated.apply_state_operation(stale_slot)["superseded"])
            self.assertEqual(
                migrated.load_slot(profile_id, "2048", "autosave")["state"]
                ["marker"], "new")
            self.assertGreaterEqual(
                migrated.state_high_water(), int(updated_at * 1_000_000_000))
            self.assertIsNotNone(migrated.migration_backup)

    def test_malformed_receipt_schema_is_backed_up_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.db"
            store = LocalGameStore(path)
            profile_id = store.ensure_profile("player", "4" * 32)["profile_id"]
            store.set_setting(profile_id, "theme", "light")
            with store.connection() as connection:
                connection.executescript(
                    "DROP TABLE state_receipts;"
                    "CREATE TABLE state_receipts(semantic_key TEXT,"
                    "logical_revision INTEGER,operation_id TEXT,payload_hash TEXT,"
                    "method TEXT,state_ref_json TEXT,value_hash TEXT,"
                    "receipt_kind TEXT,result_json TEXT,occurred_at REAL,"
                    "applied_at REAL);")
                connection.commit()
            repaired = LocalGameStore(path)
            self.assertIsNotNone(repaired.migration_backup)
            self.assertEqual(repaired.get_setting(profile_id, "theme"), "light")
            self.assertIsNotNone(
                repaired.get_state_receipt(f"setting:{profile_id}:theme"))

    def test_corrupt_or_huge_clock_is_quarantined_and_uses_db_high_water(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            store.ensure_profile("player", "5" * 32)
            high_water = store.state_high_water()
            outbox = PersistentStateOutbox(root / "state")
            outbox.path.mkdir(parents=True, exist_ok=True)
            (outbox.path / ".state-clock").write_text(
                str(1 << 70), encoding="ascii")
            revision = outbox.next_revision(high_water)
            self.assertGreater(revision, high_water)
            quarantined = list(outbox.quarantine_path.glob("state-clock.invalid-*"))
            self.assertEqual(len(quarantined), 1)

    def test_v6_merge_receipt_does_not_conflict_with_v3_component_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "games.db"
            store = LocalGameStore(path)
            outbox = PersistentStateOutbox(root / "state")
            profile_id = "a1" * 16
            store.apply_state_operation(outbox._operation(
                f"profile:{profile_id}", "ensure_profile",
                ("player", profile_id), 1, "profile"))
            ruleset = GAME_BY_ID["zuma"].ruleset_version
            key = f"progress:{profile_id}:zuma:{ruleset}:campaign"
            operation = outbox._operation(
                key, "merge_progress",
                (profile_id, "zuma", "campaign", {"unlocked_level": 3},
                 ruleset), 2, "old-merge")
            store.apply_state_operation(operation)
            legacy = {name: value for name, value in operation.items()
                      if name != "components"}
            legacy["schema_version"] = 2
            legacy["payload_hash"] = outbox._digest({
                name: value for name, value in legacy.items()
                if name != "payload_hash"})
            self._downgrade_receipts_to_v6(path)
            connection = sqlite3.connect(path)
            connection.execute(
                "INSERT INTO state_merge_receipts "
                "(operation_id,semantic_key,payload_hash,applied_at) "
                "VALUES(?,?,?,?)",
                ("old-merge", key, legacy["payload_hash"], time.time()))
            connection.commit()
            connection.close()

            migrated = LocalGameStore(path)
            upgraded_operation = outbox._parse(
                canonical_json(legacy).encode("utf-8"))
            result = migrated.apply_state_operation(upgraded_operation)
            self.assertTrue(result["ok"])
            self.assertEqual(
                migrated.get_progress(profile_id, "zuma", "campaign")
                ["unlocked_level"], 3)

    def test_deferred_backend_refreshes_revision_from_reopened_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "games.db"
            store = LocalGameStore(path)
            profile_id = store.ensure_profile("player", "b1" * 16)["profile_id"]
            high_water = 1 << 62
            with store.connection() as connection:
                connection.execute(
                    "UPDATE state_receipts SET logical_revision=? "
                    "WHERE semantic_key=?",
                    (high_water, f"profile:{profile_id}"))
                connection.commit()
            backend = LocalBackendClient(
                db_path=path, outbox_path=root / "pending",
                defer_initialization=True)
            self.assertTrue(backend._read_worker.drain(5))
            self.assertIsNotNone(backend.store)
            self.assertGreaterEqual(backend._last_state_revision, high_water)
            backend.close()


class IdentityAndProfileTimeTests(unittest.TestCase):
    def test_http_backend_omits_local_profile_but_local_backend_keeps_it(self):
        profile_id = "6" * 32
        http = SimpleNamespace(capabilities=frozenset({"scores"}))
        local = SimpleNamespace(capabilities=frozenset({"scores", "profiles"}))
        self.assertIsNone(game_profile_id(http, profile_id))
        self.assertEqual(game_profile_id(local, profile_id), profile_id)

    def test_duplicate_score_retry_does_not_move_last_used(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGameStore(Path(directory) / "games.db")
            mutation = normalize_score_mutation(
                "snake", "player", 10, request_id="request-time-test-0001",
                attempt_uuid="attempt-time-test-0001", revision=1)
            occurred_at = 1_700_000_000.0
            store.record_mutation(mutation, occurred_at=occurred_at)
            before = store.last_profile()["last_used"]
            time.sleep(0.01)
            store.record_mutation(mutation, occurred_at=occurred_at)
            self.assertEqual(store.last_profile()["last_used"], before)
            self.assertEqual(before, occurred_at)

    def test_progress_write_results_are_unwrapped_for_game_hud(self):
        sokoban = SimpleNamespace(
            _progress_generation={"campaign": 2, "practice": 0},
            unlocked_level=1,
            saved_completed_levels=set())
        Sokoban._apply_progress(
            sokoban, {"ok": True, "value": {
                "unlocked_level": 5, "completed_levels": [0, 2]}}, 2)
        self.assertEqual(sokoban.unlocked_level, 5)
        self.assertEqual(sokoban.saved_completed_levels, {0, 2})

        zuma = SimpleNamespace(
            _progress_generation=3, unlocked_level=1, saved_high_score=0)
        Zuma._apply_progress(
            zuma, {"ok": True, "value": {
                "unlocked_level": 4, "highest_score": 900}}, 3)
        self.assertEqual(zuma.unlocked_level, 4)
        self.assertEqual(zuma.saved_high_score, 900)


class DataArchiveTests(unittest.TestCase):
    def test_export_preview_and_atomic_import_cover_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.db"
            target_path = root / "target.db"
            archive = root / "archive.json"
            source = LocalGameStore(source_path)
            profile_id = source.ensure_profile(
                "player", "9" * 32)["profile_id"]
            source.set_setting(profile_id, "volume", 0.7)
            source.set_progress(
                profile_id, "zuma", "campaign", {"unlocked_level": 3})
            source.save_slot(
                profile_id, "2048", "manual", {
                    "version": 1, "game_state": "playing", "score": 64,
                    "won": False,
                    "grid": [[2, 0, 0, 0], [0, 0, 0, 0],
                             [0, 0, 0, 0], [0, 0, 0, 0]],
                })
            exported = export_data(source_path, archive)
            self.assertTrue(exported["ok"])

            LocalGameStore(target_path)
            preview = preview_import(target_path, archive)
            self.assertTrue(preview["ok"])
            self.assertEqual(preview["tables"]["profiles"]["new"], 1)
            imported = import_data(target_path, archive)
            self.assertTrue(Path(imported["backup"]).is_file())
            target = LocalGameStore(target_path)
            self.assertEqual(target.get_setting(profile_id, "volume"), 0.7)
            self.assertEqual(
                target.get_progress(profile_id, "zuma", "campaign")
                ["unlocked_level"], 3)
            self.assertEqual(
                target.load_slot(profile_id, "2048", "manual")["state"]
                ["score"], 64)

    def test_status_export_and_preview_do_not_modify_current_databases(self):
        from game_service.data_cli import inspect_data

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.db"
            target_path = root / "target.db"
            archive = root / "archive.json"
            LocalGameStore(source_path).ensure_profile("player", "c1" * 16)
            LocalGameStore(target_path)
            source_before = source_path.stat().st_mtime_ns
            target_before = target_path.stat().st_mtime_ns
            self.assertTrue(inspect_data(source_path)["ok"])
            export_data(source_path, archive)
            self.assertTrue(preview_import(target_path, archive)["ok"])
            self.assertEqual(source_path.stat().st_mtime_ns, source_before)
            self.assertEqual(target_path.stat().st_mtime_ns, target_before)


class AutosaveOwnershipTests(unittest.TestCase):
    @staticmethod
    def _state(owner: str, status: str = "active", *, epoch: int = 0,
               slot_revision: int = 1, expected=None) -> dict:
        expected = expected or (None, None, None, None)
        return {
            "version": 5, "owner_token": owner, "owner_status": status,
            "owner_epoch": epoch, "slot_revision": slot_revision,
            "expected_owner_token": expected[0],
            "expected_owner_epoch": expected[1],
            "expected_slot_revision": expected[2],
            "expected_value_hash": expected[3], "marker": owner,
        }

    def test_owner_takeover_blocks_delayed_old_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            outbox = PersistentStateOutbox(root / "state")
            profile_id = "7" * 32
            ruleset = GAME_BY_ID["2048"].ruleset_version
            key = f"slot:{profile_id}:2048:autosave"
            store.apply_state_operation(outbox._operation(
                f"profile:{profile_id}", "ensure_profile",
                ("player", profile_id), 1, "profile"))
            owner_a = "owner-a-000000000001"
            owner_b = "owner-b-000000000002"
            owner_c = "owner-c-000000000003"
            def operation(revision, operation_id, state):
                return outbox._operation(
                    key, "save_slot",
                    (profile_id, "2048", "autosave", state, ruleset),
                    revision, operation_id)

            store.apply_state_operation(operation(2, "a1", self._state(owner_a)))
            with self.assertRaisesRegex(StoreError, "another game window"):
                store.apply_state_operation(
                    operation(3, "b-no-takeover", self._state(owner_b)))
            current = store.load_slot(profile_id, "2048", "autosave")
            store.apply_state_operation(operation(
                4, "b-takeover", self._state(
                    owner_b, epoch=1, slot_revision=2,
                    expected=(owner_a, 0, 1, current["value_hash"]))))
            with self.assertRaisesRegex(StoreError, "another game window"):
                store.apply_state_operation(
                    operation(5, "a-late", self._state(
                        owner_a, slot_revision=2)))
            store.apply_state_operation(
                operation(6, "b-release", self._state(
                    owner_b, "released", epoch=1, slot_revision=3)))
            released = store.load_slot(profile_id, "2048", "autosave")
            store.apply_state_operation(
                operation(7, "c-claim", self._state(
                    owner_c, epoch=2, slot_revision=4,
                    expected=(owner_b, 1, 3, released["value_hash"]))))
            self.assertEqual(
                store.load_slot(profile_id, "2048", "autosave")["state"]
                ["owner_token"], owner_c)

    def test_pending_merge_components_are_protected_from_maintenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGameStore(root / "games.db")
            old = time.time() - (
                STATE_MERGE_RECEIPT_RETENTION_DAYS + 1) * 86400
            with store.connection() as connection:
                connection.execute(
                    "INSERT INTO state_merge_receipts "
                    "(operation_id,semantic_key,payload_hash,applied_at) "
                    "VALUES('protected','key',?,?)", ("a" * 64, old))
                connection.commit()
            store.maintenance(("protected",))
            with store.connection() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM state_merge_receipts WHERE "
                    "operation_id='protected'").fetchone()[0]
            self.assertEqual(count, 1)

    def test_status_reconstruction_keeps_unapplied_stale_merge_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = LocalBackendClient(
                db_path=root / "games.db", outbox_path=root / "pending")
            profile_id = backend.ensure_profile_async(
                "player", "8" * 32).result(timeout=5)["profile_id"]
            ruleset = GAME_BY_ID["sokoban"].ruleset_version
            key = f"progress:{profile_id}:sokoban:{ruleset}:campaign"
            newer = backend.state_outbox._operation(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 2}, ruleset), 100, "newer")
            backend.store.apply_state_operation(newer)
            backend.state_outbox.put(
                key, "merge_progress",
                (profile_id, "sokoban", "campaign",
                 {"unlocked_level": 7}, ruleset),
                logical_revision=50, operation_id="late-unapplied")
            backend._refresh_local_state_status(key)
            event = backend.get_local_state_status(key)
            self.assertEqual(event.state, SaveState.DURABLE_PENDING)
            backend.close()


if __name__ == "__main__":
    unittest.main()
