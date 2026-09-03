from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace

from src.optimizer.data import (
    ArtifactSelection,
    load_bundled_character_profile_selector,
)
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    FinalStat,
    GearItem,
    GearSet,
    GearSlot,
    HeroModifiers,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SetPattern,
    StatRange,
)
from src.optimizer.engine import (
    PRIMARY_STAT_RULES,
    PrimaryStatBoundSide,
    PrimaryStatBoundStatus,
    ProjectedGearItem,
    aggregate_with_set_bonuses,
    evaluate_primary_stat_bounds,
)


def _request(selection, stat_ranges=()) -> OptimizationRequest:
    return OptimizationRequest(
        request_id="request.primary-stat-bounds",
        hero_id=selection.hero_id,
        base_profile_id=selection.profile_id,
        modifiers=HeroModifiers(),
        set_pattern=SetPattern((GearSet.SPEED, GearSet.CRITICAL)),
        stat_ranges=stat_ranges,
        item_projection_mode=ItemProjectionMode.CURRENT,
    )


def _items_for_sets(sets: tuple[GearSet, ...]) -> tuple[ProjectedGearItem, ...]:
    if len(sets) != 6:
        raise AssertionError("Fixture must contain six set IDs.")
    return tuple(
        ProjectedGearItem.from_gear_item(
            GearItem(
                item_id=f"bound-item.{index}",
                dense_id=index,
                slot=slot,
                gear_set=gear_set,
                main_stat=ItemStatType.FLAT_ATTACK,
                main_stat_value=0,
            )
        )
        for index, (slot, gear_set) in enumerate(zip(GearSlot, sets, strict=True))
    )


class PrimaryStatBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selection = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.base_request = _request(cls.selection)
        cls.base_set_result = aggregate_with_set_bonuses(
            cls.base_request,
            cls.selection,
            ArtifactSelection(),
            _items_for_sets(
                (
                    GearSet.ATTACK,
                    GearSet.ATTACK,
                    GearSet.ATTACK,
                    GearSet.SPEED,
                    GearSet.CRITICAL,
                    GearSet.HIT,
                )
            ),
        )
        cls.fixture_values = {
            FinalStat.ATTACK: 1000,
            FinalStat.HEALTH: 10_000,
            FinalStat.DEFENSE: 1000,
            FinalStat.SPEED: 200,
            FinalStat.CRITICAL_HIT_CHANCE: 80,
            FinalStat.CRITICAL_HIT_DAMAGE: 300,
            FinalStat.EFFECTIVENESS: 50,
            FinalStat.EFFECT_RESISTANCE: 75,
        }
        cls.fixture_set_result = replace(
            cls.base_set_result,
            final_stats=tuple(
                (stat, cls.fixture_values[stat]) for stat in FINAL_STAT_ORDER
            ),
        )

    def _evaluate(self, stat_ranges=(), set_result=None):
        return evaluate_primary_stat_bounds(
            _request(self.selection, stat_ranges),
            self.fixture_set_result if set_result is None else set_result,
        )

    def test_all_primary_bounds_are_inclusive_and_report_the_failed_side(self) -> None:
        for stat in FINAL_STAT_ORDER:
            value = self.fixture_values[stat]
            with self.subTest(stat=stat, case="exact"):
                exact = self._evaluate({stat: StatRange(value, value)})
                self.assertTrue(exact.passes)
                self.assertIs(
                    exact.evaluation_for(stat).status,
                    PrimaryStatBoundStatus.PASSED,
                )

            with self.subTest(stat=stat, case="minimum"):
                below = self._evaluate({stat: StatRange(minimum=value + 1)})
                failure = below.failures[0]
                self.assertFalse(below.passes)
                self.assertIs(failure.status, PrimaryStatBoundStatus.BELOW_MINIMUM)
                self.assertIs(failure.failure_side, PrimaryStatBoundSide.MINIMUM)

            with self.subTest(stat=stat, case="maximum"):
                above = self._evaluate({stat: StatRange(maximum=value - 1)})
                failure = above.failures[0]
                self.assertFalse(above.passes)
                self.assertIs(failure.status, PrimaryStatBoundStatus.ABOVE_MAXIMUM)
                self.assertIs(failure.failure_side, PrimaryStatBoundSide.MAXIMUM)

    def test_omitted_blank_and_zero_ranges_remain_distinct(self) -> None:
        omitted = self._evaluate()
        self.assertTrue(omitted.passes)
        self.assertTrue(
            all(not evaluation.range_supplied for evaluation in omitted.evaluations)
        )

        result = self._evaluate(
            {
                FinalStat.ATTACK: StatRange(),
                FinalStat.SPEED: StatRange(minimum=0),
                FinalStat.HEALTH: StatRange(maximum=0),
            }
        )
        blank = result.evaluation_for(FinalStat.ATTACK)
        zero_minimum = result.evaluation_for(FinalStat.SPEED)
        zero_maximum = result.evaluation_for(FinalStat.HEALTH)
        self.assertTrue(blank.range_supplied)
        self.assertFalse(blank.constrained)
        self.assertIs(blank.status, PrimaryStatBoundStatus.UNRESTRICTED)
        self.assertTrue(zero_minimum.constrained)
        self.assertIs(zero_minimum.status, PrimaryStatBoundStatus.PASSED)
        self.assertEqual(0, zero_minimum.requested_range.minimum)
        self.assertIs(zero_maximum.status, PrimaryStatBoundStatus.ABOVE_MAXIMUM)
        self.assertEqual((FinalStat.HEALTH,), tuple(item.stat for item in result.failures))

    def test_cap_catalog_is_canonical_and_only_caps_crit_stats(self) -> None:
        self.assertEqual(
            tuple(FINAL_STAT_ORDER),
            tuple(rule.stat for rule in PRIMARY_STAT_RULES),
        )
        self.assertEqual(
            {
                FinalStat.CRITICAL_HIT_CHANCE: 100,
                FinalStat.CRITICAL_HIT_DAMAGE: 350,
            },
            {rule.stat: rule.upper_cap for rule in PRIMARY_STAT_RULES if rule.upper_cap},
        )

        huge_values = self.fixture_values | {
            FinalStat.ATTACK: 999_999,
            FinalStat.HEALTH: 999_999,
            FinalStat.DEFENSE: 999_999,
            FinalStat.SPEED: 999_999,
            FinalStat.EFFECTIVENESS: 999_999,
            FinalStat.EFFECT_RESISTANCE: 999_999,
        }
        set_result = replace(
            self.base_set_result,
            final_stats=tuple((stat, huge_values[stat]) for stat in FINAL_STAT_ORDER),
        )
        result = self._evaluate(set_result=set_result)
        for stat in (
            FinalStat.ATTACK,
            FinalStat.HEALTH,
            FinalStat.DEFENSE,
            FinalStat.SPEED,
            FinalStat.EFFECTIVENESS,
            FinalStat.EFFECT_RESISTANCE,
        ):
            with self.subTest(stat=stat):
                self.assertEqual(999_999, result.effective_value(stat))
                self.assertIsNone(result.evaluation_for(stat).upper_cap)

    def test_uncapped_crit_values_are_retained_beside_effective_caps(self) -> None:
        items = list(
            _items_for_sets((GearSet.DESTRUCTION,) * 4 + (GearSet.CRITICAL,) * 2)
        )
        totals = dict(items[0].current_totals)
        totals[ItemStatType.CRITICAL_HIT_CHANCE_PERCENT] = 100
        totals[ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT] = 300
        items[0] = replace(items[0], current_totals=totals)
        set_result = aggregate_with_set_bonuses(
            self.base_request,
            self.selection,
            ArtifactSelection(),
            items,
        )
        result = self._evaluate(
            {
                FinalStat.CRITICAL_HIT_CHANCE: StatRange(127, 127),
                FinalStat.CRITICAL_HIT_DAMAGE: StatRange(350, 350),
            },
            set_result,
        )
        self.assertTrue(result.passes)
        self.assertEqual(127, result.raw_value(FinalStat.CRITICAL_HIT_CHANCE))
        self.assertEqual(510, result.raw_value(FinalStat.CRITICAL_HIT_DAMAGE))
        self.assertEqual(100, result.effective_value(FinalStat.CRITICAL_HIT_CHANCE))
        self.assertEqual(350, result.effective_value(FinalStat.CRITICAL_HIT_DAMAGE))
        self.assertTrue(result.evaluation_for(FinalStat.CRITICAL_HIT_CHANCE).cap_applied)
        self.assertTrue(result.evaluation_for(FinalStat.CRITICAL_HIT_DAMAGE).cap_applied)
        self.assertEqual(set_result.final_stats, result.set_evaluation.final_stats)

    def test_crit_chance_bounds_use_raw_value_while_damage_uses_cap(self) -> None:
        capped = self._evaluate(
            {
                FinalStat.CRITICAL_HIT_CHANCE: StatRange(minimum=127),
                FinalStat.CRITICAL_HIT_DAMAGE: StatRange(minimum=350),
            },
            replace(
                self.fixture_set_result,
                final_stats=tuple(
                    (
                        stat,
                        127
                        if stat is FinalStat.CRITICAL_HIT_CHANCE
                        else 510
                        if stat is FinalStat.CRITICAL_HIT_DAMAGE
                        else self.fixture_values[stat],
                    )
                    for stat in FINAL_STAT_ORDER
                ),
            ),
        )
        self.assertTrue(capped.passes)

        overflow = self._evaluate(
            {FinalStat.CRITICAL_HIT_CHANCE: StatRange(99, 106)},
            replace(
                self.fixture_set_result,
                final_stats=tuple(
                    (stat, 124 if stat is FinalStat.CRITICAL_HIT_CHANCE else value)
                    for stat, value in self.fixture_set_result.final_stats
                ),
            ),
        )
        self.assertIs(
            overflow.evaluation_for(FinalStat.CRITICAL_HIT_CHANCE).status,
            PrimaryStatBoundStatus.ABOVE_MAXIMUM,
        )

        impossible = self._evaluate(
            {
                FinalStat.CRITICAL_HIT_CHANCE: StatRange(minimum=101),
                FinalStat.CRITICAL_HIT_DAMAGE: StatRange(minimum=351),
            }
        )
        self.assertEqual(
            (
                FinalStat.CRITICAL_HIT_CHANCE,
                FinalStat.CRITICAL_HIT_DAMAGE,
            ),
            tuple(failure.stat for failure in impossible.failures),
        )
        self.assertEqual(
            (
                PrimaryStatBoundStatus.BELOW_MINIMUM,
                PrimaryStatBoundStatus.MINIMUM_ABOVE_CAP,
            ),
            tuple(failure.status for failure in impossible.failures),
        )
        self.assertTrue(
            all(
                failure.failure_side is PrimaryStatBoundSide.MINIMUM
                for failure in impossible.failures
            )
        )

    def test_bounds_consume_completed_stacked_and_negative_set_stats(self) -> None:
        health_result = aggregate_with_set_bonuses(
            self.base_request,
            self.selection,
            ArtifactSelection(),
            _items_for_sets((GearSet.HEALTH,) * 6),
        )
        health_bounds = self._evaluate(
            {FinalStat.HEALTH: StatRange(9321, 9321)},
            health_result,
        )
        self.assertTrue(health_bounds.passes)
        self.assertEqual(3, health_result.diagnostics.activation_for(GearSet.HEALTH).activation_count)

        torrent_result = aggregate_with_set_bonuses(
            self.base_request,
            self.selection,
            ArtifactSelection(),
            _items_for_sets((GearSet.TORRENT,) * 6),
        )
        torrent_bounds = self._evaluate(
            {FinalStat.HEALTH: StatRange(maximum=4077)},
            torrent_result,
        )
        self.assertEqual(4078, torrent_bounds.raw_value(FinalStat.HEALTH))
        self.assertIs(
            torrent_bounds.failures[0].status,
            PrimaryStatBoundStatus.ABOVE_MAXIMUM,
        )
        self.assertIs(torrent_bounds.set_evaluation, torrent_result)

    def test_evaluations_and_failures_are_canonical_and_immutable(self) -> None:
        ranges = {
            FinalStat.EFFECT_RESISTANCE: StatRange(minimum=76),
            FinalStat.EFFECTIVENESS: StatRange(),
            FinalStat.ATTACK: StatRange(maximum=999),
        }
        result = self._evaluate(ranges)
        self.assertEqual(
            tuple(FINAL_STAT_ORDER),
            tuple(evaluation.stat for evaluation in result.evaluations),
        )
        self.assertEqual(
            (FinalStat.ATTACK, FinalStat.EFFECT_RESISTANCE),
            tuple(failure.stat for failure in result.failures),
        )
        self.assertIsInstance(hash(result), int)
        with self.assertRaises(FrozenInstanceError):
            result.raw_final_stats = ()

    def test_bounds_layer_does_not_add_later_engine_outputs(self) -> None:
        result = self._evaluate()
        self.assertFalse(hasattr(result, "metrics"))
        self.assertFalse(hasattr(result, "priority_score"))
        self.assertFalse(hasattr(result, "constraint_distance"))
        self.assertFalse(hasattr(result, "set_pattern_match"))


if __name__ == "__main__":
    unittest.main()
