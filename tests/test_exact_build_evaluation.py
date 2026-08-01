from __future__ import annotations

import ast
import inspect
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
    GEAR_SLOT_ORDER,
    SET_CATALOG,
    EquipmentEligibilityReason,
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
    ProjectedGearItem,
    aggregate_with_set_bonuses,
    calculate_derived_metrics,
    calculate_item_final_contributions,
    calculate_item_gear_score,
    evaluate_primary_stat_bounds,
    score_final_build_priority,
)
from src.optimizer.search import (
    SEARCH_PREPARATION_EXCLUSION_ORDER,
    CartesianBatch,
    CartesianSearchSpace,
    ExactBuildBatchResult,
    ExactBuildEvaluationError,
    SearchItemPreparationDiagnostic,
    SearchPreparationDiagnostics,
    SearchReadySlotArrays,
    SearchSlotArray,
    SearchSlotPreparationDiagnostic,
    compile_exact_build_context,
    compile_set_pattern,
    create_cartesian_search_space,
    evaluate_exact_build_batch,
    iter_cartesian_batches,
)
from src.optimizer.search import exact_evaluation as exact_module


GOLDEN_SETS = (
    GearSet.RAGE,
    GearSet.RAGE,
    GearSet.RAGE,
    GearSet.RAGE,
    GearSet.PENETRATION,
    GearSet.PENETRATION,
)


def _items(sets: tuple[GearSet, ...] = GOLDEN_SETS) -> tuple[ProjectedGearItem, ...]:
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
                item_id=f"exact.{slot.value}",
                slot=slot,
                gear_set=gear_set,
                main_stat=main_stat,
                main_stat_value=main_value,
                substats=substats,
            )
        )
        for (slot, main_stat, main_value, substats), gear_set in zip(
            definitions,
            sets,
            strict=True,
        )
    )


def _arrays(request, profile, candidates) -> tuple[SearchReadySlotArrays, dict[int, ProjectedGearItem]]:
    mode = request.item_projection_mode
    assert mode is not None
    base = dict(profile.profile.final_stats)
    by_slot = {slot: [] for slot in GEAR_SLOT_ORDER}
    for candidate in candidates:
        by_slot[candidate.slot].append(candidate)

    dense_id = 0
    arrays: list[SearchSlotArray] = []
    decisions: list[SearchItemPreparationDiagnostic] = []
    reverse: list[tuple[int, str]] = []
    projected_by_dense: dict[int, ProjectedGearItem] = {}
    slot_diagnostics: list[SearchSlotPreparationDiagnostic] = []
    for slot in GEAR_SLOT_ORDER:
        ordered = tuple(sorted(by_slot[slot], key=lambda item: item.item_id))
        dense_ids: list[int] = []
        set_indices: list[int] = []
        contributions: list[tuple[float, ...]] = []
        gear_scores: list[int] = []
        for candidate in ordered:
            item = replace(candidate, dense_id=dense_id)
            totals = item.totals_for(mode)
            dense_ids.append(dense_id)
            set_indices.append(SET_CATALOG[item.gear_set].fribbels_index)
            contributions.append(
                tuple(value for _, value in calculate_item_final_contributions(totals, base))
            )
            assert item.main_stat is not None
            gear_scores.append(
                calculate_item_gear_score(
                    item.item_id,
                    totals,
                    item.main_stat,
                    item.main_value_for(mode),
                ).score
            )
            decisions.append(
                SearchItemPreparationDiagnostic(
                    stable_item_id=item.item_id,
                    slot=slot,
                    included=True,
                    eligibility_reason=EquipmentEligibilityReason.UNEQUIPPED,
                    exclusion_reason=None,
                    projection_evidence=item.evidence_for(mode),
                )
            )
            reverse.append((dense_id, item.item_id))
            projected_by_dense[dense_id] = item
            dense_id += 1
        arrays.append(
            SearchSlotArray(
                slot=slot,
                dense_ids=tuple(dense_ids),
                set_indices=tuple(set_indices),
                final_stat_contributions=tuple(contributions),
                gear_scores=tuple(gear_scores),
            )
        )
        slot_diagnostics.append(
            SearchSlotPreparationDiagnostic(
                slot=slot,
                input_count=len(ordered),
                included_count=len(ordered),
                exclusion_counts=tuple(
                    (reason, 0) for reason in SEARCH_PREPARATION_EXCLUSION_ORDER
                ),
            )
        )
    diagnostics = SearchPreparationDiagnostics(
        projection_mode=mode,
        decisions=tuple(decisions),
        slots=tuple(slot_diagnostics),
        unmatched_excluded_item_ids=(),
    )
    return (
        SearchReadySlotArrays(
            slots=tuple(arrays),
            dense_id_to_stable_id=tuple(reverse),
            diagnostics=diagnostics,
            request_id=request.request_id,
            hero_id=request.hero_id,
            base_profile_id=request.base_profile_id,
            base_stats=tuple(value for _, value in profile.profile.final_stats),
        ),
        projected_by_dense,
    )


class ExactBuildEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.skill_repository = load_bundled_skill_context_repository()
        cls.contexts = (
            SkillContext(SkillSlot.S1, 1500, hit_type=SkillHitType.CRITICAL),
            SkillContext(SkillSlot.S2, 1200, hit_type=SkillHitType.NORMAL),
            SkillContext(SkillSlot.S3, 0, hit_type=SkillHitType.MISS),
        )

    def _request(self, sets, **overrides) -> OptimizationRequest:
        values = {
            "request_id": "request.exact-evaluation",
            "hero_id": self.profile.hero_id,
            "base_profile_id": self.profile.profile_id,
            "modifiers": HeroModifiers(),
            "set_pattern": SetPattern(sets),
            "stat_priorities": (
                (FinalStat.ATTACK, 3),
                (FinalStat.HEALTH, 1),
                (FinalStat.CRITICAL_HIT_DAMAGE, 2),
                (FinalStat.EFFECT_RESISTANCE, -1),
            ),
            "target_defense": 1500,
            "skill_contexts": self.contexts,
            "item_projection_mode": ItemProjectionMode.CURRENT,
        }
        values.update(overrides)
        return OptimizationRequest(**values)

    def _evaluate(self, request, candidates):
        arrays, projected = _arrays(request, self.profile, candidates)
        pattern = compile_set_pattern(request.set_pattern)
        skills = self.skill_repository.select(request.hero_id, request.skill_contexts)
        context = compile_exact_build_context(
            request,
            self.profile,
            ArtifactSelection(),
            skills,
            pattern,
        )
        space = create_cartesian_search_space(arrays)
        batch = next(iter_cartesian_batches(space, space.total_permutations))
        return evaluate_exact_build_batch(context, arrays, batch), context, arrays, projected, batch

    def _assert_p03_oracle(self, request, row, projected_by_dense) -> None:
        items = tuple(projected_by_dense[dense_id] for dense_id in row.dense_ids)
        primary = evaluate_primary_stat_bounds(
            request,
            aggregate_with_set_bonuses(request, self.profile, ArtifactSelection(), items),
        )
        skills = self.skill_repository.select(request.hero_id, request.skill_contexts)
        scored = score_final_build_priority(
            request,
            calculate_derived_metrics(request, primary, skills),
        )
        self.assertEqual(
            tuple(value for _, value in primary.set_evaluation.diagnostics.unrounded_final_stats),
            row.unrounded_final_stats,
        )
        self.assertEqual(tuple(value for _, value in primary.raw_final_stats), row.raw_final_stats)
        self.assertEqual(
            tuple(value for _, value in primary.effective_final_stats),
            row.effective_final_stats,
        )
        self.assertEqual(
            tuple(dict(scored.metrics.derived_metrics)[metric] for metric in DERIVED_METRIC_IDS),
            row.derived_metrics,
        )
        self.assertEqual(scored.priority_score, row.priority_score)

    def test_full_numeric_row_matches_p03_for_4_plus_2_and_repeated_triple(self) -> None:
        cases = (
            ((GearSet.RAGE, GearSet.PENETRATION), GOLDEN_SETS),
            (
                (GearSet.HEALTH, GearSet.HEALTH, GearSet.DEFENSE),
                (
                    GearSet.HEALTH,
                    GearSet.HEALTH,
                    GearSet.HEALTH,
                    GearSet.HEALTH,
                    GearSet.DEFENSE,
                    GearSet.DEFENSE,
                ),
            ),
        )
        for requested, actual in cases:
            with self.subTest(requested=requested):
                request = self._request(requested)
                result, _, _, projected, _ = self._evaluate(request, _items(actual))
                self.assertEqual((1, 1, 0, 1), (
                    result.evaluated_count,
                    result.exact_set_count,
                    result.hard_bound_rejected_count,
                    result.emitted_count,
                ))
                self._assert_p03_oracle(request, result.rows[0], projected)

    def test_damage_and_nonnumeric_set_families_match_p03(self) -> None:
        cases = (
            ((GearSet.RAGE, GearSet.PENETRATION), GOLDEN_SETS),
            (
                (GearSet.SPEED, GearSet.TORRENT),
                (GearSet.SPEED,) * 4 + (GearSet.TORRENT,) * 2,
            ),
            (
                (GearSet.SPEED, GearSet.FERVOR),
                (GearSet.SPEED,) * 4 + (GearSet.FERVOR,) * 2,
            ),
            (
                (GearSet.LIFESTEAL, GearSet.IMMUNITY),
                (GearSet.LIFESTEAL,) * 4 + (GearSet.IMMUNITY,) * 2,
            ),
        )
        for requested, actual in cases:
            with self.subTest(requested=requested):
                request = self._request(requested)
                result, _, _, projected, _ = self._evaluate(request, _items(actual))
                self.assertEqual(1, result.emitted_count)
                self._assert_p03_oracle(request, result.rows[0], projected)

    def test_only_the_exact_set_vector_is_numerically_evaluated(self) -> None:
        request = self._request((GearSet.RAGE, GearSet.PENETRATION))
        candidates = list(_items())
        candidates.append(
            replace(
                candidates[0],
                item_id="wrong.weapon",
                gear_set=GearSet.ATTACK,
                dense_id=None,
            )
        )
        result, _, _, projected, _ = self._evaluate(request, candidates)
        self.assertEqual(2, result.evaluated_count)
        self.assertEqual(1, result.exact_set_count)
        self.assertEqual(0, result.hard_bound_rejected_count)
        self.assertEqual(1, result.emitted_count)
        self._assert_p03_oracle(request, result.rows[0], projected)

    def test_blank_primary_and_derived_bounds_pass_and_supplied_bounds_reject(self) -> None:
        base_request = self._request((GearSet.RAGE, GearSet.PENETRATION))
        base_result, _, _, _, _ = self._evaluate(base_request, _items())
        baseline = base_result.rows[0]
        attack = baseline.effective_final_stats[FINAL_STAT_ORDER.index(FinalStat.ATTACK)]
        ehp = baseline.derived_metrics[DERIVED_METRIC_IDS.index("metric.ehp")]
        passing = replace(
            base_request,
            stat_ranges=((FinalStat.ATTACK, StatRange(attack, attack)),),
            derived_metric_ranges=(("metric.ehp", StatRange(ehp, ehp)),),
        )
        passing_result, _, _, projected, _ = self._evaluate(passing, _items())
        self.assertEqual(1, passing_result.emitted_count)
        self._assert_p03_oracle(passing, passing_result.rows[0], projected)

        for failing in (
            replace(
                base_request,
                stat_ranges=((FinalStat.ATTACK, StatRange(maximum=attack - 1)),),
            ),
            replace(
                base_request,
                derived_metric_ranges=(("metric.ehp", StatRange(minimum=ehp + 1)),),
            ),
        ):
            with self.subTest(failing=failing):
                result, _, _, _, _ = self._evaluate(failing, _items())
                self.assertEqual((1, 1, 0), (
                    result.exact_set_count,
                    result.hard_bound_rejected_count,
                    result.emitted_count,
                ))

    def test_raw_stats_preserve_over_cap_values_while_effective_stats_are_capped(self) -> None:
        candidates = list(_items())
        candidates[3] = ProjectedGearItem.from_gear_item(
            GearItem(
                item_id="exact.necklace",
                slot=GearSlot.NECKLACE,
                gear_set=GearSet.RAGE,
                main_stat=ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
                main_stat_value=400,
                substats=((ItemStatType.CRITICAL_HIT_CHANCE_PERCENT, 120),),
            )
        )
        request = self._request((GearSet.RAGE, GearSet.PENETRATION))
        result, _, _, projected, _ = self._evaluate(request, candidates)
        row = result.rows[0]
        crit = FINAL_STAT_ORDER.index(FinalStat.CRITICAL_HIT_CHANCE)
        damage = FINAL_STAT_ORDER.index(FinalStat.CRITICAL_HIT_DAMAGE)
        self.assertGreater(row.raw_final_stats[crit], 100)
        self.assertGreater(row.raw_final_stats[damage], 350)
        self.assertEqual((100, 350), (row.effective_final_stats[crit], row.effective_final_stats[damage]))
        self._assert_p03_oracle(request, row, projected)

    def test_context_rejects_identity_projection_base_and_radix_mismatches(self) -> None:
        request = self._request((GearSet.RAGE, GearSet.PENETRATION))
        _, context, arrays, _, batch = self._evaluate(request, _items())
        wrong_space = CartesianSearchSpace((2, 1, 1, 1, 1, 1), 2)
        wrong_batch = CartesianBatch(wrong_space, 0, 1, ((0, 0, 0, 0, 0, 0),))
        cases = (
            (replace(arrays, request_id="request.other"), batch, "search-context-mismatch"),
            (
                replace(arrays, base_stats=(arrays.base_stats[0] + 1,) + arrays.base_stats[1:]),
                batch,
                "base-stat-context-mismatch",
            ),
            (
                replace(
                    arrays,
                    diagnostics=replace(
                        arrays.diagnostics,
                        projection_mode=ItemProjectionMode.REFORGED,
                    ),
                ),
                batch,
                "projection-context-mismatch",
            ),
            (arrays, wrong_batch, "cartesian-radix-mismatch"),
        )
        for supplied_arrays, supplied_batch, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(ExactBuildEvaluationError) as caught:
                    evaluate_exact_build_batch(context, supplied_arrays, supplied_batch)
                self.assertEqual(code, caught.exception.code)

    def test_batch_splits_flat_indices_dense_ids_reordered_inputs_and_repetition(self) -> None:
        request = self._request((GearSet.RAGE, GearSet.PENETRATION))
        candidates = list(_items())
        candidates.append(
            replace(
                candidates[0],
                item_id="zz.exact.weapon",
                dense_id=None,
            )
        )
        arrays, projected = _arrays(request, self.profile, candidates)
        reordered, _ = _arrays(request, self.profile, reversed(candidates))
        self.assertEqual(arrays, reordered)
        skills = self.skill_repository.select(request.hero_id, request.skill_contexts)
        context = compile_exact_build_context(
            request,
            self.profile,
            ArtifactSelection(),
            skills,
            compile_set_pattern(request.set_pattern),
        )
        batches = tuple(iter_cartesian_batches(create_cartesian_search_space(arrays), 1))
        first = evaluate_exact_build_batch(context, arrays, batches[0])
        second = evaluate_exact_build_batch(context, arrays, batches[1])
        self.assertEqual(first, evaluate_exact_build_batch(context, arrays, batches[0]))
        self.assertEqual((0, 1), (first.rows[0].flat_index, second.rows[0].flat_index))
        self.assertEqual((0, 1), (first.rows[0].dense_ids[0], second.rows[0].dense_ids[0]))
        self.assertEqual(first.rows[0].dense_ids[1:], second.rows[0].dense_ids[1:])
        self._assert_p03_oracle(request, first.rows[0], projected)
        self._assert_p03_oracle(request, second.rows[0], projected)

    def test_fractional_six_slot_addition_set_insertion_and_final_multiplier_match_p03(self) -> None:
        profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.aki"
        )
        contexts = tuple(SkillContext(skill, 1337) for skill in SkillSlot)
        request = OptimizationRequest(
            request_id="request.exact-binary32",
            hero_id=profile.hero_id,
            base_profile_id=profile.profile_id,
            modifiers=HeroModifiers(),
            set_pattern=SetPattern((GearSet.RAGE, GearSet.PENETRATION)),
            stat_priorities=((FinalStat.ATTACK, 3), (FinalStat.SPEED, -1)),
            target_defense=1337,
            skill_contexts=contexts,
            item_projection_mode=ItemProjectionMode.CURRENT,
        )
        fractional = []
        for index, item in enumerate(_items()):
            totals = dict(item.current_totals)
            totals[ItemStatType.FLAT_ATTACK] += 0.17 * (index + 1)
            totals[ItemStatType.ATTACK_PERCENT] += 0.13 * (index + 1)
            totals[ItemStatType.SPEED] += 0.11 * (index + 1)
            fractional.append(replace(item, current_totals=totals))
        arrays, projected = _arrays(request, profile, fractional)
        skills = self.skill_repository.select(request.hero_id, request.skill_contexts)
        context = compile_exact_build_context(
            request,
            profile,
            ArtifactSelection(),
            skills,
            compile_set_pattern(request.set_pattern),
        )
        self.assertEqual(1.5, context.final_stat_multipliers[0])
        batch = next(iter_cartesian_batches(create_cartesian_search_space(arrays), 1))
        result = evaluate_exact_build_batch(context, arrays, batch)
        row = result.rows[0]
        items = tuple(projected[dense_id] for dense_id in row.dense_ids)
        primary = evaluate_primary_stat_bounds(
            request,
            aggregate_with_set_bonuses(request, profile, ArtifactSelection(), items),
        )
        scored = score_final_build_priority(
            request,
            calculate_derived_metrics(request, primary, skills),
        )
        self.assertEqual(
            tuple(value for _, value in primary.set_evaluation.diagnostics.unrounded_final_stats),
            row.unrounded_final_stats,
        )
        self.assertEqual(tuple(value for _, value in primary.raw_final_stats), row.raw_final_stats)
        self.assertEqual(tuple(value for _, value in primary.effective_final_stats), row.effective_final_stats)
        self.assertEqual(tuple(value for _, value in scored.metrics.derived_metrics), row.derived_metrics)
        self.assertEqual(scored.priority_score, row.priority_score)

    def test_numeric_records_validate_shape_finiteness_counts_and_immutability(self) -> None:
        request = self._request((GearSet.RAGE, GearSet.PENETRATION))
        result, context, _, _, _ = self._evaluate(request, _items())
        row = result.rows[0]
        with self.assertRaises(ExactBuildEvaluationError):
            replace(context, base_stats=context.base_stats[:-1] + (math.nan,))
        with self.assertRaises(ExactBuildEvaluationError):
            replace(context, required_piece_counts=(0,) * 24)
        with self.assertRaises(ExactBuildEvaluationError):
            replace(
                context,
                numeric_set_contributions=(
                    ((1.0,) + context.numeric_set_contributions[0][1:]),
                    *context.numeric_set_contributions[1:],
                ),
            )
        with self.assertRaises(ExactBuildEvaluationError):
            replace(row, raw_final_stats=(row.raw_final_stats[0] + 1,) + row.raw_final_stats[1:])
        with self.assertRaises(ExactBuildEvaluationError):
            ExactBuildBatchResult(
                start_index=0,
                stop_index=1,
                evaluated_count=1,
                exact_set_count=1,
                hard_bound_rejected_count=1,
                emitted_count=1,
                rows=(row,),
            )
        self.assertIsInstance(hash(context), int)
        self.assertIsInstance(hash(row), int)
        self.assertEqual(result, replace(result))
        with self.assertRaises(FrozenInstanceError):
            row.priority_score = 1  # type: ignore[misc]
        self.assertTrue(
            all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in (
                    row.flat_index,
                    *row.dense_ids,
                    *row.unrounded_final_stats,
                    *row.raw_final_stats,
                    *row.effective_final_stats,
                    *row.derived_metrics,
                    row.priority_score,
                )
            )
        )

    def test_module_is_a_pure_exact_numeric_layer_without_near_set_or_io_dependencies(self) -> None:
        source = inspect.getsource(exact_module)
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertNotIn("near_set", source.casefold())
        self.assertNotIn("replacement", source.casefold())
        self.assertFalse(
            any(
                forbidden in imported.casefold()
                for imported in imports
                for forbidden in ("constraint_distance", "cupy", "sqlite", "desktop")
            )
        )


if __name__ == "__main__":
    unittest.main()
