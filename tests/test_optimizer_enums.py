import inspect
import unittest
from dataclasses import FrozenInstanceError

from src.constants import ALL_SETS, ALL_SLOTS, ALL_STATS
from src.extractors.candidates import SET_ALIASES, rank_options
from src.optimizer import data, domain, engine, results, search
from src.optimizer.domain import (
    DISPLAY_SET_ORDER,
    FINAL_STAT_DISPLAY_NAMES,
    FINAL_STAT_ORDER,
    FRIBBELS_ITEM_STAT_ORDER,
    FRIBBELS_SET_ORDER,
    FRIBBELS_VOCABULARY_SOURCE_REVISION,
    FRIBBELS_VOCABULARY_SOURCE_URL,
    GEAR_RANK_CATALOG,
    GEAR_RANK_ORDER,
    GEAR_SLOT_CATALOG,
    GEAR_SLOT_ORDER,
    ITEM_STAT_CATALOG,
    ITEM_STAT_DISPLAY_ORDER,
    RESULT_CATEGORY_DISPLAY_NAMES,
    RESULT_CATEGORY_ORDER,
    REFORGE_MATERIAL_CATALOG,
    REFORGE_MATERIAL_ORDER,
    SET_CATALOG,
    ExecutionPreference,
    FinalStat,
    GearRank,
    GearSet,
    GearSlot,
    ItemStatType,
    ReforgeMaterial,
    ResultCategory,
    final_stat_display_name,
    gear_set_display_name,
    gear_set_fribbels_name,
    gear_rank_fribbels_name,
    gear_slot_display_name,
    gear_slot_fribbels_name,
    get_set_metadata,
    item_stat_display_name,
    item_stat_fribbels_name,
    item_stat_from_display,
    item_stat_from_fribbels,
    reforge_material_fribbels_name,
    resolve_final_stat,
    resolve_gear_set,
    resolve_gear_rank,
    resolve_gear_slot,
    resolve_result_category,
    resolve_reforge_material,
    result_category_display_name,
)


EXPECTED_FRIBBELS_SETS = (
    (GearSet.HEALTH, "HealthSet", 2),
    (GearSet.DEFENSE, "DefenseSet", 2),
    (GearSet.ATTACK, "AttackSet", 4),
    (GearSet.SPEED, "SpeedSet", 4),
    (GearSet.CRITICAL, "CriticalSet", 2),
    (GearSet.HIT, "HitSet", 2),
    (GearSet.DESTRUCTION, "DestructionSet", 4),
    (GearSet.LIFESTEAL, "LifestealSet", 4),
    (GearSet.COUNTER, "CounterSet", 4),
    (GearSet.RESIST, "ResistSet", 2),
    (GearSet.UNITY, "UnitySet", 2),
    (GearSet.RAGE, "RageSet", 4),
    (GearSet.IMMUNITY, "ImmunitySet", 2),
    (GearSet.PENETRATION, "PenetrationSet", 2),
    (GearSet.REVENGE, "RevengeSet", 4),
    (GearSet.INJURY, "InjurySet", 4),
    (GearSet.PROTECTION, "ProtectionSet", 4),
    (GearSet.TORRENT, "TorrentSet", 2),
    (GearSet.REVERSAL, "ReversalSet", 4),
    (GearSet.RIPOSTE, "RiposteSet", 4),
    (GearSet.WARFARE, "WarfareSet", 4),
    (GearSet.PURSUIT, "PursuitSet", 2),
    (GearSet.WEAKENING, "WeakeningSet", 4),
    (GearSet.FERVOR, "FervorSet", 2),
)


class OptimizerEnumContractTests(unittest.TestCase):
    def test_optimizer_namespaces_exist_without_desktop_dependencies(self):
        self.assertEqual(
            {module.__name__ for module in (data, domain, engine, search, results)},
            {
                "src.optimizer.data",
                "src.optimizer.domain",
                "src.optimizer.engine",
                "src.optimizer.search",
                "src.optimizer.results",
            },
        )
        domain_source = inspect.getsource(domain)
        catalog_source = inspect.getsource(domain.catalog)
        self.assertNotIn("src.desktop", domain_source + catalog_source)
        self.assertNotIn("src.ui", domain_source + catalog_source)

    def test_enum_values_are_stable_unique_and_namespaced(self):
        enum_types = (
            GearSlot,
            GearRank,
            ReforgeMaterial,
            ItemStatType,
            FinalStat,
            GearSet,
            ResultCategory,
            ExecutionPreference,
        )
        all_values = []
        for enum_type in enum_types:
            values = [member.value for member in enum_type]
            self.assertEqual(len(values), len(set(values)))
            all_values.extend(values)
        self.assertEqual(len(all_values), len(set(all_values)))
        self.assertTrue(all(value.startswith("item_stat.") for value in ItemStatType))
        self.assertTrue(all(value.startswith("final_stat.") for value in FinalStat))

    def test_rank_and_reforge_material_source_names_round_trip(self):
        self.assertEqual(GEAR_RANK_ORDER, tuple(GearRank))
        self.assertEqual(REFORGE_MATERIAL_ORDER, tuple(ReforgeMaterial))
        self.assertEqual(len(GEAR_RANK_CATALOG), 5)
        self.assertEqual(len(REFORGE_MATERIAL_CATALOG), 3)
        for rank, metadata in GEAR_RANK_CATALOG.items():
            self.assertIs(resolve_gear_rank(metadata.fribbels_name), rank)
            self.assertEqual(gear_rank_fribbels_name(rank), metadata.fribbels_name)
        for material, metadata in REFORGE_MATERIAL_CATALOG.items():
            self.assertIs(resolve_reforge_material(metadata.fribbels_name), material)
            self.assertEqual(reforge_material_fribbels_name(material), metadata.fribbels_name)
        self.assertIs(resolve_reforge_material("Unknown"), ReforgeMaterial.UNKNOWN)

    def test_slot_display_and_fribbels_names_round_trip(self):
        self.assertEqual(len(GEAR_SLOT_ORDER), 6)
        for slot in GEAR_SLOT_ORDER:
            metadata = GEAR_SLOT_CATALOG[slot]
            self.assertIs(resolve_gear_slot(metadata.display_name), slot)
            self.assertIs(resolve_gear_slot(metadata.fribbels_name), slot)
            self.assertIs(resolve_gear_slot(slot.value), slot)
            self.assertEqual(gear_slot_display_name(slot), metadata.display_name)
            self.assertEqual(gear_slot_fribbels_name(slot), metadata.fribbels_name)
        self.assertIs(resolve_gear_slot("armour"), GearSlot.ARMOR)

    def test_item_stat_source_specific_names_round_trip(self):
        self.assertEqual(len(ITEM_STAT_CATALOG), 11)
        for stat, metadata in ITEM_STAT_CATALOG.items():
            self.assertIs(item_stat_from_display(metadata.display_name), stat)
            self.assertIs(item_stat_from_fribbels(metadata.fribbels_name), stat)
            self.assertEqual(item_stat_display_name(stat), metadata.display_name)
            self.assertEqual(item_stat_fribbels_name(stat), metadata.fribbels_name)

        # Fribbels' unqualified Attack/Health/Defense names are flat. Existing
        # Meowtoko E7 Tool display names with the same text are percent stats.
        self.assertIs(item_stat_from_fribbels("Attack"), ItemStatType.FLAT_ATTACK)
        self.assertIs(item_stat_from_display("Attack"), ItemStatType.ATTACK_PERCENT)
        self.assertIs(item_stat_from_fribbels("Health"), ItemStatType.FLAT_HEALTH)
        self.assertIs(item_stat_from_display("Health"), ItemStatType.HEALTH_PERCENT)
        self.assertIs(item_stat_from_fribbels("Defense"), ItemStatType.FLAT_DEFENSE)
        self.assertIs(item_stat_from_display("Defense"), ItemStatType.DEFENSE_PERCENT)

    def test_fribbels_item_stat_order_excludes_non_item_dac(self):
        self.assertEqual(
            FRIBBELS_ITEM_STAT_ORDER,
            (
                ItemStatType.FLAT_ATTACK,
                ItemStatType.FLAT_HEALTH,
                ItemStatType.FLAT_DEFENSE,
                ItemStatType.ATTACK_PERCENT,
                ItemStatType.HEALTH_PERCENT,
                ItemStatType.DEFENSE_PERCENT,
                ItemStatType.CRITICAL_HIT_CHANCE_PERCENT,
                ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
                ItemStatType.EFFECTIVENESS_PERCENT,
                ItemStatType.EFFECT_RESISTANCE_PERCENT,
                ItemStatType.SPEED,
            ),
        )
        self.assertEqual(len(FRIBBELS_ITEM_STAT_ORDER), len(ITEM_STAT_CATALOG))

    def test_upstream_vocabulary_revision_is_attributed(self):
        self.assertEqual(FRIBBELS_VOCABULARY_SOURCE_REVISION, "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb")
        self.assertIn(FRIBBELS_VOCABULARY_SOURCE_REVISION, FRIBBELS_VOCABULARY_SOURCE_URL)

    def test_flat_item_stats_are_distinct_from_final_totals(self):
        item_values = {member.value for member in ItemStatType}
        final_values = {member.value for member in FinalStat}
        self.assertTrue(item_values.isdisjoint(final_values))
        self.assertNotEqual(ItemStatType.FLAT_ATTACK, FinalStat.ATTACK)
        self.assertNotEqual(ItemStatType.FLAT_HEALTH, FinalStat.HEALTH)
        self.assertNotEqual(ItemStatType.FLAT_DEFENSE, FinalStat.DEFENSE)

    def test_final_stats_round_trip_through_display_and_stable_id(self):
        self.assertEqual(FINAL_STAT_ORDER, tuple(FinalStat))
        for stat, display_name in FINAL_STAT_DISPLAY_NAMES.items():
            self.assertIs(resolve_final_stat(display_name), stat)
            self.assertIs(resolve_final_stat(stat.value), stat)
            self.assertEqual(final_stat_display_name(stat), display_name)

    def test_fribbels_set_order_names_indices_and_piece_counts(self):
        self.assertEqual(len(SET_CATALOG), 24)
        self.assertEqual(FRIBBELS_SET_ORDER, tuple(row[0] for row in EXPECTED_FRIBBELS_SETS))
        for expected_index, (gear_set, fribbels_name, pieces) in enumerate(EXPECTED_FRIBBELS_SETS):
            metadata = SET_CATALOG[gear_set]
            self.assertEqual(metadata.fribbels_index, expected_index)
            self.assertEqual(metadata.fribbels_name, fribbels_name)
            self.assertEqual(metadata.pieces_required, pieces)
            self.assertIn(pieces, {2, 4})

    def test_every_set_name_and_alias_round_trips(self):
        for gear_set, metadata in SET_CATALOG.items():
            self.assertIs(resolve_gear_set(metadata.display_name), gear_set)
            self.assertIs(resolve_gear_set(metadata.fribbels_name), gear_set)
            self.assertIs(resolve_gear_set(gear_set.value), gear_set)
            self.assertEqual(gear_set_display_name(gear_set), metadata.display_name)
            self.assertEqual(gear_set_fribbels_name(gear_set), metadata.fribbels_name)
            self.assertIs(get_set_metadata(metadata.display_name), metadata)
            for alias in metadata.aliases:
                self.assertIs(resolve_gear_set(alias), gear_set)

        self.assertIs(resolve_gear_set("Crit"), GearSet.CRITICAL)
        self.assertIs(resolve_gear_set("RiposteSet"), GearSet.RIPOSTE)

    def test_only_repeatable_two_piece_bonuses_are_stackable(self):
        expected_stackable = {
            GearSet.HEALTH,
            GearSet.DEFENSE,
            GearSet.CRITICAL,
            GearSet.HIT,
            GearSet.RESIST,
            GearSet.UNITY,
            GearSet.TORRENT,
        }
        actual_stackable = {gear_set for gear_set, metadata in SET_CATALOG.items() if metadata.stackable}
        self.assertEqual(actual_stackable, expected_stackable)
        self.assertTrue(all(SET_CATALOG[gear_set].pieces_required == 2 for gear_set in actual_stackable))
        self.assertFalse(SET_CATALOG[GearSet.IMMUNITY].stackable)
        self.assertFalse(SET_CATALOG[GearSet.PENETRATION].stackable)

    def test_catalog_and_metadata_are_immutable(self):
        with self.assertRaises(TypeError):
            SET_CATALOG[GearSet.HEALTH] = SET_CATALOG[GearSet.HEALTH]
        with self.assertRaises(FrozenInstanceError):
            SET_CATALOG[GearSet.HEALTH].pieces_required = 4

    def test_result_categories_round_trip(self):
        self.assertEqual(RESULT_CATEGORY_ORDER, tuple(ResultCategory))
        for category, display_name in RESULT_CATEGORY_DISPLAY_NAMES.items():
            self.assertIs(resolve_result_category(display_name), category)
            self.assertIs(resolve_result_category(category.value), category)
            self.assertEqual(result_category_display_name(category), display_name)

    def test_unknown_vocabulary_is_rejected(self):
        for resolver in (
            resolve_gear_slot,
            resolve_gear_rank,
            resolve_reforge_material,
            item_stat_from_display,
            item_stat_from_fribbels,
            resolve_final_stat,
            resolve_gear_set,
            resolve_result_category,
        ):
            with self.subTest(resolver=resolver.__name__):
                with self.assertRaises(ValueError):
                    resolver("not-a-real-value")

    def test_legacy_constants_derive_from_canonical_catalogs(self):
        self.assertIsInstance(ALL_SLOTS, list)
        self.assertIsInstance(ALL_SETS, list)
        self.assertIsInstance(ALL_STATS, list)
        self.assertEqual(ALL_SLOTS, [gear_slot_display_name(slot) for slot in GEAR_SLOT_ORDER])
        self.assertEqual(ALL_STATS, [item_stat_display_name(stat) for stat in ITEM_STAT_DISPLAY_ORDER])
        self.assertEqual(ALL_SETS, [gear_set_display_name(gear_set) for gear_set in DISPLAY_SET_ORDER])
        self.assertEqual(ALL_SLOTS, ["Weapon", "Helmet", "Armor", "Necklace", "Ring", "Boots"])
        self.assertEqual(
            ALL_STATS,
            [
                "Flat Attack",
                "Attack",
                "Defense",
                "Flat Defense",
                "Flat Health",
                "Health",
                "Speed",
                "Critical Hit Chance",
                "Critical Hit Damage",
                "Effectiveness",
                "Effect Resistance",
            ],
        )
        self.assertEqual(len(ALL_SETS), 24)
        self.assertIn("Riposte Set", ALL_SETS)

    def test_ocr_aliases_are_derived_from_set_catalog(self):
        self.assertEqual(set(SET_ALIASES), set(ALL_SETS))
        for gear_set, metadata in SET_CATALOG.items():
            self.assertIn(metadata.fribbels_name, SET_ALIASES[metadata.display_name])
            ranked = rank_options(metadata.fribbels_name, ALL_SETS, aliases=SET_ALIASES, limit=1)
            self.assertEqual(ranked[0]["value"], metadata.display_name)


if __name__ == "__main__":
    unittest.main()
