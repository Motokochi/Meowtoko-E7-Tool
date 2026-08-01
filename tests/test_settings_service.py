import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from src.core.settings_service import (
    SETTINGS_SCHEMA_VERSION,
    SettingsConflictError,
    SettingsReadOnlyError,
    SettingsService,
    SettingsStorage,
    SettingsValidationError,
    SettingsWriteError,
    default_settings,
    migrate_document,
    protocol_settings_to_document,
)
from src.desktop.settings_controller import SettingsController


def legacy_document():
    document = default_settings()
    document.pop("schema_version")
    document.pop("appearance")
    document["automation"]["enhancement_read_retries"] = 2.0
    return document


class SettingsServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "user_data" / "settings.json"
        self.service = SettingsService(self.path)

    def write_json(self, value, path=None):
        target = path or self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2), encoding="utf-8")

    def read_json(self, path=None):
        return json.loads((path or self.path).read_text(encoding="utf-8"))

    def test_missing_file_loads_complete_defaults_without_writing(self):
        snapshot = self.service.load()

        self.assertEqual(snapshot.source, "defaults")
        self.assertEqual(snapshot.schema_version, SETTINGS_SCHEMA_VERSION)
        self.assertEqual(snapshot.document["appearance"]["theme"], "system")
        self.assertEqual(
            snapshot.document["click_points"]["probe_ingredient"],
            {"x": 1060, "y": 170},
        )
        self.assertEqual(
            snapshot.document["click_points"]["probe_select"],
            {"x": 640, "y": 490},
        )
        self.assertEqual(snapshot.revision, "missing")
        self.assertFalse(self.path.exists())

    def test_partial_legacy_file_is_migrated_in_memory_and_defaults_are_merged(self):
        self.write_json({"target_window": "LDPlayer", "automation": {"after_enhance_seconds": 7.5}})

        snapshot = self.service.load()

        self.assertEqual(snapshot.migrated_from, 0)
        self.assertEqual(snapshot.document["schema_version"], 1)
        self.assertEqual(snapshot.document["target_window"], "LDPlayer")
        self.assertEqual(snapshot.document["automation"]["after_enhance_seconds"], 7.5)
        self.assertIn("destroy_confirm", snapshot.document["click_points"])
        self.assertNotIn("schema_version", self.read_json())

    def test_update_preserves_unknown_keys_and_creates_backup(self):
        original = legacy_document()
        original["future_plugin"] = {"enabled": True, "nested": {"value": 9}}
        self.write_json(original)
        snapshot = self.service.load()

        updated = self.service.update(snapshot.revision, {
            "targetWindow": "Epic Seven - Test",
            "appearance": {"theme": "dark"},
        })
        persisted = self.read_json()

        self.assertEqual(updated.document["target_window"], "Epic Seven - Test")
        self.assertEqual(persisted["schema_version"], 1)
        self.assertEqual(persisted["appearance"]["theme"], "dark")
        self.assertEqual(persisted["future_plugin"], original["future_plugin"])
        self.assertEqual(json.loads(Path(f"{self.path}.bak").read_text(encoding="utf-8")), original)

    def test_malformed_primary_uses_defaults_and_preserves_original_before_save(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not valid json", encoding="utf-8")
        snapshot = self.service.load()

        self.assertEqual(snapshot.source, "defaults")
        self.assertIn("invalid", snapshot.warning.lower())

        self.service.update(snapshot.revision, {"targetWindow": "Recovered"})

        self.assertEqual(self.read_json()["target_window"], "Recovered")
        self.assertEqual(Path(f"{self.path}.corrupt").read_text(encoding="utf-8"), "{not valid json")

    def test_invalid_primary_recovers_from_valid_backup(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[]", encoding="utf-8")
        backup = Path(f"{self.path}.bak")
        self.write_json(legacy_document(), backup)

        snapshot = self.service.load()

        self.assertEqual(snapshot.source, "backup")
        self.assertIn("backup", snapshot.warning.lower())
        self.assertEqual(snapshot.document["target_window"], "Epic Seven")

    def test_future_schema_is_read_only_and_keeps_unknown_data(self):
        future = default_settings()
        future["schema_version"] = 99
        future["future_only"] = {"value": "keep"}
        self.write_json(future)
        snapshot = self.service.load()

        self.assertTrue(snapshot.read_only)
        self.assertEqual(snapshot.schema_version, 99)
        self.assertEqual(snapshot.document["future_only"], {"value": "keep"})
        with self.assertRaises(SettingsReadOnlyError):
            self.service.update(snapshot.revision, {"targetWindow": "No"})

    def test_validation_rejects_bad_values_and_unknown_protocol_fields(self):
        snapshot = self.service.load()

        with self.assertRaises(SettingsValidationError) as invalid:
            self.service.update(snapshot.revision, {
                "regions": {"mainStat": {"width": 0}},
            })
        self.assertIn("regions.main_stat.width", invalid.exception.issues)

        with self.assertRaises(SettingsValidationError) as invalid_cooldown:
            self.service.update(snapshot.revision, {
                "automation": {"afterEnhanceSeconds": 1.9},
            })
        self.assertIn("automation.after_enhance_seconds", invalid_cooldown.exception.issues)

        with self.assertRaises(SettingsValidationError):
            self.service.update(snapshot.revision, {"shellCommand": "anything"})
        self.assertFalse(self.path.exists())

    def test_stale_revision_is_rejected_without_writing(self):
        snapshot = self.service.load()
        self.service.update(snapshot.revision, {"targetWindow": "First"})

        with self.assertRaises(SettingsConflictError):
            self.service.update(snapshot.revision, {"targetWindow": "Stale"})

        self.assertEqual(self.read_json()["target_window"], "First")

    def test_atomic_replace_failure_leaves_primary_unchanged(self):
        original = default_settings()
        self.write_json(original)

        def fail_primary(source, target):
            if Path(target) == self.path:
                raise OSError("disk failure")
            os.replace(source, target)

        service = SettingsService(self.path, SettingsStorage(fail_primary))
        snapshot = service.load()

        with self.assertRaises(SettingsWriteError):
            service.update(snapshot.revision, {"targetWindow": "Must not land"})

        self.assertEqual(self.read_json(), original)
        self.assertFalse(any(self.path.parent.glob("*.tmp")))

    def test_backup_publish_failure_leaves_primary_unchanged_and_no_owned_temp(self):
        original = default_settings()
        self.write_json(original)

        def fail_backup(source, target):
            if Path(target) == Path(f"{self.path}.bak"):
                raise OSError("injected backup interruption")
            os.replace(source, target)

        service = SettingsService(self.path, SettingsStorage(fail_backup))
        snapshot = service.load()
        with self.assertRaises(SettingsWriteError):
            service.update(snapshot.revision, {"targetWindow": "Must not land"})

        self.assertEqual(self.read_json(), original)
        self.assertFalse(Path(f"{self.path}.bak").exists())
        self.assertFalse(any(self.path.parent.glob("*.tmp")))

    def test_corrupt_archive_failure_preserves_malformed_primary_unchanged(self):
        self.path.parent.mkdir(parents=True)
        malformed = b"{synthetic-not-json"
        self.path.write_bytes(malformed)

        def fail_corrupt(source, target):
            if Path(target) == Path(f"{self.path}.corrupt"):
                raise OSError("injected corrupt archive interruption")
            os.replace(source, target)

        service = SettingsService(self.path, SettingsStorage(fail_corrupt))
        snapshot = service.load()
        with self.assertRaises(SettingsWriteError):
            service.update(snapshot.revision, {"targetWindow": "Must not land"})

        self.assertEqual(malformed, self.path.read_bytes())
        self.assertFalse(Path(f"{self.path}.corrupt").exists())
        self.assertFalse(any(self.path.parent.glob("*.tmp")))

    def test_saved_settings_survive_a_new_service_instance(self):
        first = self.service.load()
        saved = self.service.update(first.revision, {
            "targetWindow": "Restart Test",
            "adb": {"deviceSerial": "emulator-test"},
        })

        restarted = SettingsService(self.path).load()

        self.assertEqual(restarted.revision, saved.revision)
        self.assertEqual(restarted.document["target_window"], "Restart Test")
        self.assertEqual(restarted.document["adb"]["device_serial"], "emulator-test")

    def test_migration_is_idempotent(self):
        first, migrated_from, read_only = migrate_document(legacy_document())
        second, migrated_again, second_read_only = migrate_document(first)

        self.assertEqual(migrated_from, 0)
        self.assertFalse(read_only)
        self.assertIsNone(migrated_again)
        self.assertFalse(second_read_only)
        self.assertEqual(second, first)

    def test_controller_emits_the_saved_snapshot(self):
        events = []
        controller = SettingsController(self.service, events.append)
        initial = controller.get_snapshot()

        saved = controller.update(initial["revision"], {"appearance": {"theme": "light"}})

        self.assertEqual(events, [saved])
        self.assertEqual(saved["settings"]["appearance"]["theme"], "light")

    def test_real_shape_fixture_round_trips_without_loss(self):
        fixture = legacy_document()
        fixture["target_window"] = "Epic Seven"
        fixture["adb"].update({
            "adb_path": r"D:\Emulator\adb.exe",
            "device_serial": "emulator-5554",
        })
        fixture["unrecognized_legacy_section"] = {"keep": [1, 2, 3]}
        self.write_json(copy.deepcopy(fixture))
        snapshot = self.service.load()

        self.service.update(snapshot.revision, snapshot.to_dict()["settings"])
        persisted = self.read_json()

        self.assertEqual(persisted["unrecognized_legacy_section"], fixture["unrecognized_legacy_section"])
        self.assertEqual(persisted["regions"], fixture["regions"])
        self.assertEqual(persisted["click_points"], fixture["click_points"])
        self.assertEqual(persisted["adb"], fixture["adb"])

    def test_complete_protocol_settings_validate_without_writing(self):
        protocol_settings = self.service.load().to_dict()["settings"]
        protocol_settings["targetWindow"] = "Unsaved Preview"

        document = protocol_settings_to_document(protocol_settings)

        self.assertEqual(document["target_window"], "Unsaved Preview")
        self.assertEqual(document["regions"]["main_stat"], protocol_settings["regions"]["mainStat"])
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
