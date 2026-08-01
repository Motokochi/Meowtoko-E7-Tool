from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace

from src.optimizer.data import (
    ArtifactSelection,
    load_bundled_artifact_repository,
    load_bundled_character_profile_selector,
    load_bundled_skill_context_repository,
)
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    DomainValidationError,
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
    SkillContext,
    SkillSlot,
    StatRange,
    custom_bonus_projection,
)
from src.optimizer.engine import (
    FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_GIT_BLOB_SHA1,
    FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_REVISION,
    PRIORITY_NORMALIZATION_RULES,
    ProjectedGearItem,
    aggregate_with_set_bonuses,
    calculate_derived_metrics,
    evaluate_primary_stat_bounds,
    score_final_build_priority,
)
from src.optimizer.engine.stat_aggregation import _f32


NON_NUMERIC_SETS = (GearSet.IMMUNITY,) * 6
ONE_ROLL_ITEM_STATS = {
    FinalStat.ATTACK: (ItemStatType.ATTACK_PERCENT, 8),
    FinalStat.HEALTH: (ItemStatType.HEALTH_PERCENT, 8),
    FinalStat.DEFENSE: (ItemStatType.DEFENSE_PERCENT, 8),
    FinalStat.SPEED: (ItemStatType.SPEED, 4),
    FinalStat.CRITICAL_HIT_CHANCE: (
        ItemStatType.CRITICAL_HIT_CHANCE_PERCENT,
        5,
    ),
    FinalStat.CRITICAL_HIT_DAMAGE: (
        ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
        7,
    ),
    FinalStat.EFFECTIVENESS: (ItemStatType.EFFECTIVENESS_PERCENT, 8),
    FinalStat.EFFECT_RESISTANCE: (
        ItemStatType.EFFECT_RESISTANCE_PERCENT,
        8,
    ),
}


def _items(
    sets: tuple[GearSet, ...] = NON_NUMERIC_SETS,
    item_stat: ItemStatType | None = None,
    value: int | float = 0,
) -> tuple[ProjectedGearItem, ...]:
    if len(sets) != 6:
        raise AssertionError("A scoring fixture requires six set IDs.")
    result = []
    for index, (slot, gear_set) in enumerate(zip(GearSlot, sets, strict=True)):
        main_stat = item_stat if index == 0 and item_stat is not None else ItemStatType.FLAT_ATTACK
        main_value = value if index == 0 and item_stat is not None else 0
        result.append(
            ProjectedGearItem.from_gear_item(
                GearItem(
                    item_id=f"priority-item.{index}",
                    dense_id=index,
                    slot=slot,
                    gear_set=gear_set,
                    main_stat=main_stat,
                    main_stat_value=main_value,
                )
            )
        )
    return tuple(result)


class FinalBuildPriorityScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.skills = load_bundled_skill_context_repository()
        cls.contexts = tuple(SkillContext(skill, 1000) for skill in SkillSlot)

    def _request(
        self,
        *,
        priorities=(),
        modifiers: HeroModifiers = HeroModifiers(),
        stat_ranges=(),
        derived_ranges=(),
    ) -> OptimizationRequest:
        return OptimizationRequest(
            request_id="request.priority-scoring",
            hero_id=self.profile.hero_id,
            base_profile_id=self.profile.profile_id,
            modifiers=modifiers,
            set_pattern=SetPattern((GearSet.SPEED, GearSet.CRITICAL)),
            stat_priorities=priorities,
            stat_ranges=stat_ranges,
            derived_metric_ranges=derived_ranges,
            skill_contexts=self.contexts,
            item_projection_mode=ItemProjectionMode.CURRENT,
        )

    def _derive(
        self,
        request: OptimizationRequest,
        items: tuple[ProjectedGearItem, ...],
        artifact: ArtifactSelection = ArtifactSelection(),
    ):
        primary = evaluate_primary_stat_bounds(
            request,
            aggregate_with_set_bonuses(request, self.profile, artifact, items),
        )
        return calculate_derived_metrics(
            request,
            primary,
            self.skills.select(request.hero_id, request.skill_contexts),
        )

    def _score(
        self,
        stat: FinalStat,
        weight: int,
        *,
        items: tuple[ProjectedGearItem, ...] | None = None,
        modifiers: HeroModifiers = HeroModifiers(),
        artifact: ArtifactSelection = ArtifactSelection(),
    ):
        request = self._request(priorities=((stat, weight),), modifiers=modifiers)
        selected_items = _items() if items is None else items
        return score_final_build_priority(
            request,
            self._derive(request, selected_items, artifact),
        )

    def test_pinned_source_and_normalization_catalog_are_stable(self) -> None:
        self.assertEqual(
            "b291cbbc415f11abede146859edc7b67d26e9c4b",
            FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_REVISION,
        )
        self.assertEqual(
            "f1aacc90e5e45c6724c8d4521a85de39976f4be3",
            FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_GIT_BLOB_SHA1,
        )
        self.assertEqual(FINAL_STAT_ORDER, tuple(rule.stat for rule in PRIORITY_NORMALIZATION_RULES))
        self.assertEqual(
            (0.08, 0.08, 0.08, 4, 5, 7, 8, 8),
            tuple(
                rule.base_ratio if rule.base_ratio is not None else rule.fixed_divisor
                for rule in PRIORITY_NORMALIZATION_RULES
            ),
        )

    def test_every_weight_has_exact_one_roll_evidence(self) -> None:
        item_stat, value = ONE_ROLL_ITEM_STATS[FinalStat.ATTACK]
        items = _items(item_stat=item_stat, value=value)
        for weight in (-1, 0, 1, 2, 3):
            with self.subTest(weight=weight):
                result = self._score(FinalStat.ATTACK, weight, items=items)
                evidence = result.diagnostics.for_stat(FinalStat.ATTACK)
                self.assertAlmostEqual(1, evidence.normalized_contribution, places=6)
                expected = _f32(_f32(evidence.normalized_contribution) * _f32(weight))
                self.assertEqual(expected, evidence.weighted_term)
                self.assertEqual(expected, result.priority_score)

    def test_omitted_and_explicit_zero_are_distinguishable_but_neutral(self) -> None:
        item_stat, value = ONE_ROLL_ITEM_STATS[FinalStat.SPEED]
        items = _items(item_stat=item_stat, value=value)
        omitted_request = self._request()
        explicit_request = self._request(priorities=((FinalStat.SPEED, 0),))
        omitted = score_final_build_priority(
            omitted_request, self._derive(omitted_request, items)
        )
        explicit = score_final_build_priority(
            explicit_request, self._derive(explicit_request, items)
        )
        omitted_stat = omitted.diagnostics.for_stat(FinalStat.SPEED)
        explicit_stat = explicit.diagnostics.for_stat(FinalStat.SPEED)
        self.assertFalse(omitted_stat.priority_supplied)
        self.assertTrue(explicit_stat.priority_supplied)
        self.assertEqual(0, omitted_stat.weight)
        self.assertEqual(0, explicit_stat.weight)
        self.assertEqual(0, omitted.priority_score)
        self.assertEqual(0, explicit.priority_score)

    def test_all_eight_one_roll_units_are_comparable_and_monotonic(self) -> None:
        normalized = {}
        for stat in FINAL_STAT_ORDER:
            item_stat, value = ONE_ROLL_ITEM_STATS[stat]
            items = _items(item_stat=item_stat, value=value)
            positive = self._score(stat, 1, items=items)
            negative = self._score(stat, -1, items=items)
            zero = self._score(stat, 1, items=_items())
            unrelated_stat = (
                FinalStat.EFFECTIVENESS
                if stat is FinalStat.SPEED
                else FinalStat.SPEED
            )
            unrelated_type, unrelated_value = ONE_ROLL_ITEM_STATS[unrelated_stat]
            mixed = list(items)
            second = mixed[1]
            mixed_totals = dict(second.current_totals)
            mixed_totals[unrelated_type] = unrelated_value
            mixed[1] = replace(second, current_totals=mixed_totals)
            unrelated = self._score(stat, 1, items=tuple(mixed))
            diagnostic = positive.diagnostics.for_stat(stat)
            normalized[stat] = diagnostic.normalized_contribution
            with self.subTest(stat=stat):
                self.assertAlmostEqual(1, diagnostic.normalized_contribution, places=5)
                self.assertGreater(positive.priority_score, zero.priority_score)
                self.assertLess(negative.priority_score, 0)
                self.assertAlmostEqual(
                    -positive.priority_score, negative.priority_score, places=6
                )
                self.assertEqual(
                    diagnostic.weighted_term,
                    unrelated.diagnostics.for_stat(stat).weighted_term,
                )
        self.assertAlmostEqual(normalized[FinalStat.HEALTH], normalized[FinalStat.SPEED], places=5)
        self.assertAlmostEqual(
            normalized[FinalStat.HEALTH],
            normalized[FinalStat.CRITICAL_HIT_CHANCE],
            places=5,
        )
        health = self._score(
            FinalStat.HEALTH,
            1,
            items=_items(item_stat=ItemStatType.HEALTH_PERCENT, value=8),
        ).diagnostics.for_stat(FinalStat.HEALTH)
        speed = self._score(
            FinalStat.SPEED,
            1,
            items=_items(item_stat=ItemStatType.SPEED, value=4),
        ).diagnostics.for_stat(FinalStat.SPEED)
        self.assertGreater(health.raw_contribution, speed.raw_contribution * 100)
        self.assertAlmostEqual(
            health.normalized_contribution,
            speed.normalized_contribution,
            places=5,
        )

    def test_unrelated_stat_does_not_change_weighted_term(self) -> None:
        attack_stat, attack_value = ONE_ROLL_ITEM_STATS[FinalStat.ATTACK]
        attack_only = self._score(
            FinalStat.ATTACK,
            3,
            items=_items(item_stat=attack_stat, value=attack_value),
        )
        mixed = list(_items(item_stat=attack_stat, value=attack_value))
        second = mixed[1]
        totals = dict(second.current_totals)
        totals[ItemStatType.SPEED] = 40
        mixed[1] = replace(second, current_totals=totals)
        mixed_result = self._score(FinalStat.ATTACK, 3, items=tuple(mixed))
        self.assertEqual(
            attack_only.diagnostics.for_stat(FinalStat.ATTACK).weighted_term,
            mixed_result.diagnostics.for_stat(FinalStat.ATTACK).weighted_term,
        )
        self.assertEqual(attack_only.priority_score, mixed_result.priority_score)

    def test_flat_and_percentage_base_relative_stats_share_final_stat_units(self) -> None:
        base = dict(self.profile.profile.final_stats)
        cases = (
            (FinalStat.ATTACK, ItemStatType.FLAT_ATTACK, ItemStatType.ATTACK_PERCENT),
            (FinalStat.HEALTH, ItemStatType.FLAT_HEALTH, ItemStatType.HEALTH_PERCENT),
            (FinalStat.DEFENSE, ItemStatType.FLAT_DEFENSE, ItemStatType.DEFENSE_PERCENT),
        )
        for stat, flat_type, percent_type in cases:
            with self.subTest(stat=stat):
                flat = self._score(
                    stat,
                    1,
                    items=_items(item_stat=flat_type, value=base[stat] * 0.08),
                )
                percent = self._score(
                    stat, 1, items=_items(item_stat=percent_type, value=8)
                )
                self.assertAlmostEqual(flat.priority_score, 1, places=5)
                self.assertAlmostEqual(percent.priority_score, 1, places=5)
                self.assertAlmostEqual(flat.priority_score, percent.priority_score, places=5)

    def test_completed_set_bonuses_do_not_affect_item_priority(self) -> None:
        cases = (
            (GearSet.ATTACK, 4, FinalStat.ATTACK, 5.625),
            (GearSet.HEALTH, 2, FinalStat.HEALTH, 2.5),
            (GearSet.DEFENSE, 2, FinalStat.DEFENSE, 2.5),
            (GearSet.SPEED, 4, FinalStat.SPEED, 5.75),
            (GearSet.REVENGE, 4, FinalStat.SPEED, 2.75),
            (GearSet.REVERSAL, 4, FinalStat.SPEED, 3.5),
            (GearSet.WEAKENING, 4, FinalStat.SPEED, 3.5),
            (GearSet.CRITICAL, 2, FinalStat.CRITICAL_HIT_CHANCE, 2.4),
            (GearSet.DESTRUCTION, 4, FinalStat.CRITICAL_HIT_DAMAGE, 60 / 7),
            (GearSet.HIT, 2, FinalStat.EFFECTIVENESS, 2.5),
            (GearSet.RESIST, 2, FinalStat.EFFECT_RESISTANCE, 2.5),
            (GearSet.WARFARE, 4, FinalStat.HEALTH, 2.5),
            (GearSet.TORRENT, 2, FinalStat.HEALTH, -1.25),
        )
        for gear_set, count, stat, _ in cases:
            filler_count = 6 - count
            filler = GearSet.RAGE if filler_count == 4 else GearSet.IMMUNITY
            sets = (gear_set,) * count + (filler,) * filler_count
            with self.subTest(gear_set=gear_set):
                result = self._score(stat, 1, items=_items(sets))
                self.assertEqual(0, result.priority_score)

        non_numeric = self._score(FinalStat.ATTACK, 1, items=_items())
        self.assertEqual(0, non_numeric.priority_score)

    def test_configured_non_gear_values_are_part_of_the_naked_baseline(self) -> None:
        artifacts = load_bundled_artifact_repository()
        artifact = artifacts.select(artifacts.artifacts[0].artifact_id, level=15)
        imprint = HeroModifierContribution(HeroModifierStatType.ATTACK_PERCENT, 0.10)
        ee = HeroModifierContribution(HeroModifierStatType.FLAT_HEALTH, 100)
        custom = (
            HeroModifierContribution(HeroModifierStatType.DEFENSE_PERCENT, 0.10),
            HeroModifierContribution(HeroModifierStatType.SPEED, 5),
            HeroModifierContribution(HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT, 1.00),
            HeroModifierContribution(HeroModifierStatType.EFFECTIVENESS_PERCENT, 0.10),
            HeroModifierContribution(HeroModifierStatType.EFFECT_RESISTANCE_PERCENT, 0.10),
            HeroModifierContribution(HeroModifierStatType.FINAL_ATTACK_PERCENT, 0.20),
            HeroModifierContribution(HeroModifierStatType.FINAL_HEALTH_PERCENT, 0.15),
            HeroModifierContribution(HeroModifierStatType.FINAL_DEFENSE_PERCENT, 0.10),
        )
        modifiers = replace(
            artifact.to_artifact_only_modifiers(),
            imprint_level="A",
            imprint_bonuses=imprint.legacy_final_stat_bonus(),
            imprint_contribution=imprint,
            exclusive_equipment_id="exclusive-equipment.synthetic",
            exclusive_equipment_bonuses=ee.legacy_final_stat_bonus(),
            exclusive_equipment_contribution=ee,
            custom_bonuses=custom_bonus_projection(custom),
            custom_contributions=custom,
        )
        request = self._request(
            priorities=tuple((stat, 3) for stat in reversed(FINAL_STAT_ORDER)),
            modifiers=modifiers,
        )
        derived = self._derive(request, _items(), artifact)
        result = score_final_build_priority(request, derived)
        self.assertEqual(modifiers, HeroModifiers.from_dict(modifiers.to_dict()))
        self.assertEqual(0, result.priority_score)
        self.assertTrue(all(item.raw_contribution == 0 for item in result.diagnostics.stats))
        self.assertEqual(
            dict(
                derived.diagnostics.primary_bounds.set_evaluation.diagnostics.pre_set_diagnostics.configured_naked_stats
            ),
            dict(
                derived.diagnostics.primary_bounds.set_evaluation.diagnostics.unrounded_final_stats
            ),
        )

    def test_source_final_multiplier_and_set_bonus_do_not_affect_item_priority(self) -> None:
        profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.aki"
        )
        contexts = tuple(SkillContext(skill, 1000) for skill in SkillSlot)
        final_attack = HeroModifierContribution(
            HeroModifierStatType.FINAL_ATTACK_PERCENT, 0.25
        )
        modifiers = HeroModifiers(
            custom_bonuses=custom_bonus_projection((final_attack,)),
            custom_contributions=(final_attack,),
        )
        request = OptimizationRequest(
            request_id="request.priority-final-multiplier",
            hero_id=profile.hero_id,
            base_profile_id=profile.profile_id,
            modifiers=modifiers,
            set_pattern=SetPattern((GearSet.ATTACK, GearSet.IMMUNITY)),
            stat_priorities=((FinalStat.ATTACK, 1),),
            skill_contexts=contexts,
            item_projection_mode=ItemProjectionMode.CURRENT,
        )
        sets = (GearSet.ATTACK,) * 4 + (GearSet.IMMUNITY,) * 2
        items = _items(sets, ItemStatType.ATTACK_PERCENT, 8)
        primary = evaluate_primary_stat_bounds(
            request,
            aggregate_with_set_bonuses(request, profile, ArtifactSelection(), items),
        )
        derived = calculate_derived_metrics(
            request,
            primary,
            self.skills.select(request.hero_id, request.skill_contexts),
        )
        result = score_final_build_priority(request, derived)
        diagnostic = result.diagnostics.for_stat(FinalStat.ATTACK)
        base_attack = dict(profile.profile.final_stats)[FinalStat.ATTACK]
        self.assertEqual(1.75, dict(primary.set_evaluation.diagnostics.pre_set_diagnostics.final_stat_multipliers)[FinalStat.ATTACK])
        self.assertEqual(0, diagnostic.baseline_value)
        expected_divisor = _f32(_f32(base_attack) * _f32(0.08))
        self.assertEqual(expected_divisor, diagnostic.normalization_divisor)
        self.assertEqual(1, result.priority_score)

    def test_critical_stats_score_owned_item_values_without_final_build_caps(self) -> None:
        cases = (
            (FinalStat.CRITICAL_HIT_CHANCE, ItemStatType.CRITICAL_HIT_CHANCE_PERCENT, 85, 185),
            (FinalStat.CRITICAL_HIT_DAMAGE, ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT, 200, 300),
        )
        for stat, item_stat, at_cap, over_cap in cases:
            with self.subTest(stat=stat):
                capped = self._score(stat, 1, items=_items(item_stat=item_stat, value=at_cap))
                over = self._score(stat, 1, items=_items(item_stat=item_stat, value=over_cap))
                self.assertGreater(over.priority_score, capped.priority_score)
                capped_primary = capped.derived_result.diagnostics.primary_bounds
                over_primary = over.derived_result.diagnostics.primary_bounds
                self.assertLess(capped_primary.raw_value(stat), over_primary.raw_value(stat))
                self.assertEqual(capped_primary.effective_value(stat), over_primary.effective_value(stat))

    def test_each_piece_is_rounded_before_the_six_scores_are_summed(self) -> None:
        first = list(_items())
        for index in (0, 1):
            item = first[index]
            totals = dict(item.current_totals)
            totals[ItemStatType.SPEED] = 1.96
            first[index] = replace(item, current_totals=totals)
        result = self._score(FinalStat.SPEED, 1, items=tuple(first))
        self.assertEqual(0, result.priority_score)
        self.assertEqual((0, 0), tuple(piece.rounded_score for piece in result.diagnostics.pieces[:2]))
        self.assertAlmostEqual(
            0.49,
            result.diagnostics.pieces[0].unrounded_score,
            places=5,
        )

    def test_order_repeatability_immutability_and_source_preservation(self) -> None:
        priorities = {
            FinalStat.SPEED: 2,
            FinalStat.HEALTH: -1,
            FinalStat.ATTACK: 3,
        }
        request = self._request(priorities=priorities)
        items = _items(item_stat=ItemStatType.ATTACK_PERCENT, value=8)
        derived = self._derive(request, items)
        first = score_final_build_priority(request, derived)
        second = score_final_build_priority(request, derived)
        reversed_request = self._request(priorities=tuple(reversed(tuple(priorities.items()))))
        reversed_derived = self._derive(reversed_request, items)
        reversed_result = score_final_build_priority(reversed_request, reversed_derived)
        self.assertEqual(first, second)
        self.assertEqual(first.priority_score, reversed_result.priority_score)
        self.assertEqual(first.diagnostics, reversed_result.diagnostics)
        self.assertEqual(FINAL_STAT_ORDER, tuple(item.stat for item in first.diagnostics.stats))
        self.assertEqual(derived.metrics.final_stats, first.metrics.final_stats)
        self.assertEqual(derived.metrics.derived_metrics, first.metrics.derived_metrics)
        self.assertIs(derived.evaluations, first.derived_result.evaluations)
        self.assertIs(derived.diagnostics, first.derived_result.diagnostics)
        self.assertEqual(0, derived.metrics.priority_score)
        with self.assertRaises(FrozenInstanceError):
            first.diagnostics.stats = ()

    def test_bound_failures_do_not_prevent_scoring(self) -> None:
        request = self._request(
            priorities=((FinalStat.ATTACK, 1),),
            stat_ranges=((FinalStat.ATTACK, StatRange(minimum=999999)),),
            derived_ranges=(("metric.cp", StatRange(minimum=999999999)),),
        )
        item_stat, value = ONE_ROLL_ITEM_STATS[FinalStat.ATTACK]
        derived = self._derive(request, _items(item_stat=item_stat, value=value))
        result = score_final_build_priority(request, derived)
        self.assertFalse(derived.diagnostics.primary_bounds.passes)
        self.assertFalse(derived.passes)
        self.assertAlmostEqual(1, result.priority_score, places=5)

    def test_invalid_types_and_weights_fail_at_the_boundary(self) -> None:
        with self.assertRaises(DomainValidationError):
            self._request(priorities=((FinalStat.ATTACK, 4),))
        with self.assertRaises(DomainValidationError):
            self._request(priorities=((FinalStat.ATTACK, 1.5),))
        request = self._request()
        derived = self._derive(request, _items())
        with self.assertRaises(TypeError):
            score_final_build_priority(object(), derived)
        with self.assertRaises(TypeError):
            score_final_build_priority(request, object())

    def test_future_stages_remain_out_of_scope(self) -> None:
        result = self._score(FinalStat.ATTACK, 1)
        for name in (
            "normalized_distance",
            "replacement_plan",
            "result_category",
            "target_set",
            "cuda",
        ):
            self.assertFalse(hasattr(result, name))


if __name__ == "__main__":
    unittest.main()
