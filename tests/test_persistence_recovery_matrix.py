from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.core.settings_service import SETTINGS_SCHEMA_VERSION, SettingsService
from src.desktop.optimizer_profile_service import (
    DESKTOP_PROFILE_DIRECTORY,
    OptimizerProfileService,
    OptimizerProfileServiceError,
)
from src.optimizer.data import (
    INVENTORY_REPOSITORY_SCHEMA_VERSION,
    OPTIMIZER_PROFILE_CURRENT_VERSION,
    RUN_MANIFEST_CURRENT_VERSION,
    FribbelsMergeResult,
    ImportHistoryRecord,
    InventoryRepository,
    load_run_manifest_json,
    merge_fribbels_inventory,
    parse_fribbels_gear_file,
)
from src.optimizer.result_store import ResultRunStore


FIXTURES = Path(__file__).parent / "fixtures"
RECOVERY_MANIFEST = FIXTURES / "recovery" / "manifest.json"
FRIBBELS_FIXTURE = FIXTURES / "fribbels" / "valid-enriched-export-utf8.txt"
RUN_MANIFEST_FIXTURE = FIXTURES / "optimizer" / "run-manifest-v1.json"


def _corpus() -> dict:
    value = json.loads(RECOVERY_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("Recovery corpus root must be an object.")
    return value


def _downgrade_request_document(
    current: dict,
    version: int,
    *,
    request_field: str,
) -> dict:
    value = copy.deepcopy(current)
    value["schemaVersion"] = version
    request = value[request_field]
    modifiers = request["modifiers"]
    if version < 7:
        del request["maximumReplacementDistance"]
    if version < 6:
        del request["gearFilters"]
    if version < 5:
        del request["itemProjectionMode"]
    if version < 4:
        del request["skillContexts"]
        del modifiers["customContributions"]
    if version < 3:
        for field in (
            "imprintContribution",
            "exclusiveEquipmentContribution",
            "exclusiveEquipmentSkillOptionId",
        ):
            del modifiers[field]
    if version < 2:
        for field in (
            "artifactLimitBreaks",
            "artifactAttackOverride",
            "artifactHealthOverride",
            "artifactDefenseOverride",
        ):
            del modifiers[field]
    return value


def _configured_draft(service: OptimizerProfileService, hero_name: str, ordinal: int) -> dict:
    hero = service.search_heroes(hero_name, 1)["results"][0]
    details = service.get_hero_details(hero["heroId"])
    draft = service.load_draft(hero["heroId"])["draft"]
    artifact = service.search_artifacts("", ordinal + 1)["results"][ordinal]
    draft["baseProfileId"] = details["profiles"][0]["profileId"]
    draft["artifact"] = {
        "artifactId": artifact["artifactId"],
        "level": 30 - ordinal,
        "attackOverride": 100 + ordinal,
        "healthOverride": 200 + ordinal,
        "defenseOverride": ordinal,
    }
    draft["imprintGrade"] = details["imprints"][-1]["grade"]
    equipment = details["exclusiveEquipment"]
    draft["exclusiveEquipment"] = {
        "equipmentId": equipment["equipmentId"],
        "statValue": equipment["rolls"][-1 - ordinal],
        "skillOptionId": equipment["skillOptions"][ordinal]["optionId"],
    }
    draft["customBonuses"]["flatAttack"] = 75 + ordinal
    draft["customBonuses"]["attackPercent"] = 12.5 + ordinal
    draft["includeEquipped"] = ordinal == 0
    draft["maximumReplacementDistance"] = 0
    draft["nearSetTolerancePercent"] = 0
    draft["itemProjectionMode"] = "projection.reforged" if ordinal == 0 else "projection.current"
    draft["gearFilters"] = {
        "minimumEnhance": 15,
        "rightSideMainStats": {
            "slot.necklace": ["item_stat.critical_hit_damage_percent"],
            "slot.ring": ["item_stat.effectiveness_percent"],
            "slot.boots": ["item_stat.speed"],
        },
    }
    draft["primaryStats"]["attack"] = {
        "minimum": 0,
        "maximum": 5000 + ordinal,
        "priority": 3 - ordinal,
    }
    return draft


class RecoveryCorpusContractTests(unittest.TestCase):
    def test_manifest_is_exact_synthetic_and_matches_production_versions(self) -> None:
        manifest = _corpus()
        self.assertEqual(
            {
                "schemaId",
                "schemaVersion",
                "fixtureId",
                "privacy",
                "settings",
                "inventoryRepository",
                "optimizerProfile",
                "runManifest",
                "resultStore",
            },
            set(manifest),
        )
        self.assertEqual("e7.test.migration-recovery-corpus", manifest["schemaId"])
        self.assertEqual(1, manifest["schemaVersion"])
        self.assertEqual("entirely-synthetic-no-user-data", manifest["privacy"])
        self.assertEqual([0, SETTINGS_SCHEMA_VERSION], manifest["settings"]["versions"])
        self.assertEqual(
            [0, INVENTORY_REPOSITORY_SCHEMA_VERSION],
            manifest["inventoryRepository"]["versions"],
        )
        self.assertEqual(
            list(range(1, OPTIMIZER_PROFILE_CURRENT_VERSION + 1)),
            manifest["optimizerProfile"]["versions"],
        )
        self.assertEqual(
            list(range(1, RUN_MANIFEST_CURRENT_VERSION + 1)),
            manifest["runManifest"]["versions"],
        )
        serialized = json.dumps(manifest, sort_keys=True)
        for forbidden in ("C:\\", "gear.txt", "player", "Meowtoko", "AppData"):
            self.assertNotIn(forbidden, serialized)


class PersistenceUpdateMatrixTests(unittest.TestCase):
    def test_first_run_is_lazy_then_explicit_state_survives_an_application_reopen(self) -> None:
        parsed = parse_fribbels_gear_file(FRIBBELS_FIXTURE)
        merged = merge_fribbels_inventory((), parsed)
        first_with_user_state = replace(
            merged.items[0],
            user_metadata={"favorite": True, "labels": ["synthetic-update-candidate"]},
        )
        merged = FribbelsMergeResult(
            items=(first_with_user_state, *merged.items[1:]),
            outcomes=merged.outcomes,
            unseen_existing_ids=merged.unseen_existing_ids,
            source_warnings=merged.source_warnings,
            source_rejections=merged.source_rejections,
        )
        history = ImportHistoryRecord.from_merge_result(
            import_id="synthetic-update-import",
            imported_at="2026-07-22T12:00:00Z",
            source_encoding=parsed.encoding,
            source_variant=parsed.variant,
            source_item_count=parsed.source_item_count,
            merge_result=merged,
            source_metadata={"fixture": "migration-recovery.synthetic.v1"},
        )

        with tempfile.TemporaryDirectory(prefix="e7-update-matrix-") as directory:
            user_data = Path(directory) / "user-data"
            settings_path = user_data / "settings.json"
            database_path = user_data / "optimizer.db"
            result_root = user_data / "optimizer_results"

            settings = SettingsService(settings_path)
            inventory = InventoryRepository(database_path)
            profiles = OptimizerProfileService(user_data)
            results = ResultRunStore(result_root)
            achates = _configured_draft(profiles, "Achates", 0)

            self.assertEqual("defaults", settings.load().source)
            self.assertFalse(user_data.exists())
            self.assertEqual("default", profiles.load_draft(achates["heroId"])["state"])
            self.assertFalse(user_data.exists())

            saved_settings = settings.update(
                settings.load().revision,
                {"targetWindow": "Synthetic Update Window", "appearance": {"theme": "dark"}},
            )
            inventory.initialize()
            inventory.apply_import(merged, parsed.heroes, history)
            saved_achates = profiles.save_draft(achates)
            alencia = _configured_draft(profiles, "Alencia", 1)
            saved_alencia = profiles.save_draft(alencia)
            completed = results.begin_run("synthetic-update-run").complete()

            before_items = inventory.load_inventory()
            before_heroes = inventory.load_heroes()
            before_history = inventory.load_import_history()
            before_summary = inventory.inventory_summary()
            before_dense = inventory.dense_snapshot()
            profile_bytes = {
                draft["heroId"]: profiles._profile_path(draft["heroId"]).read_bytes()
                for draft in (achates, alencia)
            }
            manifest_bytes = (completed.path / "manifest.json").read_bytes()

            reopened_settings = SettingsService(settings_path).load()
            reopened_inventory = InventoryRepository(database_path)
            initialization = reopened_inventory.initialize()
            reopened_profiles = OptimizerProfileService(user_data)
            reopened_results = ResultRunStore(result_root)
            reopened_run = reopened_results.open_run("synthetic-update-run", verify_hashes=True)

            self.assertFalse(initialization.created)
            self.assertEqual(INVENTORY_REPOSITORY_SCHEMA_VERSION, initialization.previous_version)
            self.assertIsNone(initialization.backup_path)
            self.assertEqual(saved_settings.revision, reopened_settings.revision)
            self.assertEqual("Synthetic Update Window", reopened_settings.document["target_window"])
            self.assertEqual(before_items, reopened_inventory.load_inventory())
            self.assertEqual(before_heroes, reopened_inventory.load_heroes())
            self.assertEqual(before_history, reopened_inventory.load_import_history())
            self.assertEqual(before_summary, reopened_inventory.inventory_summary())
            self.assertEqual(before_dense, reopened_inventory.dense_snapshot())
            self.assertEqual(6, len(before_dense.items_by_slot))
            self.assertEqual(saved_achates["draft"], reopened_profiles.load_draft(achates["heroId"])["draft"])
            self.assertEqual(saved_alencia["draft"], reopened_profiles.load_draft(alencia["heroId"])["draft"])
            self.assertEqual(profile_bytes[achates["heroId"]], profiles._profile_path(achates["heroId"]).read_bytes())
            self.assertEqual(profile_bytes[alencia["heroId"]], profiles._profile_path(alencia["heroId"]).read_bytes())
            self.assertEqual(manifest_bytes, (reopened_run.path / "manifest.json").read_bytes())
            self.assertEqual(0, reopened_run.row_count)
            self.assertEqual(
                {"settings.json", "optimizer.db", DESKTOP_PROFILE_DIRECTORY, "optimizer_results"},
                {path.name for path in user_data.iterdir()},
            )

    def test_every_historical_profile_version_loads_from_disk_without_rewrite_then_resaves_current(self) -> None:
        versions = _corpus()["optimizerProfile"]["versions"]
        for version in versions:
            with self.subTest(version=version), tempfile.TemporaryDirectory(
                prefix=f"e7-profile-v{version}-"
            ) as directory:
                user_data = Path(directory) / "user-data"
                service = OptimizerProfileService(user_data)
                draft = _configured_draft(service, "Achates", 0)
                service.save_draft(draft)
                path = service._profile_path(draft["heroId"])
                current = json.loads(path.read_text(encoding="utf-8"))
                historical = _downgrade_request_document(
                    current,
                    version,
                    request_field="configuration",
                )
                historical_bytes = json.dumps(
                    historical,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                path.write_bytes(historical_bytes)

                loaded = OptimizerProfileService(user_data).load_draft(draft["heroId"])
                self.assertEqual(historical_bytes, path.read_bytes())
                self.assertEqual("saved", loaded["state"])
                self.assertEqual(0, loaded["draft"]["maximumReplacementDistance"])
                self.assertEqual(0, loaded["draft"]["nearSetTolerancePercent"])
                self.assertEqual(15, loaded["draft"]["gearFilters"]["minimumEnhance"])
                self.assertEqual(
                    "projection.current" if version < 5 else "projection.reforged",
                    loaded["draft"]["itemProjectionMode"],
                )

                saved = OptimizerProfileService(user_data).save_draft(loaded["draft"])
                persisted = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(OPTIMIZER_PROFILE_CURRENT_VERSION, persisted["schemaVersion"])
                self.assertEqual(saved["draft"], OptimizerProfileService(user_data).load_draft(draft["heroId"])["draft"])
                self.assertEqual([], list(path.parent.glob("*.tmp")))

    def test_every_historical_run_manifest_loads_from_disk_and_reserializes_idempotently(self) -> None:
        current = load_run_manifest_json(
            RUN_MANIFEST_FIXTURE.read_text(encoding="utf-8")
        ).to_dict()
        for version in _corpus()["runManifest"]["versions"]:
            with self.subTest(version=version), tempfile.TemporaryDirectory(
                prefix=f"e7-run-manifest-v{version}-"
            ) as directory:
                path = Path(directory) / "run-manifest.json"
                historical = _downgrade_request_document(
                    current,
                    version,
                    request_field="requestSnapshot",
                )
                historical_bytes = json.dumps(
                    historical,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                path.write_bytes(historical_bytes)

                loaded = load_run_manifest_json(path.read_text(encoding="utf-8"))
                self.assertEqual(historical_bytes, path.read_bytes())
                self.assertEqual(RUN_MANIFEST_CURRENT_VERSION, loaded.to_dict()["schemaVersion"])
                expected_distance = 2 if version < 7 else current["requestSnapshot"]["maximumReplacementDistance"]
                self.assertEqual(expected_distance, loaded.request_snapshot.maximum_replacement_distance)

                current_bytes = (loaded.to_json() + "\n").encode("utf-8")
                path.write_bytes(current_bytes)
                reloaded = load_run_manifest_json(path.read_text(encoding="utf-8"))
                self.assertEqual(loaded, reloaded)
                self.assertEqual(current_bytes, (reloaded.to_json() + "\n").encode("utf-8"))

    def test_ambiguous_legacy_modifier_projection_is_read_only_and_byte_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-profile-legacy-tamper-") as directory:
            user_data = Path(directory) / "user-data"
            service = OptimizerProfileService(user_data)
            draft = _configured_draft(service, "Achates", 0)
            service.save_draft(draft)
            path = service._profile_path(draft["heroId"])
            historical = _downgrade_request_document(
                json.loads(path.read_text(encoding="utf-8")),
                2,
                request_field="configuration",
            )
            bonuses = historical["configuration"]["modifiers"]["exclusiveEquipmentBonuses"]
            stat = next(iter(bonuses))
            bonuses[stat] += 1
            altered = json.dumps(historical, sort_keys=True).encode("utf-8")
            path.write_bytes(altered)

            with self.assertRaises(OptimizerProfileServiceError) as raised:
                OptimizerProfileService(user_data).load_draft(draft["heroId"])

            self.assertEqual("profile-invalid", raised.exception.code)
            self.assertTrue(raised.exception.read_only)
            self.assertEqual(altered, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
