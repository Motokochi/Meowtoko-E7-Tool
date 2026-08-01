from __future__ import annotations

import copy
import inspect
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from src.optimizer.data import (
    FribbelsDocumentError,
    FribbelsEncoding,
    FribbelsVariant,
    FrozenJsonArray,
    FrozenJsonObject,
    ProjectionEvidenceState,
    parse_fribbels_gear_bytes,
    parse_fribbels_gear_file,
    thaw_json,
)
from src.optimizer.data import fribbels as fribbels_module
from src.optimizer.domain import (
    GEAR_RANK_CATALOG,
    GEAR_SLOT_CATALOG,
    ITEM_STAT_CATALOG,
    REFORGE_MATERIAL_CATALOG,
    SET_CATALOG,
    GearRank,
    GearSet,
    GearSlot,
    ItemStatType,
    ReforgeMaterial,
)


FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"


def _base_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "gear": "Ring",
        "rank": "Epic",
        "set": "HealthSet",
        "enhance": 15,
        "level": 85,
        "main": {"type": "HealthPercent", "value": 60},
        "substats": [{"type": "Speed", "value": 12, "rolls": 3}],
    }
    item.update(overrides)
    return item


def _payload(
    items: list[object],
    *,
    heroes: list[object] | None = None,
    root_fields: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {"items": items}
    if heroes is not None:
        result["heroes"] = heroes
    if root_fields:
        result.update(root_fields)
    return result


def _parse_payload(payload: object):
    return parse_fribbels_gear_bytes(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _stat_source_name(stat_type: ItemStatType) -> str:
    return ITEM_STAT_CATALOG[stat_type].fribbels_name


def _add_projection_evidence(item: dict[str, object]) -> None:
    main = item["main"]
    substats = item["substats"]
    assert isinstance(main, dict)
    assert isinstance(substats, list)
    augmented = {_stat_source_name(stat): 0 for stat in ItemStatType}
    reforged = {_stat_source_name(stat): 0 for stat in ItemStatType}
    for substat in substats:
        assert isinstance(substat, dict)
        stat_name = substat["type"]
        assert isinstance(stat_name, str)
        augmented[stat_name] += substat["value"]
        reforged[stat_name] += substat.get("reforgedValue", substat["value"])
    augmented["mainType"] = main["type"]
    augmented["mainValue"] = main["value"]
    reforged["mainType"] = main["type"]
    reforged["mainValue"] = main.get("reforgedValue", main["value"])
    item["augmentedStats"] = augmented
    item["reforgedStats"] = reforged


class FribbelsParserFixtureTests(unittest.TestCase):
    def test_every_valid_fixture_parses_with_expected_variant_and_encoding(self) -> None:
        expected = {
            "valid-scanner-export-utf8.txt": (FribbelsVariant.SCANNER, FribbelsEncoding.UTF8, 2),
            "valid-scanner-export-bom.txt": (FribbelsVariant.SCANNER, FribbelsEncoding.UTF8_BOM, 1),
            "valid-items-only-utf8.txt": (FribbelsVariant.ITEMS_ONLY, FribbelsEncoding.UTF8, 1),
            "valid-enriched-export-utf8.txt": (FribbelsVariant.ENRICHED, FribbelsEncoding.UTF8, 2),
        }

        for filename, (variant, encoding, count) in expected.items():
            with self.subTest(filename=filename):
                result = parse_fribbels_gear_file(FIXTURES / filename)
                self.assertIs(result.variant, variant)
                self.assertIs(result.encoding, encoding)
                self.assertEqual(result.source_item_count, count)
                self.assertEqual(result.accepted_count, count)
                self.assertEqual(result.rejected_count, 0)
                self.assertEqual(result.warning_count, 0)

    def test_scanner_fixture_normalizes_identity_ownership_and_raw_metadata(self) -> None:
        result = parse_fribbels_gear_file(FIXTURES / "valid-scanner-export-utf8.txt")
        equipped, unequipped = result.items

        self.assertEqual(equipped.ingame_id, "700000001")
        self.assertEqual(equipped.source_id, "700000001")
        self.assertEqual(equipped.equipped_hero_id, "91001")
        self.assertIs(equipped.slot, GearSlot.WEAPON)
        self.assertIs(equipped.gear_set, GearSet.SPEED)
        self.assertIs(equipped.rank, GearRank.EPIC)
        self.assertIs(equipped.main_stat.stat_type, ItemStatType.FLAT_ATTACK)
        self.assertFalse(equipped.locked)
        self.assertTrue(equipped.raw["l"])
        self.assertEqual(result.heroes[0].hero_id, "91001")

        self.assertEqual(unequipped.ingame_id, "700000002")
        self.assertIsNone(unequipped.equipped_hero_id)
        self.assertEqual(unequipped.raw["ingameEquippedId"], "undefined")
        self.assertEqual(result.root_metadata["fixtureFutureRootField"], "preserve-me")
        self.assertIsInstance(result.heroes[0].raw["fixtureFutureHeroField"], FrozenJsonObject)

    def test_items_only_fixture_does_not_fabricate_identity_or_owner(self) -> None:
        result = parse_fribbels_gear_file(FIXTURES / "valid-items-only-utf8.txt")
        item = result.items[0]

        self.assertEqual(result.heroes, ())
        self.assertIsNone(item.ingame_id)
        self.assertIsNone(item.source_id)
        self.assertIsNone(item.equipped_hero_id)
        self.assertFalse(item.locked)
        self.assertNotIn("id", item.raw)
        self.assertNotIn("locked", item.raw)
        gear_item = item.to_gear_item("identity.supplied.by.p01-t03")
        self.assertEqual(gear_item.item_id, "identity.supplied.by.p01-t03")
        self.assertIsNone(gear_item.dense_id)

    def test_enriched_fixture_validates_projection_lock_material_and_nulls(self) -> None:
        result = parse_fribbels_gear_file(FIXTURES / "valid-enriched-export-utf8.txt")
        equipped, unequipped = result.items

        self.assertTrue(equipped.locked)
        self.assertIs(equipped.material, ReforgeMaterial.HUNT)
        self.assertEqual(equipped.equipped_hero_id, "fixture-hero-guardian")
        self.assertIs(equipped.projection.augmented_evidence, ProjectionEvidenceState.VALID)
        self.assertIs(equipped.projection.reforged_evidence, ProjectionEvidenceState.VALID)
        self.assertEqual(equipped.projection.current_value(ItemStatType.FLAT_DEFENSE), 310)
        self.assertEqual(equipped.projection.current_value(ItemStatType.HEALTH_PERCENT), 18)
        self.assertEqual(equipped.projection.reforged_value(ItemStatType.HEALTH_PERCENT), 22)

        self.assertFalse(unequipped.locked)
        self.assertIsNone(unequipped.material)
        self.assertIsNone(unequipped.ingame_id)
        self.assertIsNone(unequipped.equipped_hero_id)
        self.assertIn("ingameId", unequipped.raw)
        self.assertIsNone(unequipped.raw["ingameId"])
        self.assertIsNone(unequipped.raw["equippedById"])

    def test_every_invalid_corpus_document_has_its_manifest_error_category(self) -> None:
        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        invalid_entries = [entry for entry in manifest["files"] if entry["status"] == "invalid"]

        for entry in invalid_entries:
            with self.subTest(filename=entry["name"]):
                with self.assertRaises(FribbelsDocumentError) as captured:
                    parse_fribbels_gear_file(FIXTURES / entry["name"])
                self.assertEqual(captured.exception.code, entry["errorCategory"])
                self.assertTrue(captured.exception.path.startswith("$"))
                self.assertTrue(captured.exception.message.endswith("."))


class FribbelsParserVocabularyTests(unittest.TestCase):
    def test_all_six_slots_normalize_through_the_source_catalog(self) -> None:
        main_by_slot = {
            GearSlot.WEAPON: ItemStatType.FLAT_ATTACK,
            GearSlot.HELMET: ItemStatType.FLAT_HEALTH,
            GearSlot.ARMOR: ItemStatType.FLAT_DEFENSE,
            GearSlot.NECKLACE: ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
            GearSlot.RING: ItemStatType.EFFECTIVENESS_PERCENT,
            GearSlot.BOOTS: ItemStatType.SPEED,
        }
        items = [
            _base_item(
                gear=GEAR_SLOT_CATALOG[slot].fribbels_name,
                enhance=0,
                main={"type": _stat_source_name(stat), "value": 10},
                substats=[],
            )
            for slot, stat in main_by_slot.items()
        ]

        result = _parse_payload(_payload(items, heroes=[]))

        self.assertEqual(tuple(item.slot for item in result.items), tuple(GearSlot))
        self.assertEqual(
            tuple(item.main_stat.stat_type for item in result.items),
            tuple(main_by_slot.values()),
        )
        self.assertEqual(result.rejections, ())

    def test_all_eleven_item_stats_normalize_in_the_fribbels_namespace(self) -> None:
        slot_by_stat = {
            ItemStatType.FLAT_ATTACK: GearSlot.NECKLACE,
            ItemStatType.ATTACK_PERCENT: GearSlot.NECKLACE,
            ItemStatType.FLAT_HEALTH: GearSlot.NECKLACE,
            ItemStatType.HEALTH_PERCENT: GearSlot.NECKLACE,
            ItemStatType.FLAT_DEFENSE: GearSlot.NECKLACE,
            ItemStatType.DEFENSE_PERCENT: GearSlot.NECKLACE,
            ItemStatType.SPEED: GearSlot.BOOTS,
            ItemStatType.CRITICAL_HIT_CHANCE_PERCENT: GearSlot.NECKLACE,
            ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT: GearSlot.NECKLACE,
            ItemStatType.EFFECTIVENESS_PERCENT: GearSlot.RING,
            ItemStatType.EFFECT_RESISTANCE_PERCENT: GearSlot.RING,
        }
        items = [
            _base_item(
                gear=GEAR_SLOT_CATALOG[slot_by_stat[stat]].fribbels_name,
                enhance=0,
                main={"type": _stat_source_name(stat), "value": 10},
                substats=[],
            )
            for stat in ItemStatType
        ]

        result = _parse_payload(_payload(items, heroes=[]))

        self.assertEqual(tuple(item.main_stat.stat_type for item in result.items), tuple(ItemStatType))
        self.assertEqual(result.rejections, ())

    def test_all_sets_ranks_and_materials_normalize(self) -> None:
        ranks = tuple(GearRank)
        materials = tuple(ReforgeMaterial)
        items = []
        for index, gear_set in enumerate(GearSet):
            rank = ranks[index % len(ranks)]
            material = materials[index % len(materials)]
            items.append(
                _base_item(
                    gear="Weapon",
                    rank=GEAR_RANK_CATALOG[rank].fribbels_name,
                    set=SET_CATALOG[gear_set].fribbels_name,
                    material=REFORGE_MATERIAL_CATALOG[material].fribbels_name,
                    enhance=0,
                    main={"type": "Attack", "value": 100},
                    substats=[],
                )
            )

        result = _parse_payload(_payload(items, heroes=[]))

        self.assertEqual(tuple(item.gear_set for item in result.items), tuple(GearSet))
        self.assertEqual({item.rank for item in result.items}, set(GearRank))
        self.assertEqual({item.material for item in result.items}, set(ReforgeMaterial))
        self.assertEqual(result.rejections, ())

    def test_display_aliases_are_not_accepted_as_fribbels_source_values(self) -> None:
        cases = (
            ("gear", "sword"),
            ("set", "Speed Set"),
            ("rank", "rank.epic"),
        )
        for key, value in cases:
            with self.subTest(key=key):
                result = _parse_payload(_payload([_base_item(**{key: value})], heroes=[]))
                self.assertEqual(result.accepted_count, 0)
                self.assertEqual(result.rejected_count, 1)
                self.assertEqual(result.rejections[0].code, "unknown-vocabulary")


class FribbelsParserRecoveryTests(unittest.TestCase):
    def test_bad_item_rows_are_rejected_without_losing_valid_rows(self) -> None:
        missing_slot = _base_item()
        del missing_slot["gear"]
        bad_main = _base_item(main={"type": "NotAStat", "value": 10})
        bad_set = _base_item(set="NotASet")
        bad_substat = _base_item(substats=[{"type": "Speed"}])
        result = _parse_payload(
            _payload(
                [_base_item(), "not-an-object", missing_slot, bad_main, bad_set, bad_substat],
                heroes=[],
            )
        )

        self.assertEqual(result.source_item_count, 6)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.rejected_count, 5)
        self.assertEqual(tuple(issue.item_index for issue in result.rejections), (1, 2, 3, 4, 5))
        self.assertEqual(
            tuple(issue.path for issue in result.rejections),
            (
                "$.items[1]",
                "$.items[2].gear",
                "$.items[3].main.type",
                "$.items[4].set",
                "$.items[5].substats[0].value",
            ),
        )

    def test_duplicate_and_illegal_stats_have_precise_rejections(self) -> None:
        duplicate_main = _base_item(
            substats=[{"type": "HealthPercent", "value": 10}]
        )
        duplicate_substat = _base_item(
            substats=[
                {"type": "Speed", "value": 10},
                {"type": "Speed", "value": 8},
            ]
        )
        illegal_substat = _base_item(
            gear="Weapon",
            main={"type": "Attack", "value": 100},
            substats=[{"type": "DefensePercent", "value": 10}],
        )
        illegal_main = _base_item(
            gear="Weapon",
            main={"type": "Speed", "value": 40},
            substats=[],
        )
        too_many = _base_item(
            substats=[
                {"type": "Attack", "value": 10},
                {"type": "Defense", "value": 10},
                {"type": "Health", "value": 10},
                {"type": "Speed", "value": 10},
                {"type": "EffectivenessPercent", "value": 10},
            ]
        )
        bad_rolls = _base_item(substats=[{"type": "Speed", "value": 10, "rolls": 1.5}])

        result = _parse_payload(
            _payload(
                [duplicate_main, duplicate_substat, illegal_substat, illegal_main, too_many, bad_rolls],
                heroes=[],
            )
        )

        self.assertEqual(result.accepted_count, 0)
        self.assertEqual(
            tuple(issue.code for issue in result.rejections),
            (
                "duplicate-main-stat",
                "duplicate-substat",
                "illegal-substat",
                "illegal-main-stat",
                "invalid-substats",
                "invalid-field",
            ),
        )
        self.assertTrue(all(issue.path.startswith("$.items[") for issue in result.rejections))

    def test_invalid_optional_fields_warn_but_preserve_the_item(self) -> None:
        item = _base_item(
            ingameId=[],
            id=True,
            ingameEquippedId="hero-one",
            equippedById="hero-two",
            locked="yes",
            material="Moon",
            name=7,
            equippedByName=8,
            l=True,
        )

        result = _parse_payload(
            _payload(
                [item],
                heroes=[{"id": "hero-one", "name": "Fixture One"}, {"id": "hero-two"}],
            )
        )

        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(result.warning_item_count, 1)
        self.assertEqual(
            {issue.code for issue in result.warnings},
            {
                "invalid-optional-identity",
                "owner-conflict",
                "invalid-lock-state",
                "unknown-material",
                "invalid-optional-text",
            },
        )
        parsed = result.items[0]
        self.assertIsNone(parsed.ingame_id)
        self.assertIsNone(parsed.source_id)
        self.assertEqual(parsed.equipped_hero_id, "hero-one")
        self.assertFalse(parsed.locked)
        self.assertTrue(parsed.raw["l"])

    def test_stale_owner_is_retained_with_a_warning(self) -> None:
        result = _parse_payload(
            _payload([_base_item(ingameEquippedId="hero-stale")], heroes=[])
        )

        self.assertEqual(result.items[0].equipped_hero_id, "hero-stale")
        self.assertEqual(result.warnings[0].code, "unresolved-owner")
        self.assertEqual(result.warnings[0].path, "$.items[0].ingameEquippedId")

    def test_malformed_heroes_are_skipped_without_rejecting_items(self) -> None:
        result = _parse_payload(
            _payload(
                [_base_item()],
                heroes=[
                    "bad",
                    {},
                    {"id": []},
                    {"id": "same"},
                    {"id": "same"},
                    {"id": "valid", "name": 9, "stars": 7, "awaken": False},
                ],
            )
        )

        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(tuple(hero.hero_id for hero in result.heroes), ("same", "valid"))
        self.assertEqual(
            {issue.code for issue in result.warnings},
            {
                "invalid-hero-row",
                "missing-hero-id",
                "invalid-hero-id",
                "duplicate-hero-id",
                "invalid-hero-metadata",
            },
        )


class FribbelsProjectionTests(unittest.TestCase):
    def test_consistent_evidence_is_validated_against_main_and_substats(self) -> None:
        item = _base_item(
            main={"type": "HealthPercent", "value": 60, "reforgedValue": 65},
            substats=[
                {"type": "Speed", "value": 12, "reforgedValue": 14},
                {"type": "DefensePercent", "value": 10, "reforgedValue": 12},
            ],
            locked=True,
        )
        _add_projection_evidence(item)

        result = _parse_payload(_payload([item], heroes=[]))
        projection = result.items[0].projection

        self.assertIs(projection.augmented_evidence, ProjectionEvidenceState.VALID)
        self.assertIs(projection.reforged_evidence, ProjectionEvidenceState.VALID)
        self.assertEqual(projection.current_value(ItemStatType.HEALTH_PERCENT), 60)
        self.assertEqual(projection.reforged_value(ItemStatType.HEALTH_PERCENT), 65)
        self.assertEqual(projection.current_value(ItemStatType.SPEED), 12)
        self.assertEqual(projection.reforged_value(ItemStatType.SPEED), 14)
        self.assertEqual(result.warnings, ())

    def test_inconsistent_evidence_warns_and_uses_per_stat_fallback(self) -> None:
        item = _base_item(
            main={"type": "HealthPercent", "value": 60, "reforgedValue": 65},
            substats=[{"type": "Speed", "value": 12, "reforgedValue": 14}],
        )
        _add_projection_evidence(item)
        augmented = item["augmentedStats"]
        reforged = item["reforgedStats"]
        assert isinstance(augmented, dict)
        assert isinstance(reforged, dict)
        augmented["Speed"] = 999
        reforged["mainValue"] = 999

        result = _parse_payload(_payload([item], heroes=[]))
        projection = result.items[0].projection

        self.assertIs(projection.augmented_evidence, ProjectionEvidenceState.INVALID)
        self.assertIs(projection.reforged_evidence, ProjectionEvidenceState.INVALID)
        self.assertEqual(projection.current_value(ItemStatType.SPEED), 12)
        self.assertEqual(projection.reforged_value(ItemStatType.SPEED), 14)
        self.assertEqual(projection.reforged_value(ItemStatType.HEALTH_PERCENT), 65)
        self.assertEqual(
            tuple(issue.code for issue in result.warnings),
            ("invalid-augmented-stats", "invalid-reforged-stats"),
        )

    def test_missing_projection_objects_use_conservative_derivation_without_noise(self) -> None:
        result = _parse_payload(_payload([_base_item()], heroes=[]))
        projection = result.items[0].projection

        self.assertIs(projection.augmented_evidence, ProjectionEvidenceState.MISSING)
        self.assertIs(projection.reforged_evidence, ProjectionEvidenceState.MISSING)
        self.assertEqual(projection.current_totals, projection.reforged_totals)
        self.assertEqual(result.warnings, ())

    def test_present_but_non_object_projection_is_an_invalid_fallback(self) -> None:
        result = _parse_payload(
            _payload([_base_item(augmentedStats=None, reforgedStats=[])], heroes=[])
        )

        projection = result.items[0].projection
        self.assertIs(projection.augmented_evidence, ProjectionEvidenceState.INVALID)
        self.assertIs(projection.reforged_evidence, ProjectionEvidenceState.INVALID)
        self.assertEqual(result.warning_count, 2)


class FribbelsParserDocumentAndMetadataTests(unittest.TestCase):
    def test_strict_json_rejects_invalid_utf8_duplicates_and_nonfinite_numbers(self) -> None:
        cases = (
            (b'{"items":[]}\xff', "invalid-utf8"),
            (b'{"items":[],"items":[]}', "duplicate-key"),
            (b'{"items":[],"unknown":NaN}', "invalid-number"),
            (b'{"items":[],"unknown":1e999}', "invalid-number"),
        )
        for payload, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(FribbelsDocumentError) as captured:
                    parse_fribbels_gear_bytes(payload)
                self.assertEqual(captured.exception.code, code)

    def test_file_reader_wraps_os_errors_without_leaking_raw_rows(self) -> None:
        missing = FIXTURES / "does-not-exist.txt"
        with self.assertRaises(FribbelsDocumentError) as captured:
            parse_fribbels_gear_file(missing)

        self.assertEqual(captured.exception.code, "file-read")
        self.assertEqual(captured.exception.path, "$file")
        self.assertNotIn(str(missing), str(captured.exception))

    def test_unknown_fields_at_every_supported_level_are_lossless_and_frozen(self) -> None:
        item = _base_item(
            main={"type": "HealthPercent", "value": 60, "futureMain": {"a": 1}},
            substats=[
                {
                    "type": "Speed",
                    "value": 12,
                    "futureSub": ["x", {"null": None}],
                }
            ],
            futureItem={"nested": [1, 2, 3]},
        )
        payload = _payload(
            [item],
            heroes=[{"id": "hero-one", "futureHero": {"enabled": True}}],
            root_fields={"futureRoot": ["keep", None]},
        )

        result = _parse_payload(payload)
        parsed = result.items[0]

        self.assertEqual(thaw_json(result.root_metadata), {"futureRoot": ["keep", None]})
        self.assertEqual(thaw_json(parsed.raw), item)
        self.assertEqual(thaw_json(parsed.main_stat.raw), item["main"])
        self.assertEqual(thaw_json(parsed.substats[0].raw), item["substats"][0])
        self.assertEqual(thaw_json(result.heroes[0].raw), payload["heroes"][0])
        self.assertIsInstance(parsed.raw["futureItem"], FrozenJsonObject)
        self.assertIsInstance(result.root_metadata["futureRoot"], FrozenJsonArray)

    def test_results_are_deeply_immutable_equal_and_deterministic(self) -> None:
        payload = _payload([_base_item()], heroes=[{"id": "hero-one"}])
        first = _parse_payload(payload)
        second = _parse_payload(copy.deepcopy(payload))

        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.items[0].source_index = 9  # type: ignore[misc]
        with self.assertRaises(TypeError):
            first.items[0].raw["new"] = 1  # type: ignore[index]
        with self.assertRaises(TypeError):
            first.root_metadata["new"] = 1  # type: ignore[index]

    def test_empty_inventory_is_valid_and_count_properties_are_consistent(self) -> None:
        result = _parse_payload(_payload([], heroes=[]))

        self.assertEqual(result.source_item_count, 0)
        self.assertEqual(result.accepted_count, 0)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(result.warning_count, 0)
        self.assertEqual(result.items, ())

    def test_parser_has_no_desktop_ui_database_or_logging_dependency(self) -> None:
        source = inspect.getsource(fribbels_module)
        for forbidden in ("src.desktop", "src.ui", "sqlite3", "logging", "print("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
