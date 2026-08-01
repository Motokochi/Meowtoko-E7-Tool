from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError, replace

from src.optimizer.data import (
    ArtifactSelection,
    load_bundled_character_profile_selector,
    load_bundled_skill_context_repository,
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
    SkillContext,
    SkillHitType,
    SkillSlot,
    StatRange,
)
from src.optimizer.engine import (
    DERIVED_METRIC_IDS,
    DERIVED_METRIC_RULES,
    FRIBBELS_DERIVED_METRIC_CALCULATOR_GIT_BLOB_SHA1,
    FRIBBELS_DERIVED_METRIC_GPU_KERNEL_GIT_BLOB_SHA1,
    FRIBBELS_DERIVED_METRIC_SOURCE_REVISION,
    FRIBBELS_GEAR_SCORE_GIT_BLOB_SHA1,
    FRIBBELS_SKILL_MAPPING_GIT_BLOB_SHA1,
    DerivedMetricBoundSide,
    DerivedMetricBoundStatus,
    DerivedMetricError,
    ItemProjectionEvidence,
    ProjectedGearItem,
    SkillMetricKind,
    aggregate_with_set_bonuses,
    calculate_derived_metrics,
    evaluate_primary_stat_bounds,
)
from src.optimizer.engine.stat_aggregation import _f32


GOLDEN_SETS = (
    GearSet.RAGE,
    GearSet.RAGE,
    GearSet.RAGE,
    GearSet.RAGE,
    GearSet.PENETRATION,
    GearSet.PENETRATION,
)


def _golden_items(sets: tuple[GearSet, ...] = GOLDEN_SETS) -> tuple[ProjectedGearItem, ...]:
    definitions = (
        (
            GearSlot.WEAPON,
            ItemStatType.FLAT_ATTACK,
            500,
            ((ItemStatType.ATTACK_PERCENT, 20), (ItemStatType.SPEED, 10)),
        ),
        (
            GearSlot.HELMET,
            ItemStatType.FLAT_HEALTH,
            2500,
            ((ItemStatType.HEALTH_PERCENT, 20), (ItemStatType.DEFENSE_PERCENT, 10)),
        ),
        (
            GearSlot.ARMOR,
            ItemStatType.FLAT_DEFENSE,
            300,
            (
                (ItemStatType.DEFENSE_PERCENT, 20),
                (ItemStatType.EFFECT_RESISTANCE_PERCENT, 30),
            ),
        ),
        (
            GearSlot.NECKLACE,
            ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
            65,
            ((ItemStatType.CRITICAL_HIT_CHANCE_PERCENT, 40),),
        ),
        (
            GearSlot.RING,
            ItemStatType.EFFECTIVENESS_PERCENT,
            65,
            ((ItemStatType.EFFECT_RESISTANCE_PERCENT, 20),),
        ),
        (
            GearSlot.BOOTS,
            ItemStatType.SPEED,
            45,
            ((ItemStatType.ATTACK_PERCENT, 20),),
        ),
    )
    return tuple(
        ProjectedGearItem.from_gear_item(
            GearItem(
                item_id=f"metric-item.{index}",
                dense_id=index,
                slot=slot,
                gear_set=gear_set,
                main_stat=main_stat,
                main_stat_value=main_value,
                substats=substats,
            )
        )
        for index, ((slot, main_stat, main_value, substats), gear_set) in enumerate(
            zip(definitions, sets, strict=True)
        )
    )


def _zero_items(sets: tuple[GearSet, ...]) -> tuple[ProjectedGearItem, ...]:
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


class DerivedMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.skills = load_bundled_skill_context_repository()
        cls.golden_contexts = (
            SkillContext(SkillSlot.S1, 1500, hit_type=SkillHitType.CRITICAL),
            SkillContext(SkillSlot.S2, 1200, hit_type=SkillHitType.NORMAL),
            SkillContext(SkillSlot.S3, 0, hit_type=SkillHitType.MISS),
        )
        cls.golden_request = cls._request(contexts=cls.golden_contexts)
        cls.golden_primary = cls._primary(cls.golden_request, _golden_items())
        cls.golden_result = cls._calculate(cls.golden_request, cls.golden_primary)

    @classmethod
    def _request(
        cls,
        *,
        hero_id: str | None = None,
        contexts: tuple[SkillContext, ...] | None = None,
        derived_ranges=(),
        stat_ranges=(),
        target_defense: int = 1500,
        projection_mode: ItemProjectionMode = ItemProjectionMode.CURRENT,
        modifiers: HeroModifiers = HeroModifiers(),
    ) -> OptimizationRequest:
        selected_hero = cls.profile.hero_id if hero_id is None else hero_id
        selected_contexts = (
            tuple(SkillContext(skill, target_defense) for skill in SkillSlot)
            if contexts is None
            else contexts
        )
        return OptimizationRequest(
            request_id="request.derived-metrics",
            hero_id=selected_hero,
            base_profile_id=cls.profile.profile_id,
            modifiers=modifiers,
            set_pattern=SetPattern((GearSet.RAGE, GearSet.PENETRATION)),
            stat_ranges=stat_ranges,
            derived_metric_ranges=derived_ranges,
            target_defense=target_defense,
            skill_contexts=selected_contexts,
            item_projection_mode=projection_mode,
        )

    @classmethod
    def _primary(cls, request, items):
        return evaluate_primary_stat_bounds(
            request,
            aggregate_with_set_bonuses(request, cls.profile, ArtifactSelection(), items),
        )

    @classmethod
    def _calculate(cls, request, primary):
        return calculate_derived_metrics(
            request,
            primary,
            cls.skills.select(request.hero_id, request.skill_contexts),
        )

    def _hero_id(self, name: str) -> str:
        return next(
            hero.hero_id for hero in self.skills.character_repository.heroes if hero.name == name
        )

    def test_pinned_source_evidence_and_catalog_are_stable(self) -> None:
        self.assertEqual(
            "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb",
            FRIBBELS_DERIVED_METRIC_SOURCE_REVISION,
        )
        self.assertEqual(
            "dfd9b1e363905a0aef3a2fca2e3369acde8d020e",
            FRIBBELS_DERIVED_METRIC_CALCULATOR_GIT_BLOB_SHA1,
        )
        self.assertEqual(
            "80d34477fd0548be8f63f4086884756febac5425",
            FRIBBELS_DERIVED_METRIC_GPU_KERNEL_GIT_BLOB_SHA1,
        )
        self.assertEqual("de493420a0e6167c7a066f5d35a7a4f4e3edd623", FRIBBELS_GEAR_SCORE_GIT_BLOB_SHA1)
        self.assertEqual("af788037c2fd4f8fb08b426d3c4b10ab8bdb2568", FRIBBELS_SKILL_MAPPING_GIT_BLOB_SHA1)
        self.assertEqual(tuple(sorted(DERIVED_METRIC_IDS)), DERIVED_METRIC_IDS)
        self.assertEqual(DERIVED_METRIC_IDS, tuple(rule.metric_id for rule in DERIVED_METRIC_RULES))
        self.assertEqual(15, len(DERIVED_METRIC_IDS))
        self.assertIn("metric.ehp", DERIVED_METRIC_IDS)
        self.assertIn("metric.damage", DERIVED_METRIC_IDS)
        self.assertIn("metric.mcd", DERIVED_METRIC_IDS)

    def test_all_non_skill_and_skill_metrics_match_golden_fixture(self) -> None:
        self.assertEqual(
            {
                "metric.build_score": 606,
                "metric.cp": 49150,
                "metric.damage": 3786,
                "metric.damage_defense": 3748,
                "metric.damage_health": 3031,
                "metric.damage_speed": 567,
                "metric.ehp": 46620,
                "metric.ehp_speed": 6993,
                "metric.gear_score": 224,
                "metric.hp_speed": 1423,
                "metric.mcd": 4986,
                "metric.mcd_speed": 747,
                "metric.s1": 2371,
                "metric.s2": 1294,
                "metric.s3": 4640,
            },
            dict(self.golden_result.metrics.derived_metrics),
        )
        self.assertEqual(
            (1561, 9491, 1173, 150, 55, 215, 65, 62),
            tuple(value for _, value in self.golden_result.metrics.final_stats),
        )
        self.assertEqual(0, self.golden_result.metrics.priority_score)
        self.assertEqual(
            (40, 30, 50, 64, 20, 20),
            tuple(item.score for item in self.golden_result.diagnostics.gear_scores),
        )
        self.assertNotEqual(
            self.golden_result.value("metric.build_score"),
            self.golden_result.value("metric.gear_score"),
        )

    def test_binary32_inputs_and_cp_binary64_promotion_match_boundary(self) -> None:
        inputs = {
            FinalStat.ATTACK: 4945.3896484375,
            FinalStat.HEALTH: 23322.98828125,
            FinalStat.DEFENSE: 1807.5819091796875,
            FinalStat.SPEED: 325,
            FinalStat.CRITICAL_HIT_CHANCE: 58,
            FinalStat.CRITICAL_HIT_DAMAGE: 102,
            FinalStat.EFFECTIVENESS: 202,
            FinalStat.EFFECT_RESISTANCE: 173,
        }
        set_result = self.golden_primary.set_evaluation
        changed_set = replace(
            set_result,
            final_stats=tuple((stat, math.trunc(inputs[stat])) for stat in FINAL_STAT_ORDER),
            diagnostics=replace(
                set_result.diagnostics,
                unrounded_final_stats=tuple(
                    (stat, _f32(inputs[stat])) for stat in FINAL_STAT_ORDER
                ),
            ),
        )
        primary = evaluate_primary_stat_bounds(self.golden_request, changed_set)
        result = self._calculate(self.golden_request, primary)
        self.assertEqual(238800, result.value("metric.cp"))
        self.assertNotEqual(238801, result.value("metric.cp"))
        self.assertEqual(
            _f32(inputs[FinalStat.ATTACK]),
            dict(result.diagnostics.formula_inputs)[FinalStat.ATTACK],
        )

    def test_over_cap_crit_uses_effective_gameplay_values_but_raw_build_score(self) -> None:
        base_set = self.golden_primary.set_evaluation

        def with_crit(chance: int, damage: int):
            raw = dict(base_set.final_stats) | {
                FinalStat.CRITICAL_HIT_CHANCE: chance,
                FinalStat.CRITICAL_HIT_DAMAGE: damage,
            }
            unrounded = dict(base_set.diagnostics.unrounded_final_stats) | {
                FinalStat.CRITICAL_HIT_CHANCE: float(chance),
                FinalStat.CRITICAL_HIT_DAMAGE: float(damage),
            }
            changed = replace(
                base_set,
                final_stats=tuple((stat, raw[stat]) for stat in FINAL_STAT_ORDER),
                diagnostics=replace(
                    base_set.diagnostics,
                    unrounded_final_stats=tuple(
                        (stat, unrounded[stat]) for stat in FINAL_STAT_ORDER
                    ),
                ),
            )
            primary = evaluate_primary_stat_bounds(self.golden_request, changed)
            return primary, self._calculate(self.golden_request, primary)

        capped_primary, capped = with_crit(127, 510)
        edge_primary, edge = with_crit(100, 350)
        gameplay_ids = (
            "metric.damage",
            "metric.mcd",
            "metric.damage_health",
            "metric.damage_defense",
            "metric.s1",
            "metric.s2",
            "metric.s3",
        )
        self.assertEqual(
            tuple(edge.value(metric) for metric in gameplay_ids),
            tuple(capped.value(metric) for metric in gameplay_ids),
        )
        self.assertEqual(127, capped_primary.raw_value(FinalStat.CRITICAL_HIT_CHANCE))
        self.assertEqual(100, capped_primary.effective_value(FinalStat.CRITICAL_HIT_CHANCE))
        self.assertNotEqual(
            edge.value("metric.build_score"), capped.value("metric.build_score")
        )
        self.assertIs(capped.diagnostics.primary_bounds, capped_primary)
        self.assertIs(edge.diagnostics.primary_bounds, edge_primary)

    def test_damage_set_multipliers_cover_rage_pen_torrent_fervor_and_unrelated(self) -> None:
        cases = (
            ((GearSet.IMMUNITY,) * 6, 0, 0, 0, 0, 1.0),
            ((GearSet.RAGE,) * 4 + (GearSet.IMMUNITY,) * 2, 1, 0, 0, 0, 1.3),
            ((GearSet.TORRENT,) * 2 + (GearSet.IMMUNITY,) * 4, 0, 0, 1, 0, 1.1),
            ((GearSet.TORRENT,) * 4 + (GearSet.IMMUNITY,) * 2, 0, 0, 2, 0, 1.2),
            ((GearSet.TORRENT,) * 6, 0, 0, 3, 0, 1.3),
            ((GearSet.FERVOR,) * 2 + (GearSet.IMMUNITY,) * 4, 0, 0, 0, 1, 1.2),
            ((GearSet.PENETRATION,) * 2 + (GearSet.IMMUNITY,) * 4, 0, 1, 0, 0, 1.0),
        )
        for sets, rage, penetration, torrent, fervor, percent in cases:
            with self.subTest(sets=sets):
                request = self._request()
                result = self._calculate(request, self._primary(request, _zero_items(sets)))
                evidence = result.diagnostics.damage_sets
                self.assertEqual((rage, penetration, torrent, fervor), (
                    evidence.rage_groups,
                    evidence.penetration_groups,
                    evidence.torrent_groups,
                    evidence.fervor_groups,
                ))
                self.assertAlmostEqual(_f32(percent), evidence.percent_damage_multiplier, places=6)
                if penetration:
                    self.assertAlmostEqual(1.1428581476211548, evidence.penetration_set_multiplier)
                else:
                    self.assertEqual(1.0, evidence.penetration_set_multiplier)

    def test_penetration_target_defense_zero_ordinary_and_edge(self) -> None:
        sets = (GearSet.PENETRATION,) * 2 + (GearSet.IMMUNITY,) * 4
        for defense, expected in (
            (0, 1.0),
            (1500, 1.1428581476211548),
            (10000, 1.170455813407898),
        ):
            with self.subTest(defense=defense):
                request = self._request(target_defense=defense)
                result = self._calculate(request, self._primary(request, _zero_items(sets)))
                self.assertAlmostEqual(
                    expected,
                    result.diagnostics.damage_sets.penetration_set_multiplier,
                    places=6,
                )

    def test_skills_cover_health_defense_speed_extra_critical_and_support_sources(self) -> None:
        cases = (
            ("ae-KARINA", {"metric.s1": 1946, "metric.s3": 2591}),
            ("Architect Laika", {"metric.s1": 1557, "metric.s3": 4079}),
            ("Briar Witch Iseria", {"metric.s1": 1672, "metric.s3": 1772}),
            ("Karin", {"metric.s3": 2606}),
        )
        for hero_name, expected in cases:
            with self.subTest(hero=hero_name):
                hero_id = self._hero_id(hero_name)
                contexts = tuple(SkillContext(skill, 1500) for skill in SkillSlot)
                request = self._request(hero_id=hero_id, contexts=contexts)
                result = self._calculate(request, self.golden_primary)
                self.assertEqual(
                    expected,
                    {metric: result.value(metric) for metric in expected},
                )

        achates_id = self._hero_id("Achates")
        records = self.skills.skills_for(achates_id)
        option_ids = {
            record.skill: record.options[0].option_id
            for record in records
            if record.options
        }
        contexts = tuple(
            SkillContext(skill, 1500, source_option_id=option_ids.get(skill))
            for skill in SkillSlot
        )
        request = self._request(
            hero_id=achates_id,
            contexts=contexts,
            modifiers=HeroModifiers(skill_options=tuple(option_ids.values())),
        )
        support = self._calculate(request, self.golden_primary)
        self.assertEqual(1423, support.value("metric.s2"))
        self.assertEqual(2847, support.value("metric.s3"))
        self.assertEqual(
            (SkillMetricKind.SUPPORT, SkillMetricKind.SUPPORT),
            tuple(item.kind for item in support.diagnostics.skills[1:]),
        )
        self.assertTrue(all(item.hit_type is None for item in support.diagnostics.skills[1:]))

    def test_skill_hit_types_target_counts_defense_penetration_and_overrides(self) -> None:
        diagnostics = self.golden_result.diagnostics.skills
        self.assertEqual(
            (SkillHitType.CRITICAL, SkillHitType.NORMAL, SkillHitType.MISS),
            tuple(item.hit_type for item in diagnostics),
        )
        self.assertEqual((1500, 1200, 0), tuple(item.target_defense for item in diagnostics))
        self.assertEqual((1, 1, 3), tuple(item.target_count for item in diagnostics))
        self.assertEqual((True, True, False), tuple(item.penetration_set_applied for item in diagnostics))

        amiki_id = self._hero_id("Amiki")
        contexts = (
            SkillContext(
                SkillSlot.S1,
                0,
                hit_type=SkillHitType.NORMAL,
                target_count_override=1,
                penetration_override=1,
            ),
            SkillContext(SkillSlot.S2, 1500),
            SkillContext(
                SkillSlot.S3,
                1500,
                hit_type=SkillHitType.MISS,
                target_count_override=2,
                penetration_override=0.7,
            ),
        )
        request = self._request(hero_id=amiki_id, contexts=contexts)
        result = self._calculate(request, self.golden_primary)
        self.assertGreater(result.value("metric.s1"), 0)
        self.assertEqual(0, result.value("metric.s2"))
        self.assertGreater(result.value("metric.s3"), 0)
        self.assertEqual(
            (SkillMetricKind.DAMAGE, SkillMetricKind.UNAVAILABLE, SkillMetricKind.DAMAGE),
            tuple(item.kind for item in result.diagnostics.skills),
        )
        self.assertEqual((1.0, None, 0.7), tuple(item.penetration for item in result.diagnostics.skills))
        self.assertEqual((True, False, False), tuple(item.penetration_set_applied for item in result.diagnostics.skills))

    def test_gear_score_selects_current_or_reforged_substats_and_excludes_mains(self) -> None:
        current_items = _golden_items()
        reforged_items = []
        for index, item in enumerate(current_items):
            totals = dict(item.current_totals)
            if index == 0:
                totals[ItemStatType.SPEED] += 4
            reforged_items.append(
                replace(
                    item,
                    reforged_totals=totals,
                    reforged_evidence=ItemProjectionEvidence.FRIBBELS_VALID,
                )
            )
        request = self._request(projection_mode=ItemProjectionMode.REFORGED)
        result = self._calculate(request, self._primary(request, tuple(reforged_items)))
        self.assertEqual(232, result.value("metric.gear_score"))
        self.assertEqual(48, result.diagnostics.gear_scores[0].score)

        missing_main = replace(current_items[0], main_stat=None, current_main_value=None)
        items = (missing_main,) + current_items[1:]
        with self.assertRaisesRegex(DerivedMetricError, "main-stat-evidence-required"):
            self._calculate(self.golden_request, self._primary(self.golden_request, items))

    def test_all_derived_ranges_are_inclusive_blank_aware_and_canonical(self) -> None:
        values = dict(self.golden_result.metrics.derived_metrics)
        for metric_id in DERIVED_METRIC_IDS:
            value = values[metric_id]
            with self.subTest(metric=metric_id, case="exact"):
                request = replace(
                    self.golden_request,
                    derived_metric_ranges={metric_id: StatRange(value, value)},
                )
                result = self._calculate(request, self.golden_primary)
                evaluation = result.evaluation_for(metric_id)
                self.assertTrue(result.passes)
                self.assertIs(evaluation.status, DerivedMetricBoundStatus.PASSED)
            with self.subTest(metric=metric_id, case="minimum"):
                request = replace(
                    self.golden_request,
                    derived_metric_ranges={metric_id: StatRange(minimum=value + 1)},
                )
                failure = self._calculate(request, self.golden_primary).failures[0]
                self.assertIs(failure.status, DerivedMetricBoundStatus.BELOW_MINIMUM)
                self.assertIs(failure.failure_side, DerivedMetricBoundSide.MINIMUM)
            with self.subTest(metric=metric_id, case="maximum"):
                request = replace(
                    self.golden_request,
                    derived_metric_ranges={metric_id: StatRange(maximum=value - 1)},
                )
                failure = self._calculate(request, self.golden_primary).failures[0]
                self.assertIs(failure.status, DerivedMetricBoundStatus.ABOVE_MAXIMUM)
                self.assertIs(failure.failure_side, DerivedMetricBoundSide.MAXIMUM)

        request = replace(
            self.golden_request,
            derived_metric_ranges={
                "metric.cp": StatRange(),
                "metric.s1": StatRange(minimum=0),
                "metric.s2": StatRange(maximum=0),
            },
        )
        result = self._calculate(request, self.golden_primary)
        blank = result.evaluation_for("metric.cp")
        self.assertTrue(blank.range_supplied)
        self.assertFalse(blank.constrained)
        self.assertIs(blank.status, DerivedMetricBoundStatus.UNRESTRICTED)
        self.assertEqual(0, result.evaluation_for("metric.s1").requested_range.minimum)
        self.assertEqual(0, result.evaluation_for("metric.s2").requested_range.maximum)
        self.assertEqual(DERIVED_METRIC_IDS, tuple(item.metric_id for item in result.evaluations))

    def test_unknown_metric_context_mismatch_and_primary_failure_are_deterministic(self) -> None:
        unknown = replace(
            self.golden_request,
            derived_metric_ranges={"metric.unknown": StatRange(minimum=1)},
        )
        with self.assertRaises(DerivedMetricError) as raised:
            self._calculate(unknown, self.golden_primary)
        self.assertEqual("unknown-derived-metric", raised.exception.code)

        mismatched = replace(
            self.golden_request,
            skill_contexts=tuple(SkillContext(skill, 999) for skill in SkillSlot),
        )
        with self.assertRaises(DerivedMetricError) as raised:
            calculate_derived_metrics(
                mismatched,
                self.golden_primary,
                self.skills.select(
                    self.golden_request.hero_id, self.golden_request.skill_contexts
                ),
            )
        self.assertEqual("skill-context-mismatch", raised.exception.code)

        failing_request = replace(
            self.golden_request,
            stat_ranges={FinalStat.ATTACK: StatRange(minimum=999999)},
        )
        failing_primary = self._primary(failing_request, _golden_items())
        self.assertFalse(failing_primary.passes)
        calculated = self._calculate(failing_request, failing_primary)
        self.assertEqual(49150, calculated.value("metric.cp"))

    def test_results_and_nested_diagnostics_are_frozen_and_no_later_scores_exist(self) -> None:
        self.assertIsInstance(hash(self.golden_result), int)
        self.assertIsInstance(hash(self.golden_result.diagnostics), int)
        with self.assertRaises(FrozenInstanceError):
            self.golden_result.evaluations = ()
        with self.assertRaises(FrozenInstanceError):
            self.golden_result.diagnostics.damage_sets.rage_groups = 0
        self.assertFalse(hasattr(self.golden_result, "constraint_distance"))
        self.assertFalse(hasattr(self.golden_result, "set_pattern_match"))
        self.assertEqual(0, self.golden_result.metrics.priority_score)


if __name__ == "__main__":
    unittest.main()
