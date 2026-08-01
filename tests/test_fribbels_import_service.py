from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path

from src.optimizer.data import (
    INVENTORY_REPOSITORY_SCHEMA_VERSION,
    FribbelsImportErrorCategory,
    FribbelsImportIssueKind,
    FribbelsImportRequest,
    FribbelsImportRequestError,
    FribbelsImportService,
    FribbelsImportServiceError,
    FribbelsMergeResult,
    ImportHistoryRecord,
    InventoryRepository,
    merge_fribbels_inventory,
    parse_fribbels_gear_file,
    thaw_json,
)
from src.optimizer.data import fribbels_import_service as service_module
from src.optimizer.domain import GEAR_SLOT_ORDER, GearSlot, ItemStatType


FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"
IMPORTED_AT_1 = "2026-07-20T15:00:00Z"
IMPORTED_AT_2 = "2026-07-20T15:01:00Z"


def _request(
    source_path: Path,
    import_id: str = "import.fixture.1",
    imported_at: str = IMPORTED_AT_1,
) -> FribbelsImportRequest:
    return FribbelsImportRequest(
        source_path=source_path,
        import_id=import_id,
        imported_at=imported_at,
        privacy_safe_source_metadata={
            "sourceKind": "synthetic-fribbels-fixture",
            "fixtureRevision": 1,
        },
    )


def _item(
    *,
    ingame_id: str,
    gear: str = "Ring",
    main_type: str = "HealthPercent",
    main_value: int = 60,
    substat_type: str = "Speed",
    substat_value: int = 12,
    **extra: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "ingameId": ingame_id,
        "gear": gear,
        "rank": "Epic",
        "set": "HealthSet",
        "enhance": 15,
        "level": 85,
        "main": {"type": main_type, "value": main_value},
        "substats": [{"type": substat_type, "value": substat_value}],
    }
    result.update(extra)
    return result


def _write_payload(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


class FribbelsImportServiceEndToEndTests(unittest.TestCase):
    def test_in_memory_import_uses_the_same_transaction_as_a_selected_file(self) -> None:
        source = FIXTURES / "valid-enriched-export-utf8.txt"
        with tempfile.TemporaryDirectory() as directory:
            repository = InventoryRepository(Path(directory) / "optimizer.db")

            report = FribbelsImportService(repository).import_bytes(
                _request(Path("live-account-packet.json")),
                source.read_bytes(),
            )

            self.assertEqual(report.accepted_count, 2)
            self.assertEqual(len(repository.load_inventory()), 2)
            self.assertEqual(len(repository.load_heroes()), 1)

    def test_fixture_import_initializes_repository_and_returns_exact_aggregate_report(self) -> None:
        source = FIXTURES / "valid-enriched-export-utf8.txt"
        parsed = parse_fribbels_gear_file(source)
        expected_merge = merge_fribbels_inventory((), parsed)

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "nested" / "optimizer.db"
            repository = InventoryRepository(database)
            request = _request(source)
            report = FribbelsImportService(repository).import_file(request)

            self.assertTrue(database.exists())
            self.assertEqual(report.import_id, request.import_id)
            self.assertEqual(report.imported_at, IMPORTED_AT_1)
            self.assertEqual(report.source_encoding, parsed.encoding)
            self.assertEqual(report.source_variant, parsed.variant)
            self.assertEqual(report.source_item_count, 2)
            self.assertEqual(report.accepted_count, 2)
            self.assertEqual(report.rejected_count, 0)
            self.assertEqual(report.warning_count, 0)
            self.assertEqual(report.warning_item_count, 0)
            self.assertEqual(report.inserted_count, 2)
            self.assertEqual(report.updated_count, 0)
            self.assertEqual(report.unchanged_count, 0)
            self.assertEqual(report.conflict_count, 0)
            self.assertEqual(report.unseen_existing_count, 0)
            self.assertEqual(report.equipped_item_count, 1)
            self.assertEqual(report.imported_hero_count, 1)
            self.assertEqual(report.resulting_inventory_count, 2)
            self.assertEqual(report.count_for_slot(GearSlot.ARMOR), 1)
            self.assertEqual(report.count_for_slot(GearSlot.BOOTS), 1)
            self.assertEqual(tuple(slot for slot, _ in report.items_by_slot), GEAR_SLOT_ORDER)
            self.assertTrue(report.repository_created)
            self.assertFalse(report.repository_migrated)
            self.assertEqual(report.previous_schema_version, 0)
            self.assertEqual(report.schema_version, INVENTORY_REPOSITORY_SCHEMA_VERSION)
            self.assertIsNone(report.recovery_backup_path)
            self.assertEqual(report.issues, ())
            self.assertEqual(repository.load_inventory(), expected_merge.items)
            self.assertEqual(repository.load_heroes(), parsed.heroes)
            history = repository.load_import_history()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].import_id, request.import_id)
            self.assertEqual(history[0].imported_at, request.imported_at)
            self.assertEqual(
                thaw_json(history[0].source_metadata),
                {
                    "fixtureRevision": 1,
                    "sourceKind": "synthetic-fribbels-fixture",
                },
            )

    def test_same_fixture_twice_is_idempotent_with_two_history_rows(self) -> None:
        source = FIXTURES / "valid-enriched-export-utf8.txt"

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "optimizer.db"
            repository = InventoryRepository(database)
            service = FribbelsImportService(repository)

            first = service.import_file(_request(source))
            second = service.import_file(
                _request(source, "import.fixture.2", IMPORTED_AT_2)
            )

            self.assertEqual((first.inserted_count, first.unchanged_count), (2, 0))
            self.assertEqual((second.inserted_count, second.unchanged_count), (0, 2))
            self.assertEqual(second.updated_count, 0)
            self.assertFalse(second.repository_created)
            self.assertFalse(second.repository_migrated)
            self.assertEqual(len(repository.load_inventory()), 2)
            self.assertEqual(len(repository.load_import_history()), 2)
            summary = repository.inventory_summary()
            self.assertEqual(summary.total_items, 2)
            self.assertEqual(
                summary.ingame_aliases
                + summary.source_aliases
                + summary.fingerprint_aliases,
                sum(len(item.identities) for item in repository.load_inventory()),
            )

    def test_changed_source_updates_stable_item_and_preserves_user_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "selected-gear.txt"
            database = root / "optimizer.db"
            original_payload = {
                "items": [_item(ingame_id="stable-source-item")],
                "heroes": [],
            }
            _write_payload(source, original_payload)
            parsed = parse_fribbels_gear_file(source)
            initial = merge_fribbels_inventory((), parsed)
            stable_id = initial.items[0].stable_item_id
            user_metadata = {"favorite": True, "note": "user-owned"}
            seeded = FribbelsMergeResult(
                items=(replace(initial.items[0], user_metadata=user_metadata),),
                outcomes=initial.outcomes,
                unseen_existing_ids=initial.unseen_existing_ids,
                source_warnings=initial.source_warnings,
                source_rejections=initial.source_rejections,
            )
            repository = InventoryRepository(database)
            repository.initialize()
            repository.apply_import(
                seeded,
                (),
                ImportHistoryRecord.from_merge_result(
                    import_id="seed.import",
                    imported_at=IMPORTED_AT_1,
                    source_encoding=parsed.encoding,
                    source_variant=parsed.variant,
                    source_item_count=parsed.source_item_count,
                    merge_result=seeded,
                    source_metadata={"sourceKind": "synthetic-seed"},
                ),
            )
            changed_payload = {
                "items": [
                    _item(
                        ingame_id="stable-source-item",
                        substat_value=14,
                        name="Changed synthetic item",
                    )
                ],
                "heroes": [],
            }
            _write_payload(source, changed_payload)

            report = FribbelsImportService(repository).import_file(
                _request(source, "import.changed", IMPORTED_AT_2)
            )
            stored = repository.load_inventory()[0]

            self.assertEqual(report.updated_count, 1)
            self.assertEqual(report.inserted_count, 0)
            self.assertEqual(stored.stable_item_id, stable_id)
            self.assertEqual(
                dict(stored.gear_item.substats)[ItemStatType.SPEED],
                14,
            )
            self.assertEqual(thaw_json(stored.user_metadata), user_metadata)

    def test_recoverable_rows_commit_accepted_items_and_report_every_issue_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recoverable.txt"
            duplicate_id = "synthetic-duplicate-identity"
            payload = {
                "items": [
                    _item(
                        ingame_id=duplicate_id,
                        ingameEquippedId="synthetic-missing-owner",
                        locked="not-a-boolean",
                    ),
                    _item(
                        ingame_id=duplicate_id,
                        gear="Boots",
                        main_type="Speed",
                        main_value=45,
                        substat_type="AttackPercent",
                        substat_value=14,
                    ),
                    {"gear": "Ring"},
                ],
                "heroes": [],
            }
            _write_payload(source, payload)
            repository = InventoryRepository(root / "optimizer.db")

            report = FribbelsImportService(repository).import_file(_request(source))

            self.assertEqual(report.source_item_count, 3)
            self.assertEqual(report.accepted_count, 2)
            self.assertEqual(report.rejected_count, 1)
            self.assertEqual(report.warning_count, 2)
            self.assertEqual(report.warning_item_count, 1)
            self.assertEqual(report.inserted_count, 1)
            self.assertEqual(report.conflict_count, 1)
            self.assertEqual(report.equipped_item_count, 1)
            self.assertEqual(report.imported_hero_count, 0)
            self.assertEqual(report.resulting_inventory_count, 1)
            self.assertEqual(report.count_for_slot(GearSlot.RING), 1)
            self.assertEqual(len(repository.load_inventory()), 1)
            self.assertEqual(len(repository.load_import_history()), 1)
            self.assertEqual(
                tuple(issue.kind for issue in report.issues),
                (
                    FribbelsImportIssueKind.WARNING,
                    FribbelsImportIssueKind.WARNING,
                    FribbelsImportIssueKind.REJECTION,
                    FribbelsImportIssueKind.CONFLICT,
                ),
            )
            self.assertEqual(report.issues[-1].item_index, 1)
            self.assertEqual(report.issues[-1].document_path, "$.items[1]")


class FribbelsImportServiceFailureTests(unittest.TestCase):
    def test_missing_and_fatal_malformed_sources_do_not_initialize_database(self) -> None:
        cases = (
            (
                Path("missing-private-export.txt"),
                FribbelsImportErrorCategory.SOURCE_ACCESS,
                "file-read",
            ),
            (
                FIXTURES / "invalid-malformed-json.txt",
                FribbelsImportErrorCategory.DOCUMENT,
                "malformed-json",
            ),
        )
        for source, category, code in cases:
            with self.subTest(category=category):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    database = root / "new" / "optimizer.db"
                    selected = root / source if not source.is_absolute() and source.parent == Path(".") else source
                    service = FribbelsImportService(InventoryRepository(database))

                    with self.assertRaises(FribbelsImportServiceError) as raised:
                        service.import_file(_request(selected))

                    self.assertIs(raised.exception.category, category)
                    self.assertEqual(raised.exception.code, code)
                    self.assertNotIn(str(selected), str(raised.exception))
                    self.assertFalse(database.exists())
                    self.assertFalse(database.parent.exists())

    def test_future_repository_schema_is_a_structured_initialization_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "optimizer.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    f"PRAGMA user_version = {INVENTORY_REPOSITORY_SCHEMA_VERSION + 1}"
                )
                connection.commit()

            with self.assertRaises(FribbelsImportServiceError) as raised:
                FribbelsImportService(InventoryRepository(database)).import_file(
                    _request(FIXTURES / "valid-items-only-utf8.txt")
                )

            self.assertIs(
                raised.exception.category,
                FribbelsImportErrorCategory.REPOSITORY_INITIALIZATION,
            )
            self.assertEqual(raised.exception.code, "repository-initialization")
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    INVENTORY_REPOSITORY_SCHEMA_VERSION + 1,
                )

    def test_late_duplicate_history_failure_rolls_back_changed_items_and_heroes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "selected.txt"
            source.write_bytes((FIXTURES / "valid-enriched-export-utf8.txt").read_bytes())
            repository = InventoryRepository(root / "optimizer.db")
            service = FribbelsImportService(repository)
            request = _request(source, "duplicate.import")
            service.import_file(request)
            baseline_items = repository.load_inventory()
            baseline_heroes = repository.load_heroes()
            baseline_history = repository.load_import_history()
            changed = json.loads(source.read_text(encoding="utf-8"))
            changed["items"][0]["name"] = "Private changed name"
            changed["items"][0]["substats"][0]["value"] += 1
            changed["heroes"] = []
            _write_payload(source, changed)

            with self.assertRaises(FribbelsImportServiceError) as raised:
                service.import_file(request)

            self.assertIs(
                raised.exception.category,
                FribbelsImportErrorCategory.REPOSITORY_WRITE,
            )
            self.assertEqual(raised.exception.code, "repository-write")
            self.assertNotIn("Private changed name", str(raised.exception))
            self.assertEqual(repository.load_inventory(), baseline_items)
            self.assertEqual(repository.load_heroes(), baseline_heroes)
            self.assertEqual(repository.load_import_history(), baseline_history)


class FribbelsImportServiceMigrationAndPrivacyTests(unittest.TestCase):
    def test_existing_version_zero_reports_verified_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "optimizer.db"
            backup = root / "optimizer.db.import-backup"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE legacy_sentinel(value TEXT NOT NULL)")
                connection.execute("INSERT INTO legacy_sentinel VALUES('recoverable')")
                connection.commit()
            repository = InventoryRepository(
                database,
                clock=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
                backup_name_factory=lambda _path, _time: backup,
            )

            report = FribbelsImportService(repository).import_file(
                _request(FIXTURES / "valid-items-only-utf8.txt")
            )

            self.assertFalse(report.repository_created)
            self.assertTrue(report.repository_migrated)
            self.assertEqual(report.previous_schema_version, 0)
            self.assertEqual(report.recovery_backup_path, backup)
            self.assertTrue(backup.exists())
            with closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    connection.execute("SELECT value FROM legacy_sentinel").fetchone()[0],
                    "recoverable",
                )

    def test_request_report_and_issues_are_immutable_and_do_not_expose_source_path_or_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "private-account-export-name.txt"
            private_item_id = "private-item-identifier"
            private_owner_id = "private-owner-identifier"
            private_name = "Private Hero Name"
            payload = {
                "items": [
                    _item(
                        ingame_id=private_item_id,
                        ingameEquippedId=private_owner_id,
                        locked="invalid",
                    ),
                    {"privateRawField": "private-content"},
                ],
                "heroes": [{"id": private_owner_id, "name": private_name}],
            }
            _write_payload(source, payload)
            request = _request(source)
            report = FribbelsImportService(
                InventoryRepository(root / "optimizer.db")
            ).import_file(request)

            rendered = repr(report)
            request_rendered = repr(request)
            for private_value in (
                str(source),
                source.name,
                private_item_id,
                private_owner_id,
                private_name,
                "private-content",
            ):
                self.assertNotIn(private_value, rendered)
                self.assertNotIn(private_value, request_rendered)
            self.assertEqual(report.warning_count, 1)
            self.assertEqual(report.rejected_count, 1)
            self.assertEqual(len(report.issues), 2)
            with self.assertRaises(FrozenInstanceError):
                report.inserted_count = 99  # type: ignore[misc]
            with self.assertRaises(FrozenInstanceError):
                report.issues[0].message = "changed"  # type: ignore[misc]
            with self.assertRaises(FrozenInstanceError):
                request.import_id = "changed"  # type: ignore[misc]
            with self.assertRaises(TypeError):
                request.privacy_safe_source_metadata["changed"] = True  # type: ignore[index]

    def test_request_validation_is_eager_and_requires_explicit_id_time_and_safe_object(self) -> None:
        source = FIXTURES / "valid-items-only-utf8.txt"
        invalid_requests = (
            {"source_path": "", "import_id": "id", "imported_at": IMPORTED_AT_1},
            {"source_path": source, "import_id": " ", "imported_at": IMPORTED_AT_1},
            {"source_path": source, "import_id": "id", "imported_at": "today"},
            {
                "source_path": source,
                "import_id": "id",
                "imported_at": IMPORTED_AT_1,
                "privacy_safe_source_metadata": ["not-an-object"],
            },
        )
        for values in invalid_requests:
            with self.subTest(values=tuple(values)):
                with self.assertRaises(FribbelsImportRequestError):
                    FribbelsImportRequest(**values)  # type: ignore[arg-type]
        with self.assertRaises(FribbelsImportRequestError):
            FribbelsImportService(object())  # type: ignore[arg-type]

    def test_module_has_no_ui_picker_logging_print_clock_or_eligibility_dependency(self) -> None:
        source = inspect.getsource(service_module)
        for forbidden in (
            "src.desktop",
            "src.ui",
            "electron",
            "tkinter",
            "filedialog",
            "logging",
            "eligibility",
            "datetime",
            "time.time",
        ):
            self.assertNotIn(forbidden, source.lower())
        calls = {
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("print", calls)


if __name__ == "__main__":
    unittest.main()
