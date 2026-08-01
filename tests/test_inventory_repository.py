from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from src.optimizer.data import (
    INVENTORY_REPOSITORY_KIND,
    INVENTORY_REPOSITORY_SCHEMA_VERSION,
    DenseInventorySnapshot,
    FribbelsIdentityKind,
    FribbelsItemIdentity,
    FribbelsMergeResult,
    ImportHistoryRecord,
    InventoryRepository,
    InventoryRepositoryMigrationError,
    InventoryRepositorySchemaError,
    InventoryRepositoryWriteError,
    merge_fribbels_inventory,
    parse_fribbels_gear_bytes,
    parse_fribbels_gear_file,
    resolve_inventory_database_path,
    thaw_json,
)
from src.optimizer.domain import GEAR_SLOT_ORDER, GearSlot


FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"
IMPORTED_AT = "2026-07-20T12:34:56Z"


def _fixture_merge(name: str = "valid-enriched-export-utf8.txt"):
    parsed = parse_fribbels_gear_file(FIXTURES / name)
    return parsed, merge_fribbels_inventory((), parsed)


def _history(parsed, merged, import_id: str = "import-1") -> ImportHistoryRecord:
    return ImportHistoryRecord.from_merge_result(
        import_id=import_id,
        imported_at=IMPORTED_AT,
        source_encoding=parsed.encoding,
        source_variant=parsed.variant,
        source_item_count=parsed.source_item_count,
        merge_result=merged,
        source_metadata={"fixture": True, "sourceRevision": "synthetic-v1"},
    )


def _same_armor_items():
    base = {
        "gear": "Armor",
        "rank": "Epic",
        "set": "HealthSet",
        "enhance": 15,
        "level": 85,
        "main": {"type": "Defense", "value": 310},
        "substats": [
            {"type": "Speed", "value": 12, "rolls": 3},
            {"type": "HealthPercent", "value": 10, "rolls": 2},
        ],
    }
    items = [dict(base, ingameId="z-item"), dict(base, ingameId="a-item")]
    parsed = parse_fribbels_gear_bytes(
        json.dumps({"items": items, "heroes": []}, separators=(",", ":")).encode()
    )
    if parsed.rejected_count:
        raise AssertionError(parsed.rejections)
    return parsed


class InventoryRepositoryPathAndLifecycleTests(unittest.TestCase):
    def test_default_and_override_paths_are_pure_and_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            default = resolve_inventory_database_path({}, working_directory=root)
            absolute_override = root / "private-data"
            overridden = resolve_inventory_database_path(
                {"E7_USER_DATA_DIR": str(absolute_override)},
                working_directory=root / "ignored",
            )
            relative = resolve_inventory_database_path(
                {"E7_USER_DATA_DIR": "portable-data"},
                working_directory=root,
            )

            self.assertEqual(default, root / ".local" / "user-data" / "optimizer.db")
            self.assertEqual(overridden, absolute_override / "optimizer.db")
            self.assertEqual(relative, root / "portable-data" / "optimizer.db")
            self.assertFalse((root / ".local").exists())
            self.assertFalse(absolute_override.exists())
            self.assertFalse((root / "portable-data").exists())

    def test_constructor_has_no_io_and_initialize_reopens_repeatably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "nested" / "optimizer.db"
            repository = InventoryRepository(database)

            self.assertFalse(database.parent.exists())
            created = repository.initialize()
            reopened = InventoryRepository(database).initialize()

            self.assertTrue(database.exists())
            self.assertTrue(created.created)
            self.assertEqual(created.previous_version, 0)
            self.assertEqual(created.schema_version, INVENTORY_REPOSITORY_SCHEMA_VERSION)
            self.assertIsNone(created.backup_path)
            self.assertFalse(reopened.created)
            self.assertEqual(reopened.previous_version, INVENTORY_REPOSITORY_SCHEMA_VERSION)
            self.assertIsNone(reopened.backup_path)
            self.assertEqual(repository.load_inventory(), ())
            self.assertEqual(repository.load_heroes(), ())
            self.assertEqual(repository.load_import_history(), ())

    def test_current_schema_tables_indexes_metadata_and_foreign_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "optimizer.db"
            repository = InventoryRepository(database)
            repository.initialize()

            with closing(sqlite3.connect(database)) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_schema WHERE type = 'table'"
                    )
                }
                indexes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_schema WHERE type = 'index'"
                    )
                }
                metadata = dict(
                    connection.execute("SELECT key, value FROM repository_metadata")
                )
                alias_foreign_keys = connection.execute(
                    "PRAGMA foreign_key_list(item_identity_aliases)"
                ).fetchall()

            self.assertEqual(version, INVENTORY_REPOSITORY_SCHEMA_VERSION)
            self.assertTrue(
                {
                    "repository_metadata",
                    "inventory_items",
                    "item_identity_aliases",
                    "imported_heroes",
                    "import_history",
                }.issubset(tables)
            )
            self.assertTrue(
                {
                    "uq_item_identity_strong_alias",
                    "uq_item_identity_current_fingerprint",
                    "ix_item_identity_lookup",
                    "ix_inventory_slot_stable_id",
                    "ix_import_history_time",
                }.issubset(indexes)
            )
            self.assertEqual(metadata["repository_kind"], INVENTORY_REPOSITORY_KIND)
            self.assertEqual(
                metadata["schema_version"],
                str(INVENTORY_REPOSITORY_SCHEMA_VERSION),
            )
            self.assertEqual(len(alias_foreign_keys), 1)
            self.assertEqual(alias_foreign_keys[0][2], "inventory_items")
            with repository._current_connection() as owned_connection:
                self.assertEqual(
                    owned_connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    owned_connection.execute("PRAGMA busy_timeout").fetchone()[0],
                    repository.busy_timeout_ms,
                )


class InventoryRepositoryRoundTripTests(unittest.TestCase):
    def test_full_item_alias_projection_metadata_hero_and_history_round_trip(self) -> None:
        parsed, merged = _fixture_merge()
        first = replace(
            merged.items[0],
            user_metadata={
                "favorite": True,
                "labels": ["candidate", {"score": 9.5}],
            },
        )
        merged = FribbelsMergeResult(
            items=(first, *merged.items[1:]),
            outcomes=merged.outcomes,
            unseen_existing_ids=merged.unseen_existing_ids,
            source_warnings=merged.source_warnings,
            source_rejections=merged.source_rejections,
        )
        history = _history(parsed, merged)

        with tempfile.TemporaryDirectory() as directory:
            repository = InventoryRepository(Path(directory) / "optimizer.db")
            repository.initialize()
            repository.apply_import(merged, parsed.heroes, history)

            stored_items = repository.load_inventory()
            stored_heroes = repository.load_heroes()
            stored_history = repository.load_import_history()

            self.assertEqual(stored_items, tuple(sorted(merged.items, key=lambda item: item.stable_item_id)))
            self.assertEqual(stored_heroes, parsed.heroes)
            self.assertEqual(stored_history, (history,))
            self.assertEqual(
                thaw_json(stored_items[0].user_metadata),
                {"favorite": True, "labels": ["candidate", {"score": 9.5}]},
            )
            self.assertIsNone(stored_items[0].gear_item.dense_id)

    def test_reapplying_idempotent_merge_does_not_duplicate_items_or_aliases(self) -> None:
        parsed, first_merge = _fixture_merge()

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "optimizer.db"
            repository = InventoryRepository(database)
            repository.initialize()
            repository.apply_import(first_merge, parsed.heroes, _history(parsed, first_merge))

            second_merge = merge_fribbels_inventory(repository.load_inventory(), parsed)
            second_history = _history(parsed, second_merge, "import-2")
            repository.apply_import(second_merge, parsed.heroes, second_history)

            self.assertEqual(repository.load_inventory(), first_merge.items)
            self.assertEqual(len(repository.load_import_history()), 2)
            with closing(sqlite3.connect(database)) as connection:
                item_count = connection.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
                alias_count = connection.execute(
                    "SELECT COUNT(*) FROM item_identity_aliases"
                ).fetchone()[0]
            self.assertEqual(item_count, len(first_merge.items))
            self.assertEqual(alias_count, sum(len(item.identities) for item in first_merge.items))

    def test_shared_fingerprint_aliases_preserve_identical_item_multiplicity(self) -> None:
        source = _same_armor_items()
        idless_payload = {
            "items": [
                {
                    key: value
                    for key, value in thaw_json(item.raw).items()
                    if key != "ingameId"
                }
                for item in source.items
            ],
            "heroes": [],
        }
        parsed = parse_fribbels_gear_bytes(
            json.dumps(idless_payload, separators=(",", ":")).encode()
        )
        merged = merge_fribbels_inventory((), parsed)
        self.assertEqual(len(merged.items), 2)
        self.assertEqual(merged.items[0].fingerprint, merged.items[1].fingerprint)

        with tempfile.TemporaryDirectory() as directory:
            repository = InventoryRepository(Path(directory) / "optimizer.db")
            repository.initialize()
            repository.apply_import(merged, (), _history(parsed, merged))

            summary = repository.inventory_summary()
            self.assertEqual(summary.total_items, 2)
            self.assertEqual(summary.fingerprint_aliases, 2)
            self.assertEqual(summary.ingame_aliases, 0)
            self.assertEqual(repository.load_inventory(), merged.items)

    def test_duplicate_history_failure_rolls_back_items_heroes_and_history(self) -> None:
        parsed, merged = _fixture_merge()
        history = _history(parsed, merged)

        with tempfile.TemporaryDirectory() as directory:
            repository = InventoryRepository(Path(directory) / "optimizer.db")
            repository.initialize()
            repository.apply_import(merged, parsed.heroes, history)
            baseline_items = repository.load_inventory()
            baseline_heroes = repository.load_heroes()
            baseline_history = repository.load_import_history()

            changed_first = replace(merged.items[0], name="Transactional replacement")
            changed = FribbelsMergeResult(
                items=(changed_first, *merged.items[1:]),
                outcomes=merged.outcomes,
                unseen_existing_ids=merged.unseen_existing_ids,
                source_warnings=merged.source_warnings,
                source_rejections=merged.source_rejections,
            )
            with self.assertRaisesRegex(
                InventoryRepositoryWriteError,
                "no repository changes were applied",
            ):
                repository.apply_import(changed, (), history)

            self.assertEqual(repository.load_inventory(), baseline_items)
            self.assertEqual(repository.load_heroes(), baseline_heroes)
            self.assertEqual(repository.load_import_history(), baseline_history)

    def test_strong_alias_uniqueness_failure_is_atomic(self) -> None:
        parsed = _same_armor_items()
        merged = merge_fribbels_inventory((), parsed)
        duplicate = FribbelsItemIdentity(FribbelsIdentityKind.SOURCE, "duplicate-alias")
        invalid_items = tuple(
            replace(item, identities=(*item.identities, duplicate)) for item in merged.items
        )
        invalid = FribbelsMergeResult(
            items=invalid_items,
            outcomes=merged.outcomes,
            unseen_existing_ids=(),
            source_warnings=(),
            source_rejections=(),
        )

        with tempfile.TemporaryDirectory() as directory:
            repository = InventoryRepository(Path(directory) / "optimizer.db")
            repository.initialize()
            with self.assertRaises(InventoryRepositoryWriteError):
                repository.apply_import(invalid, (), _history(parsed, invalid))
            self.assertEqual(repository.inventory_summary().total_items, 0)
            self.assertEqual(repository.load_import_history(), ())


class InventoryRepositoryMigrationTests(unittest.TestCase):
    @staticmethod
    def _legacy_database(path: Path) -> None:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE legacy_sentinel(value TEXT NOT NULL)")
            connection.execute("INSERT INTO legacy_sentinel(value) VALUES('recoverable')")
            connection.execute("PRAGMA user_version = 0")
            connection.commit()

    def test_existing_version_zero_migrates_after_recoverable_adjacent_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "optimizer.db"
            backup = root / "optimizer.db.test-backup"
            self._legacy_database(database)
            repository = InventoryRepository(
                database,
                clock=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
                backup_name_factory=lambda _path, _time: backup,
            )

            initialized = repository.initialize()

            self.assertEqual(initialized.backup_path, backup)
            self.assertTrue(backup.exists())
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    INVENTORY_REPOSITORY_SCHEMA_VERSION,
                )
                self.assertEqual(
                    connection.execute("SELECT value FROM legacy_sentinel").fetchone()[0],
                    "recoverable",
                )
            with closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    connection.execute("SELECT value FROM legacy_sentinel").fetchone()[0],
                    "recoverable",
                )
                inventory_table = connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name = 'inventory_items'"
                ).fetchone()
                self.assertIsNone(inventory_table)

    def test_migration_failure_rolls_back_original_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "optimizer.db"
            backup = root / "optimizer.db.failed-backup"
            self._legacy_database(database)

            def fail_after_ddl(connection: sqlite3.Connection) -> None:
                connection.execute("CREATE TABLE failed_partial(value TEXT)")
                raise RuntimeError("injected migration failure")

            repository = InventoryRepository(
                database,
                migrations={0: fail_after_ddl},
                clock=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
                backup_name_factory=lambda _path, _time: backup,
            )

            with self.assertRaises(InventoryRepositoryMigrationError) as raised:
                repository.initialize()

            self.assertEqual(raised.exception.backup_path, backup)
            self.assertTrue(backup.exists())
            for candidate in (database, backup):
                with closing(sqlite3.connect(candidate)) as connection:
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                    self.assertEqual(
                        connection.execute("SELECT value FROM legacy_sentinel").fetchone()[0],
                        "recoverable",
                    )
                    failed_table = connection.execute(
                        "SELECT 1 FROM sqlite_schema WHERE name = 'failed_partial'"
                    ).fetchone()
                    self.assertIsNone(failed_table)

    def test_future_schema_version_is_rejected_without_mutation_or_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "optimizer.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    f"PRAGMA user_version = {INVENTORY_REPOSITORY_SCHEMA_VERSION + 1}"
                )
                connection.commit()

            with self.assertRaisesRegex(
                InventoryRepositorySchemaError,
                "update the application",
            ):
                InventoryRepository(database).initialize()

            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    INVENTORY_REPOSITORY_SCHEMA_VERSION + 1,
                )
            self.assertEqual(list(Path(directory).glob("*.backup-*")), [])

    def test_existing_backup_name_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "optimizer.db"
            requested = root / "optimizer.db.backup-fixed"
            requested.write_text("keep-me", encoding="utf-8")
            self._legacy_database(database)
            repository = InventoryRepository(
                database,
                clock=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
                backup_name_factory=lambda _path, _time: requested,
            )

            result = repository.initialize()

            self.assertEqual(requested.read_text(encoding="utf-8"), "keep-me")
            self.assertEqual(result.backup_path, root / "optimizer.db.backup-fixed-1")
            self.assertTrue(result.backup_path.exists())

    def test_documented_sqlite_backup_restore_replays_migration_without_losing_legacy_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "optimizer.db"
            backup = root / "optimizer.db.recovery-backup"
            self._legacy_database(database)
            repository = InventoryRepository(
                database,
                clock=lambda: datetime(2026, 7, 22, tzinfo=timezone.utc),
                backup_name_factory=lambda _path, _time: backup,
            )
            initialized = repository.initialize()
            self.assertEqual(backup, initialized.backup_path)
            backup_bytes = backup.read_bytes()

            database.write_bytes(b"synthetic-corrupt-primary")
            restored = root / "optimizer.db.restore-pending"
            with closing(sqlite3.connect(backup)) as source, closing(
                sqlite3.connect(restored)
            ) as destination:
                source.backup(destination)
                self.assertEqual(
                    "ok",
                    destination.execute("PRAGMA integrity_check").fetchone()[0],
                )
            os.replace(restored, database)

            reopened = InventoryRepository(
                database,
                clock=lambda: datetime(2026, 7, 22, 0, 1, tzinfo=timezone.utc),
                backup_name_factory=lambda _path, _time: backup,
            ).initialize()
            self.assertEqual(root / "optimizer.db.recovery-backup-1", reopened.backup_path)
            self.assertEqual(backup_bytes, backup.read_bytes())
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    INVENTORY_REPOSITORY_SCHEMA_VERSION,
                    connection.execute("PRAGMA user_version").fetchone()[0],
                )
                self.assertEqual(
                    "recoverable",
                    connection.execute("SELECT value FROM legacy_sentinel").fetchone()[0],
                )

    def test_mismatched_current_schema_markers_are_rejected_without_mutation_or_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "optimizer.db"
            InventoryRepository(database).initialize()
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "UPDATE repository_metadata SET value = '0' WHERE key = 'schema_version'"
                )
                connection.commit()
            mismatched = database.read_bytes()

            with self.assertRaisesRegex(InventoryRepositorySchemaError, "markers do not agree"):
                InventoryRepository(database).initialize()

            self.assertEqual(mismatched, database.read_bytes())
            self.assertEqual([], list(root.glob("optimizer.db.backup-*")))


class InventoryRepositorySummaryAndSnapshotTests(unittest.TestCase):
    def test_summary_and_dense_snapshot_are_deterministic_across_all_six_slots(self) -> None:
        parsed = _same_armor_items()
        merged = merge_fribbels_inventory((), parsed)

        with tempfile.TemporaryDirectory() as directory:
            repository = InventoryRepository(Path(directory) / "optimizer.db")
            repository.initialize()
            repository.apply_import(merged, (), _history(parsed, merged))

            summary = repository.inventory_summary()
            snapshot = repository.dense_snapshot()
            second_snapshot = repository.dense_snapshot()

            self.assertIsInstance(snapshot, DenseInventorySnapshot)
            self.assertEqual(snapshot, second_snapshot)
            self.assertEqual(
                tuple(slot for slot, _ in summary.items_by_slot),
                GEAR_SLOT_ORDER,
            )
            self.assertEqual(
                tuple(slot for slot, _ in snapshot.items_by_slot),
                GEAR_SLOT_ORDER,
            )
            self.assertEqual(summary.count_for_slot(GearSlot.ARMOR), 2)
            for slot in GEAR_SLOT_ORDER:
                expected = 2 if slot is GearSlot.ARMOR else 0
                self.assertEqual(len(snapshot.items_for_slot(slot)), expected)
            armor_ids = tuple(item.item_id for item in snapshot.items_for_slot(GearSlot.ARMOR))
            self.assertEqual(armor_ids, tuple(sorted(armor_ids)))
            self.assertEqual(
                snapshot.dense_id_to_stable_id,
                tuple(enumerate(armor_ids)),
            )
            self.assertEqual(
                tuple(item.dense_id for item in snapshot.items_for_slot(GearSlot.ARMOR)),
                (0, 1),
            )
            self.assertEqual(snapshot.stable_item_id_for_dense_id(1), armor_ids[1])
            self.assertTrue(all(item.gear_item.dense_id is None for item in repository.load_inventory()))


if __name__ == "__main__":
    unittest.main()
