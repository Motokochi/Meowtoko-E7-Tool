from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from src.optimizer.data import (
    ArtifactSelection,
    load_bundled_character_profile_selector,
    merge_fribbels_inventory,
    parse_fribbels_gear_file,
)
from src.optimizer.domain import (
    SET_CATALOG,
    FinalStat,
    GearItem,
    GearSet,
    GearSlot,
    HeroModifierContribution,
    HeroModifiers,
    HeroModifierStatType,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SetPattern,
    custom_bonus_projection,
)
from src.optimizer.engine import (
    ItemProjectionEvidence,
    ProjectedGearItem,
    aggregate_with_set_bonuses,
)


FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"


def _request(
    selection,
    *,
    mode=ItemProjectionMode.CURRENT,
    modifiers=HeroModifiers(),
    pattern=SetPattern((GearSet.SPEED, GearSet.CRITICAL)),
):
    return OptimizationRequest(
        request_id="request.set-evaluation",
        hero_id=selection.hero_id,
        base_profile_id=selection.profile_id,
        modifiers=modifiers,
        set_pattern=pattern,
        item_projection_mode=mode,
    )


def _items_for_sets(sets: tuple[GearSet, ...]) -> tuple[ProjectedGearItem, ...]:
    if len(sets) != 6:
        raise AssertionError("Fixture must contain six set IDs.")
    return tuple(
        ProjectedGearItem.from_gear_item(
            GearItem(
                item_id=f"set-item.{index}",
                dense_id=index,
                slot=slot,
                gear_set=gear_set,
                main_stat=ItemStatType.FLAT_ATTACK,
                main_stat_value=0,
            )
        )
        for index, (slot, gear_set) in enumerate(zip(GearSlot, sets, strict=True))
    )


def _four_plus_two(four_piece: GearSet, two_piece: GearSet = GearSet.FERVOR):
    return _items_for_sets((four_piece,) * 4 + (two_piece,) * 2)


def _two_plus_four(two_piece: GearSet, four_piece: GearSet = GearSet.LIFESTEAL):
    return _items_for_sets((two_piece,) * 2 + (four_piece,) * 4)


class SetEvaluationGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selection = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )

    def _evaluate(self, items, request=None):
        return aggregate_with_set_bonuses(
            _request(self.selection) if request is None else request,
            self.selection,
            ArtifactSelection(),
            items,
        )

    def test_every_numeric_primary_set_matches_hand_calculated_totals(self) -> None:
        cases = (
            (GearSet.ATTACK, _four_plus_two(GearSet.ATTACK), FinalStat.ATTACK, 1099),
            (GearSet.HEALTH, _two_plus_four(GearSet.HEALTH), FinalStat.HEALTH, 6991),
            (GearSet.DEFENSE, _two_plus_four(GearSet.DEFENSE), FinalStat.DEFENSE, 806),
            (GearSet.SPEED, _four_plus_two(GearSet.SPEED), FinalStat.SPEED, 118),
            (GearSet.CRITICAL, _two_plus_four(GearSet.CRITICAL), FinalStat.CRITICAL_HIT_CHANCE, 27),
            (GearSet.HIT, _two_plus_four(GearSet.HIT), FinalStat.EFFECTIVENESS, 20),
            (GearSet.DESTRUCTION, _four_plus_two(GearSet.DESTRUCTION), FinalStat.CRITICAL_HIT_DAMAGE, 210),
            (GearSet.RESIST, _two_plus_four(GearSet.RESIST), FinalStat.EFFECT_RESISTANCE, 32),
            (GearSet.REVENGE, _four_plus_two(GearSet.REVENGE), FinalStat.SPEED, 106),
            (GearSet.TORRENT, _two_plus_four(GearSet.TORRENT), FinalStat.HEALTH, 5243),
            (GearSet.REVERSAL, _four_plus_two(GearSet.REVERSAL), FinalStat.SPEED, 109),
            (GearSet.WARFARE, _four_plus_two(GearSet.WARFARE), FinalStat.HEALTH, 6991),
            (GearSet.WEAKENING, _four_plus_two(GearSet.WEAKENING), FinalStat.SPEED, 109),
        )
        for gear_set, items, stat, expected in cases:
            with self.subTest(gear_set=gear_set):
                result = self._evaluate(items)
                self.assertEqual(expected, result.value(stat))
                activation = result.diagnostics.activation_for(gear_set)
                self.assertIsNotNone(activation)
                self.assertTrue(activation.changes_primary_stats)
                self.assertEqual(1, activation.activation_count)

    def test_sanitized_fribbels_fixture_current_and_reforged_set_golden(self) -> None:
        parsed = parse_fribbels_gear_file(FIXTURES / "valid-enriched-export-utf8.txt")
        merged = merge_fribbels_inventory((), parsed)
        imported = {
            item.gear_item.slot: ProjectedGearItem.from_fribbels_inventory_item(
                item, dense_id=index
            )
            for index, item in enumerate(merged.items)
        }
        dense_id = len(imported)
        for slot in GearSlot:
            if slot not in imported:
                current = ProjectedGearItem.from_gear_item(
                    GearItem(
                        item_id=f"fixture.health.{slot.name.lower()}",
                        dense_id=dense_id,
                        slot=slot,
                        gear_set=GearSet.HEALTH,
                        main_stat=ItemStatType.FLAT_ATTACK,
                        main_stat_value=0,
                    )
                )
                imported[slot] = replace(
                    current,
                    reforged_totals=current.current_totals,
                    reforged_evidence=ItemProjectionEvidence.FRIBBELS_VALID,
                )
                dense_id += 1
        items = tuple(imported[slot] for slot in GearSlot)
        current = self._evaluate(
            items,
            _request(self.selection, mode=ItemProjectionMode.CURRENT),
        )
        reforged = self._evaluate(
            items,
            _request(self.selection, mode=ItemProjectionMode.REFORGED),
        )
        self.assertEqual(9455, current.value(FinalStat.HEALTH))
        self.assertEqual(9738, reforged.value(FinalStat.HEALTH))
        self.assertEqual(2, current.diagnostics.activation_for(GearSet.HEALTH).activation_count)

    def test_set_bonus_is_inserted_before_additive_hero_modifier(self) -> None:
        speed = HeroModifierContribution(HeroModifierStatType.SPEED, 5)
        modifiers = HeroModifiers(
            custom_bonuses=custom_bonus_projection((speed,)),
            custom_contributions=(speed,),
        )
        items = list(_four_plus_two(GearSet.SPEED))
        totals = dict(items[0].current_totals)
        reforged = totals | {ItemStatType.SPEED: 5}
        items[0] = replace(
            items[0],
            reforged_totals=reforged,
            reforged_evidence=ItemProjectionEvidence.FRIBBELS_VALID,
        )
        for index in range(1, len(items)):
            items[index] = replace(
                items[index],
                reforged_totals=items[index].current_totals,
                reforged_evidence=ItemProjectionEvidence.FRIBBELS_VALID,
            )

        current = self._evaluate(
            items,
            _request(self.selection, modifiers=modifiers),
        )
        projected = self._evaluate(
            items,
            _request(
                self.selection,
                mode=ItemProjectionMode.REFORGED,
                modifiers=modifiers,
            ),
        )
        self.assertEqual(123, current.value(FinalStat.SPEED))
        self.assertEqual(128, projected.value(FinalStat.SPEED))
        self.assertEqual(
            5,
            dict(current.diagnostics.pre_set_diagnostics.post_set_modifier_contributions)[
                FinalStat.SPEED
            ],
        )


class SetActivationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selection = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.base = dict(cls.selection.profile.final_stats)

    def _evaluate(self, items, request=None):
        return aggregate_with_set_bonuses(
            _request(self.selection) if request is None else request,
            self.selection,
            ArtifactSelection(),
            items,
        )

    def test_incomplete_sets_do_not_activate(self) -> None:
        items = _items_for_sets(
            (
                GearSet.ATTACK,
                GearSet.ATTACK,
                GearSet.ATTACK,
                GearSet.SPEED,
                GearSet.CRITICAL,
                GearSet.HIT,
            )
        )
        result = self._evaluate(items)
        self.assertEqual(self.base, dict(result.final_stats))
        self.assertEqual((), result.diagnostics.activations)

    def test_six_four_piece_items_activate_only_one_effect(self) -> None:
        result = self._evaluate(_items_for_sets((GearSet.ATTACK,) * 6))
        activation = result.diagnostics.activation_for(GearSet.ATTACK)
        self.assertEqual(1, activation.completed_groups)
        self.assertEqual(1, activation.activation_count)
        self.assertEqual(1099, result.value(FinalStat.ATTACK))

    def test_stackable_two_piece_sets_activate_per_complete_pair(self) -> None:
        cases = (
            (GearSet.HEALTH, FinalStat.HEALTH, 9321),
            (GearSet.CRITICAL, FinalStat.CRITICAL_HIT_CHANCE, 51),
            (GearSet.TORRENT, FinalStat.HEALTH, 4078),
        )
        for gear_set, stat, expected in cases:
            with self.subTest(gear_set=gear_set):
                result = self._evaluate(_items_for_sets((gear_set,) * 6))
                activation = result.diagnostics.activation_for(gear_set)
                self.assertEqual(3, activation.completed_groups)
                self.assertEqual(3, activation.activation_count)
                self.assertEqual(expected, result.value(stat))

    def test_nonstackable_two_piece_effect_records_raw_groups_but_activates_once(self) -> None:
        result = self._evaluate(_items_for_sets((GearSet.FERVOR,) * 6))
        activation = result.diagnostics.activation_for(GearSet.FERVOR)
        self.assertEqual(3, activation.completed_groups)
        self.assertEqual(1, activation.activation_count)
        self.assertFalse(activation.changes_primary_stats)
        self.assertEqual(self.base, dict(result.final_stats))

    def test_mixed_patterns_are_order_independent_and_target_independent(self) -> None:
        four_plus_two = _four_plus_two(GearSet.ATTACK, GearSet.HEALTH)
        triple_two = _items_for_sets(
            (GearSet.HEALTH,) * 2 + (GearSet.DEFENSE,) * 2 + (GearSet.CRITICAL,) * 2
        )
        result_42 = self._evaluate(four_plus_two)
        result_222 = self._evaluate(triple_two)
        self.assertEqual(1099, result_42.value(FinalStat.ATTACK))
        self.assertEqual(6991, result_42.value(FinalStat.HEALTH))
        self.assertEqual(6991, result_222.value(FinalStat.HEALTH))
        self.assertEqual(806, result_222.value(FinalStat.DEFENSE))
        self.assertEqual(27, result_222.value(FinalStat.CRITICAL_HIT_CHANCE))

        reversed_result = self._evaluate(tuple(reversed(four_plus_two)))
        different_target = self._evaluate(
            four_plus_two,
            _request(
                self.selection,
                pattern=SetPattern((GearSet.ATTACK, GearSet.HEALTH)),
            ),
        )
        self.assertEqual(result_42, reversed_result)
        self.assertEqual(result_42.final_stats, different_target.final_stats)
        self.assertEqual(result_42.diagnostics.activations, different_target.diagnostics.activations)

    def test_health_expression_order_is_explicit_for_warfare_and_torrent(self) -> None:
        result = self._evaluate(
            _items_for_sets((GearSet.WARFARE,) * 4 + (GearSet.TORRENT,) * 2)
        )
        self.assertEqual(6408, result.value(FinalStat.HEALTH))
        self.assertEqual(
            (GearSet.WARFARE, GearSet.TORRENT),
            result.diagnostics.numeric_application_order,
        )

    def test_all_non_numeric_sets_are_active_metadata_only(self) -> None:
        non_numeric = (
            GearSet.LIFESTEAL,
            GearSet.COUNTER,
            GearSet.UNITY,
            GearSet.RAGE,
            GearSet.IMMUNITY,
            GearSet.PENETRATION,
            GearSet.INJURY,
            GearSet.PROTECTION,
            GearSet.RIPOSTE,
            GearSet.PURSUIT,
            GearSet.FERVOR,
        )
        for gear_set in non_numeric:
            with self.subTest(gear_set=gear_set):
                metadata = SET_CATALOG[gear_set]
                items = (
                    _four_plus_two(gear_set)
                    if metadata.pieces_required == 4
                    else _two_plus_four(gear_set)
                )
                result = self._evaluate(items)
                activation = result.diagnostics.activation_for(gear_set)
                self.assertIsNotNone(activation)
                self.assertFalse(activation.changes_primary_stats)
                self.assertEqual(self.base, dict(result.final_stats))

    def test_piece_counts_follow_all_pinned_set_indices(self) -> None:
        result = self._evaluate(_four_plus_two(GearSet.WEAKENING, GearSet.FERVOR))
        counts = dict(result.diagnostics.piece_counts)
        self.assertEqual(tuple(GearSet), tuple(gear_set for gear_set, _ in result.diagnostics.piece_counts))
        self.assertEqual(4, counts[GearSet.WEAKENING])
        self.assertEqual(2, counts[GearSet.FERVOR])
        self.assertEqual(
            tuple(range(24)),
            tuple(SET_CATALOG[gear_set].fribbels_index for gear_set in GearSet),
        )
        self.assertFalse(hasattr(result, "metrics"))
        self.assertFalse(hasattr(result, "constraint_distance"))

    def test_set_stage_does_not_apply_crit_or_crit_damage_caps(self) -> None:
        items = list(_four_plus_two(GearSet.DESTRUCTION, GearSet.CRITICAL))
        totals = dict(items[0].current_totals)
        totals[ItemStatType.CRITICAL_HIT_CHANCE_PERCENT] = 100
        totals[ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT] = 300
        items[0] = replace(items[0], current_totals=totals)
        result = self._evaluate(items)
        self.assertEqual(127, result.value(FinalStat.CRITICAL_HIT_CHANCE))
        self.assertEqual(510, result.value(FinalStat.CRITICAL_HIT_DAMAGE))


if __name__ == "__main__":
    unittest.main()
