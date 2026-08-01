from __future__ import annotations

import copy
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from src.optimizer.data import (
    CHARACTER_CATALOG_CURRENT_VERSION,
    CHARACTER_CATALOG_MIGRATIONS,
    CHARACTER_CATALOG_SCHEMA_ID,
    INVENTORY_CURRENT_VERSION,
    INVENTORY_MIGRATIONS,
    INVENTORY_SCHEMA_ID,
    OPTIMIZER_PROFILE_CURRENT_VERSION,
    OPTIMIZER_PROFILE_MIGRATIONS,
    OPTIMIZER_PROFILE_SCHEMA_ID,
    RUN_MANIFEST_CURRENT_VERSION,
    RUN_MANIFEST_MIGRATIONS,
    RUN_MANIFEST_SCHEMA_ID,
    FrozenJsonArray,
    FrozenJsonObject,
    SchemaValidationError,
    deterministic_json,
    load_character_catalog,
    load_character_catalog_json,
    load_inventory,
    load_inventory_json,
    load_optimizer_profile,
    load_optimizer_profile_json,
    load_run_manifest,
    load_run_manifest_json,
)


FIXTURES = Path(__file__).parent / "fixtures" / "optimizer"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_dict(name: str) -> dict[str, object]:
    return json.loads(fixture_text(name))


class OptimizerSchemaFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog_text = fixture_text("character-catalog-v1.json")
        cls.inventory_text = fixture_text("inventory-v1.json")
        cls.profile_text = fixture_text("optimizer-profile-v1.json")
        cls.run_text = fixture_text("run-manifest-v1.json")
        cls.catalog = load_character_catalog_json(cls.catalog_text)

    def test_schema_families_have_independent_ids_versions_and_registries(self) -> None:
        identifiers = {
            CHARACTER_CATALOG_SCHEMA_ID,
            INVENTORY_SCHEMA_ID,
            OPTIMIZER_PROFILE_SCHEMA_ID,
            RUN_MANIFEST_SCHEMA_ID,
        }
        self.assertEqual(4, len(identifiers))
        self.assertEqual(
            (1, 1, 7, 7),
            (
                CHARACTER_CATALOG_CURRENT_VERSION,
                INVENTORY_CURRENT_VERSION,
                OPTIMIZER_PROFILE_CURRENT_VERSION,
                RUN_MANIFEST_CURRENT_VERSION,
            ),
        )
        registries = (
            (CHARACTER_CATALOG_MIGRATIONS, set()),
            (INVENTORY_MIGRATIONS, set()),
            (OPTIMIZER_PROFILE_MIGRATIONS, {1, 2, 3, 4, 5, 6}),
            (RUN_MANIFEST_MIGRATIONS, {1, 2, 3, 4, 5, 6}),
        )
        for registry, expected_keys in registries:
            self.assertEqual(expected_keys, set(registry))
            with self.assertRaises(TypeError):
                registry[1] = lambda value: value  # type: ignore[index]

    def test_all_current_fixtures_round_trip_without_information_loss(self) -> None:
        inventory = load_inventory_json(self.inventory_text, character_catalog=self.catalog)
        profile = load_optimizer_profile_json(self.profile_text, character_catalog=self.catalog)
        run = load_run_manifest_json(self.run_text, character_catalog=self.catalog)
        cases = (
            (self.catalog, load_character_catalog_json),
            (inventory, load_inventory_json),
            (profile, load_optimizer_profile_json),
            (run, load_run_manifest_json),
        )
        for document, loader in cases:
            with self.subTest(schema=document.to_dict()["schemaId"]):
                serialized = document.to_json()
                self.assertEqual(serialized, deterministic_json(document))
                self.assertEqual(serialized, document.to_json())
                self.assertEqual(document.to_dict(), json.loads(serialized))
                self.assertEqual(document, loader(serialized))

    def test_unknown_source_fields_are_deeply_immutable_and_preserved(self) -> None:
        unknown = self.catalog.source.unknown_fields
        self.assertIsInstance(unknown, FrozenJsonObject)
        flags = unknown["upstreamFlags"]
        self.assertIsInstance(flags, FrozenJsonArray)
        nested = flags[1]
        self.assertIsInstance(nested, FrozenJsonObject)
        self.assertTrue(nested["retained"])
        with self.assertRaises(TypeError):
            unknown["new"] = 1  # type: ignore[index]
        with self.assertRaises(TypeError):
            flags[0] = "changed"  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            self.catalog.catalog_id = "changed"  # type: ignore[misc]
        reloaded = load_character_catalog_json(self.catalog.to_json())
        self.assertEqual(unknown, reloaded.source.unknown_fields)

    def test_profiles_require_a_new_request_identity(self) -> None:
        profile = load_optimizer_profile_json(self.profile_text, character_catalog=self.catalog)
        self.assertNotIn("requestId", profile.to_dict()["configuration"])
        request = profile.create_request("request.new.001")
        self.assertEqual("request.new.001", request.request_id)
        self.assertEqual("hero.synthetic.knight", request.hero_id)


class OptimizerSchemaVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = (
            (
                fixture_dict("character-catalog-v1.json"),
                load_character_catalog,
                "Character catalog",
                CHARACTER_CATALOG_CURRENT_VERSION,
            ),
            (
                fixture_dict("inventory-v1.json"),
                load_inventory,
                "Inventory",
                INVENTORY_CURRENT_VERSION,
            ),
            (
                fixture_dict("optimizer-profile-v1.json"),
                load_optimizer_profile,
                "Optimizer profile",
                OPTIMIZER_PROFILE_CURRENT_VERSION,
            ),
            (
                fixture_dict("run-manifest-v1.json"),
                load_run_manifest,
                "Run manifest",
                RUN_MANIFEST_CURRENT_VERSION,
            ),
        )

    def test_boolean_blank_missing_and_future_versions_are_actionable(self) -> None:
        for original, loader, family, current_version in self.cases:
            with self.subTest(family=family, case="boolean"):
                payload = copy.deepcopy(original)
                payload["schemaVersion"] = True
                with self.assertRaisesRegex(SchemaValidationError, rf"{family} schemaVersion must be an integer"):
                    loader(payload)
            with self.subTest(family=family, case="future"):
                payload = copy.deepcopy(original)
                payload["schemaVersion"] = current_version + 1
                with self.assertRaisesRegex(
                    SchemaValidationError,
                    f"newer than supported version {current_version}",
                ):
                    loader(payload)
            with self.subTest(family=family, case="blank-id"):
                payload = copy.deepcopy(original)
                payload["schemaId"] = "  "
                with self.assertRaisesRegex(SchemaValidationError, rf"{family} schemaId"):
                    loader(payload)
            with self.subTest(family=family, case="missing-id"):
                payload = copy.deepcopy(original)
                del payload["schemaId"]
                with self.assertRaisesRegex(SchemaValidationError, rf"{family} schemaId"):
                    loader(payload)
            with self.subTest(family=family, case="non-positive"):
                payload = copy.deepcopy(original)
                payload["schemaVersion"] = 0
                with self.assertRaisesRegex(SchemaValidationError, "must be at least 1"):
                    loader(payload)

    def test_wrong_schema_and_unknown_envelope_field_are_rejected(self) -> None:
        payload = fixture_dict("inventory-v1.json")
        payload["schemaId"] = CHARACTER_CATALOG_SCHEMA_ID
        with self.assertRaisesRegex(SchemaValidationError, "schemaId must be"):
            load_inventory(payload)
        payload = fixture_dict("inventory-v1.json")
        payload["upstreamMystery"] = 17
        with self.assertRaisesRegex(SchemaValidationError, "source.unknownFields"):
            load_inventory(payload)

    def test_every_old_request_schema_migrates_with_safe_request_defaults(self) -> None:
        profile_current = load_optimizer_profile(fixture_dict("optimizer-profile-v1.json")).to_dict()
        run_current = load_run_manifest(fixture_dict("run-manifest-v1.json")).to_dict()
        cases = (
            (profile_current, "configuration", load_optimizer_profile),
            (run_current, "requestSnapshot", load_run_manifest),
        )
        for current, request_field, loader in cases:
            for old_version in (1, 2, 3, 4, 5, 6):
                with self.subTest(request_field=request_field, old_version=old_version):
                    old = copy.deepcopy(current)
                    old["schemaVersion"] = old_version
                    request_payload = old[request_field]
                    del request_payload["maximumReplacementDistance"]
                    if old_version < 6:
                        del request_payload["gearFilters"]
                    if old_version < 5:
                        del request_payload["itemProjectionMode"]
                    modifiers = request_payload["modifiers"]
                    if old_version < 4:
                        del request_payload["skillContexts"]
                        del modifiers["customContributions"]
                    if old_version < 3:
                        for field in (
                            "imprintContribution",
                            "exclusiveEquipmentContribution",
                            "exclusiveEquipmentSkillOptionId",
                        ):
                            del modifiers[field]
                    if old_version < 2:
                        for field in (
                            "artifactLimitBreaks",
                            "artifactAttackOverride",
                            "artifactHealthOverride",
                            "artifactDefenseOverride",
                        ):
                            del modifiers[field]
                    migrated = loader(old)
                    request = (
                        migrated.create_request("request.migrated-old")
                        if request_field == "configuration"
                        else migrated.request_snapshot
                    )
                    self.assertIsNone(request.item_projection_mode)
                    self.assertEqual(request.gear_filters.minimum_enhance, 0)
                    self.assertEqual(request.gear_filters.right_side_main_stats, ())
                    self.assertEqual(request.gear_filters.excluded_item_ids, ())
                    self.assertEqual(request.maximum_replacement_distance, 2)

            with self.subTest(request_field=request_field, case="smuggled-v5-field"):
                old = copy.deepcopy(current)
                old["schemaVersion"] = 4
                with self.assertRaisesRegex(
                    SchemaValidationError,
                    "must not already contain version-5 field itemProjectionMode",
                ):
                    loader(old)

            with self.subTest(request_field=request_field, case="smuggled-v6-field"):
                old = copy.deepcopy(current)
                old["schemaVersion"] = 5
                with self.assertRaisesRegex(
                    SchemaValidationError,
                    "must not already contain version-6 field gearFilters",
                ):
                    loader(old)

            with self.subTest(request_field=request_field, case="smuggled-v7-field"):
                old = copy.deepcopy(current)
                old["schemaVersion"] = 6
                with self.assertRaisesRegex(
                    SchemaValidationError,
                    "must not already contain version-7 field maximumReplacementDistance",
                ):
                    loader(old)

    def test_current_request_schemas_require_maximum_replacement_distance(self) -> None:
        profile = load_optimizer_profile(
            fixture_dict("optimizer-profile-v1.json")
        ).to_dict()
        run = load_run_manifest(fixture_dict("run-manifest-v1.json")).to_dict()
        cases = (
            (profile, "configuration", load_optimizer_profile),
            (run, "requestSnapshot", load_run_manifest),
        )
        for payload, request_field, loader in cases:
            with self.subTest(request_field=request_field):
                missing = copy.deepcopy(payload)
                del missing[request_field]["maximumReplacementDistance"]
                with self.assertRaisesRegex(
                    SchemaValidationError,
                    "requires version-7 field maximumReplacementDistance",
                ):
                    loader(missing)

    def test_current_profiles_and_runs_preserve_explicit_gear_filters(self) -> None:
        profile_payload = load_optimizer_profile(
            fixture_dict("optimizer-profile-v1.json")
        ).to_dict()
        run_payload = load_run_manifest(fixture_dict("run-manifest-v1.json")).to_dict()
        configured = {
            "rightSideMainStats": {
                "slot.ring": [
                    "item_stat.health_percent",
                    "item_stat.effectiveness_percent",
                ],
                "slot.boots": ["item_stat.speed"],
            },
            "minimumEnhance": 12,
            "excludedItemIds": ["item.z", "item.a"],
        }
        profile_payload["configuration"]["gearFilters"] = copy.deepcopy(configured)
        run_payload["requestSnapshot"]["gearFilters"] = copy.deepcopy(configured)

        profile = load_optimizer_profile(profile_payload)
        run = load_run_manifest(run_payload)
        requests = (
            profile.create_request("request.explicit-filters"),
            run.request_snapshot,
        )
        for request in requests:
            self.assertEqual(12, request.gear_filters.minimum_enhance)
            self.assertEqual(("item.a", "item.z"), request.gear_filters.excluded_item_ids)
            self.assertEqual(
                ("slot.ring", "slot.boots"),
                tuple(slot.value for slot, _ in request.gear_filters.right_side_main_stats),
            )

    def test_json_parser_rejects_duplicate_keys_non_object_roots_and_nonfinite_numbers(self) -> None:
        with self.assertRaisesRegex(SchemaValidationError, "duplicate object key 'schemaId'"):
            load_character_catalog_json(
                '{"schemaId":"one","schemaId":"two","schemaVersion":1}'
            )
        with self.assertRaisesRegex(SchemaValidationError, "root must be an object"):
            load_inventory_json("[]")
        with self.assertRaisesRegex(SchemaValidationError, "invalid number NaN"):
            load_inventory_json('{"value":NaN}')


class OptimizerSchemaValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_character_catalog_json(fixture_text("character-catalog-v1.json"))

    def test_character_catalog_rejects_duplicate_stable_and_dense_ids(self) -> None:
        duplicate_hero = fixture_dict("character-catalog-v1.json")
        duplicate_hero["heroes"].append(copy.deepcopy(duplicate_hero["heroes"][0]))  # type: ignore[union-attr,index]
        with self.assertRaisesRegex(SchemaValidationError, "hero IDs must contain unique values"):
            load_character_catalog(duplicate_hero)

        duplicate_profile = fixture_dict("character-catalog-v1.json")
        second = copy.deepcopy(duplicate_profile["heroes"][0])  # type: ignore[index]
        second["heroId"] = "hero.synthetic.second"
        second["denseId"] = 1
        duplicate_profile["heroes"].append(second)  # type: ignore[union-attr]
        with self.assertRaisesRegex(SchemaValidationError, "profile IDs must contain unique values"):
            load_character_catalog(duplicate_profile)

        duplicate_artifact_dense = fixture_dict("character-catalog-v1.json")
        artifact = copy.deepcopy(duplicate_artifact_dense["artifacts"][0])  # type: ignore[index]
        artifact["artifactId"] = "artifact.synthetic.second"
        duplicate_artifact_dense["artifacts"].append(artifact)  # type: ignore[union-attr]
        with self.assertRaisesRegex(SchemaValidationError, "artifact dense IDs must contain unique values"):
            load_character_catalog(duplicate_artifact_dense)

    def test_inventory_rejects_duplicate_ids_and_bad_equipped_hero_references(self) -> None:
        duplicate_item = fixture_dict("inventory-v1.json")
        duplicate_item["items"][1]["itemId"] = "gear.synthetic.weapon.001"  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "item IDs must contain unique values"):
            load_inventory(duplicate_item)

        duplicate_dense = fixture_dict("inventory-v1.json")
        duplicate_dense["items"][1]["denseId"] = 0  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "item dense IDs must contain unique values"):
            load_inventory(duplicate_dense)

        unknown_hero = fixture_dict("inventory-v1.json")
        unknown_hero["items"][0]["equippedHeroId"] = "hero.missing"  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "equippedHeroId.*hero.missing"):
            load_inventory(unknown_hero, character_catalog=self.catalog)

    def test_inventory_remains_loadable_without_catalog_context(self) -> None:
        payload = fixture_dict("inventory-v1.json")
        payload["items"][0]["equippedHeroId"] = "hero.not-yet-loaded"  # type: ignore[index]
        document = load_inventory(payload)
        equipped_ids = {item.equipped_hero_id for item in document.items}
        self.assertIn("hero.not-yet-loaded", equipped_ids)

    def test_catalog_snapshot_references_must_match_supplied_context(self) -> None:
        inventory = fixture_dict("inventory-v1.json")
        inventory["characterCatalogId"] = "catalog.other"
        with self.assertRaisesRegex(SchemaValidationError, "characterCatalogId does not match"):
            load_inventory(inventory, character_catalog=self.catalog)

        profile = fixture_dict("optimizer-profile-v1.json")
        profile["characterCatalogId"] = "catalog.other"
        with self.assertRaisesRegex(SchemaValidationError, "characterCatalogId does not match"):
            load_optimizer_profile(profile, character_catalog=self.catalog)

    def test_profile_catalog_cross_references_are_validated(self) -> None:
        cases = (
            ("heroId", "hero.missing", "heroId"),
            ("baseProfileId", "profile.missing", "baseProfileId"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                payload = fixture_dict("optimizer-profile-v1.json")
                payload["configuration"][field] = value  # type: ignore[index]
                with self.assertRaisesRegex(SchemaValidationError, message):
                    load_optimizer_profile(payload, character_catalog=self.catalog)
        payload = fixture_dict("optimizer-profile-v1.json")
        payload["configuration"]["modifiers"]["artifactId"] = "artifact.missing"  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "artifactId"):
            load_optimizer_profile(payload, character_catalog=self.catalog)

    def test_profile_rejects_persisted_request_id_and_nested_domain_errors(self) -> None:
        old_identity = fixture_dict("optimizer-profile-v1.json")
        old_identity["configuration"]["requestId"] = "request.old"  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "must not contain requestId"):
            load_optimizer_profile(old_identity)

        invalid_priority = fixture_dict("optimizer-profile-v1.json")
        invalid_priority["configuration"]["statPriorities"]["final_stat.attack"] = 4  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "configuration.*at most 3"):
            load_optimizer_profile(invalid_priority)

    def test_nested_character_and_inventory_domain_errors_include_context(self) -> None:
        character = fixture_dict("character-catalog-v1.json")
        del character["heroes"][0]["baseProfiles"][0]["finalStats"]["final_stat.attack"]  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, r"Character catalog heroes\[0\].*missing"):
            load_character_catalog(character)

        inventory = fixture_dict("inventory-v1.json")
        inventory["items"][0]["mainStat"]["type"] = "item_stat.unknown"  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, r"Inventory items\[0\].*ItemStatType"):
            load_inventory(inventory)

    def test_all_timestamp_fields_require_explicit_utc_z_values(self) -> None:
        cases = (
            (fixture_dict("character-catalog-v1.json"), "generatedAt", load_character_catalog),
            (fixture_dict("inventory-v1.json"), "importedAt", load_inventory),
            (fixture_dict("optimizer-profile-v1.json"), "savedAt", load_optimizer_profile),
            (fixture_dict("run-manifest-v1.json"), "completedAt", load_run_manifest),
        )
        for payload, field, loader in cases:
            with self.subTest(field=field):
                payload[field] = "2026-07-20T10:00:00+00:00"
                with self.assertRaisesRegex(SchemaValidationError, "ending in Z"):
                    loader(payload)

    def test_run_manifest_validates_request_summary_store_and_time_consistency(self) -> None:
        mismatch = fixture_dict("run-manifest-v1.json")
        mismatch["summary"]["requestId"] = "request.other"  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "requestSnapshot.requestId must match"):
            load_run_manifest(mismatch)

        missing_store = fixture_dict("run-manifest-v1.json")
        missing_store["resultStore"] = None
        with self.assertRaisesRegex(SchemaValidationError, "Completed run manifests require"):
            load_run_manifest(missing_store)

        bad_checksum = fixture_dict("run-manifest-v1.json")
        bad_checksum["resultStore"]["sha256"] = "bad"  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "64-character"):
            load_run_manifest(bad_checksum)

        reversed_time = fixture_dict("run-manifest-v1.json")
        reversed_time["completedAt"] = "2026-07-20T10:14:59Z"
        with self.assertRaisesRegex(SchemaValidationError, "must not precede"):
            load_run_manifest(reversed_time)

    def test_aborted_run_cannot_claim_partial_results_or_a_result_store(self) -> None:
        overflowed = fixture_dict("run-manifest-v1.json")
        overflowed["completionState"] = "overflowed"
        overflowed["summary"]["overflowed"] = True  # type: ignore[index]
        overflowed["summary"]["exactCount"] = 0  # type: ignore[index]
        overflowed["summary"]["oneAwayCount"] = 0  # type: ignore[index]
        overflowed["summary"]["twoAwayCount"] = 0  # type: ignore[index]
        overflowed["summary"]["resultCount"] = 0  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "must not reference partial"):
            load_run_manifest(overflowed)
        overflowed["resultStore"] = None
        manifest = load_run_manifest(overflowed)
        self.assertTrue(manifest.summary.overflowed)
        self.assertIsNone(manifest.result_store)

    def test_summary_nested_validation_failure_has_manifest_context(self) -> None:
        payload = fixture_dict("run-manifest-v1.json")
        payload["summary"]["resultCount"] = 999  # type: ignore[index]
        with self.assertRaisesRegex(SchemaValidationError, "Run manifest summary.*does not match"):
            load_run_manifest(payload)


if __name__ == "__main__":
    unittest.main()
