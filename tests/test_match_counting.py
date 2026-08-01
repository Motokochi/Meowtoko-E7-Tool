from __future__ import annotations

import ast
import inspect
import math
import unittest
from dataclasses import FrozenInstanceError, replace
from itertools import permutations

from src.optimizer.data import (
    ArtifactSelection,
    load_bundled_character_profile_selector,
    load_bundled_skill_context_repository,
)
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    MAX_RESULT_CAP,
    RESULT_CATEGORY_ORDER,
    ExecutionPreference,
    FinalStat,
    GearSet,
    HeroModifiers,
    ItemProjectionMode,
    OptimizationRequest,
    ResultCategory,
    SetPattern,
    SkillContext,
    SkillSlot,
    StatRange,
)
from src.optimizer.engine import DERIVED_METRIC_IDS
from src.optimizer.search import (
    CombinedMatchCounter,
    ExactBuildBatchResult,
    ExactBuildRow,
    ExactMatchCollection,
    MatchCountingContext,
    MatchCountingError,
    MatchCountingResult,
    MatchEvent,
    OverflowGuidance,
    collect_exact_build_matches,
    compile_exact_build_context,
    compile_match_counting_context,
    compile_set_pattern,
    count_match_events,
)
from src.optimizer.search import match_counting as match_counting_module


def _row(flat_index: int, *, priority: float = 0.5) -> ExactBuildRow:
    unrounded = (1500.25, 9000.5, 1000.75, 200.0, 80.0, 300.0, 50.0, 60.0)
    raw = tuple(math.trunc(value) for value in unrounded)
    return ExactBuildRow(
        flat_index=flat_index,
        dense_ids=(0, 1, 2, 3, 4, 5),
        unrounded_final_stats=unrounded,
        raw_final_stats=raw,
        effective_final_stats=raw,
        derived_metrics=tuple(range(1, len(DERIVED_METRIC_IDS) + 1)),
        priority_score=priority,
    )


def _event(flat_index: int, category: ResultCategory = ResultCategory.EXACT) -> MatchEvent:
    return MatchEvent(category=category, flat_index=flat_index, dense_ids=(0, 1, 2, 3, 4, 5))


def _batch(
    start: int,
    stop: int,
    *,
    exact: int,
    rejected: int,
    rows: tuple[ExactBuildRow, ...] = (),
) -> ExactBuildBatchResult:
    return ExactBuildBatchResult(
        start_index=start,
        stop_index=stop,
        evaluated_count=stop - start,
        exact_set_count=exact,
        hard_bound_rejected_count=rejected,
        emitted_count=len(rows),
        rows=rows,
    )


class MatchCountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.skills = load_bundled_skill_context_repository()
        cls.skill_contexts = tuple(SkillContext(skill, 1500) for skill in SkillSlot)

    def _request(self, *, cap: int = MAX_RESULT_CAP, stat_ranges=(), derived_ranges=()):
        return OptimizationRequest(
            request_id="request.match-counting",
            hero_id=self.profile.hero_id,
            base_profile_id=self.profile.profile_id,
            modifiers=HeroModifiers(),
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
            stat_ranges=stat_ranges,
            derived_metric_ranges=derived_ranges,
            target_defense=1500,
            skill_contexts=self.skill_contexts,
            result_cap=cap,
            item_projection_mode=ItemProjectionMode.CURRENT,
        )

    def _evaluation_context(self, request):
        return compile_exact_build_context(
            request,
            self.profile,
            ArtifactSelection(),
            self.skills.select(request.hero_id, request.skill_contexts),
            compile_set_pattern(request.set_pattern),
        )

    def test_low_caps_prove_exact_boundary_empty_success_and_cap_plus_one_overflow(self) -> None:
        for cap in (1, 2, 3):
            context = compile_match_counting_context(self._request(cap=cap))
            with self.subTest(cap=cap, boundary="empty"):
                empty = count_match_events(context, ())
                self.assertTrue(empty.completed)
                self.assertEqual(0, empty.detected_count)
            with self.subTest(cap=cap, boundary="exact"):
                complete = count_match_events(context, tuple(_event(index) for index in range(cap)))
                self.assertTrue(complete.completed)
                self.assertEqual(cap, complete.detected_count)
                self.assertEqual(cap, complete.retained_count)
                self.assertIsNone(complete.guidance)
            with self.subTest(cap=cap, boundary="overflow"):
                overflow = count_match_events(
                    context,
                    tuple(_event(index) for index in range(cap + 1)),
                )
                self.assertTrue(overflow.overflowed)
                self.assertEqual(cap + 1, overflow.detected_count)
                self.assertEqual(0, overflow.retained_count)
                self.assertIsNotNone(overflow.guidance)
                summary = overflow.to_search_summary(
                    evaluated_permutations=cap + 1,
                    duration_seconds=0.25,
                    execution_preference=ExecutionPreference.CPU,
                )
                self.assertTrue(summary.overflowed)
                self.assertEqual(0, summary.result_count)

    def test_all_categories_share_one_limit_in_every_category_order(self) -> None:
        context = compile_match_counting_context(self._request(cap=3))
        categories = tuple(RESULT_CATEGORY_ORDER)
        for order in permutations(categories):
            with self.subTest(order=order):
                complete = count_match_events(
                    context,
                    tuple(_event(index, category) for index, category in enumerate(order)),
                )
                self.assertEqual((1, 1, 1), complete.category_counts)
                overflow = count_match_events(
                    context,
                    tuple(
                        _event(index, category)
                        for index, category in enumerate(order + (order[0],))
                    ),
                )
                expected = [1, 1, 1]
                expected[RESULT_CATEGORY_ORDER.index(order[0])] += 1
                self.assertEqual(tuple(expected), overflow.category_counts)
                self.assertTrue(overflow.overflowed)

        production_sentinel = MatchCountingResult(
            request_id="request.production-sentinel",
            result_cap=MAX_RESULT_CAP,
            detected_count=MAX_RESULT_CAP + 1,
            category_counts=(MAX_RESULT_CAP, 1, 0),
            overflowed=True,
            guidance=context.overflow_guidance,
        )
        self.assertTrue(production_sentinel.overflowed)
        self.assertEqual(5_000_001, production_sentinel.detected_count)

    def test_event_source_is_not_consumed_after_cap_plus_one(self) -> None:
        context = compile_match_counting_context(self._request(cap=2))
        consumed: list[int] = []

        def source():
            for index in range(3):
                consumed.append(index)
                yield _event(index)
            raise AssertionError("source resumed after cap+1")

        result = count_match_events(context, source())
        self.assertTrue(result.overflowed)
        self.assertEqual([0, 1, 2], consumed)

    def test_counter_terminal_and_ordering_state_are_actionable(self) -> None:
        context = compile_match_counting_context(self._request(cap=2))
        counter = CombinedMatchCounter(context)
        self.assertTrue(counter.accept(_event(2)))
        with self.assertRaises(MatchCountingError) as order_error:
            counter.accept(_event(1))
        self.assertEqual("noncanonical-match-order", order_error.exception.code)

        result = counter.finish()
        self.assertIs(result, counter.finish())
        with self.assertRaises(MatchCountingError) as terminal_error:
            counter.accept(_event(3))
        self.assertEqual("counter-terminal", terminal_error.exception.code)

    def test_exact_batches_count_only_emitted_rows_and_preserve_order(self) -> None:
        request = self._request(cap=2)
        counting_context = compile_match_counting_context(request)
        evaluation_context = self._evaluation_context(request)
        rows = (_row(2), _row(7))
        batches = (
            _batch(0, 5, exact=2, rejected=1, rows=(rows[0],)),
            _batch(5, 9, exact=1, rejected=0, rows=(rows[1],)),
        )
        collection = collect_exact_build_matches(counting_context, evaluation_context, batches)
        self.assertTrue(collection.counting.completed)
        self.assertEqual(rows, collection.rows)
        self.assertEqual((9, 3, 1, 2), (
            collection.evaluated_permutations,
            collection.exact_set_candidates,
            collection.hard_bound_rejected_count,
            collection.counting.detected_count,
        ))
        self.assertEqual(2, collection.counting.count_for(ResultCategory.EXACT))
        self.assertEqual(0, collection.counting.count_for(ResultCategory.ONE_AWAY))
        self.assertEqual(
            collection,
            collect_exact_build_matches(counting_context, evaluation_context, batches),
        )

    def test_exact_overflow_discards_rows_and_stops_before_the_next_batch(self) -> None:
        request = self._request(cap=1)
        counting_context = compile_match_counting_context(request)
        evaluation_context = self._evaluation_context(request)
        first = _batch(0, 1, exact=1, rejected=0, rows=(_row(0),))
        second = _batch(1, 2, exact=1, rejected=0, rows=(_row(1),))
        consumed: list[int] = []

        def batches():
            consumed.append(0)
            yield first
            consumed.append(1)
            yield second
            raise AssertionError("batch source resumed after cap+1")

        collection = collect_exact_build_matches(
            counting_context,
            evaluation_context,
            batches(),
        )
        self.assertEqual([0, 1], consumed)
        self.assertTrue(collection.counting.overflowed)
        self.assertEqual(2, collection.counting.detected_count)
        self.assertEqual((), collection.rows)
        self.assertEqual(2, collection.consumed_batch_count)

    def test_guidance_distinguishes_unrestricted_blank_and_bounded_filters(self) -> None:
        blank = compile_match_counting_context(self._request(cap=1)).overflow_guidance
        self.assertEqual(tuple(stat.value for stat in FINAL_STAT_ORDER), blank.unrestricted_primary_filter_ids)
        self.assertEqual(DERIVED_METRIC_IDS, blank.unrestricted_derived_filter_ids)
        self.assertEqual((), blank.bounded_primary_filter_ids)
        self.assertEqual((), blank.bounded_derived_filter_ids)

        partial = compile_match_counting_context(
            self._request(
                cap=1,
                stat_ranges=(
                    (FinalStat.ATTACK, StatRange(minimum=1000)),
                    (FinalStat.SPEED, StatRange()),
                ),
                derived_ranges=(
                    ("metric.ehp", StatRange(maximum=100_000)),
                    ("metric.damage", StatRange()),
                ),
            )
        ).overflow_guidance
        self.assertEqual((FinalStat.ATTACK.value,), partial.bounded_primary_filter_ids)
        self.assertIn(FinalStat.SPEED.value, partial.unrestricted_primary_filter_ids)
        self.assertEqual(("metric.ehp",), partial.bounded_derived_filter_ids)
        self.assertIn("metric.damage", partial.unrestricted_derived_filter_ids)
        self.assertEqual(
            partial.unrestricted_primary_filter_ids + partial.unrestricted_derived_filter_ids,
            partial.recommended_filter_ids,
        )

        all_bounded = compile_match_counting_context(
            self._request(
                cap=1,
                stat_ranges=tuple((stat, StatRange(minimum=0)) for stat in FINAL_STAT_ORDER),
                derived_ranges=tuple(
                    (metric, StatRange(minimum=0)) for metric in DERIVED_METRIC_IDS
                ),
            )
        ).overflow_guidance
        self.assertEqual((), all_bounded.unrestricted_primary_filter_ids)
        self.assertEqual((), all_bounded.unrestricted_derived_filter_ids)
        self.assertEqual(
            all_bounded.bounded_primary_filter_ids + all_bounded.bounded_derived_filter_ids,
            all_bounded.recommended_filter_ids,
        )

    def test_invalid_context_events_results_and_collections_fail_actionably(self) -> None:
        request = self._request(cap=2)
        context = compile_match_counting_context(request)
        evaluation_context = self._evaluation_context(request)
        guidance = context.overflow_guidance

        for invalid_cap in (True, 0, MAX_RESULT_CAP + 1):
            with self.subTest(invalid_cap=invalid_cap), self.assertRaises(MatchCountingError):
                MatchCountingContext("request.test", invalid_cap, guidance)  # type: ignore[arg-type]
        with self.assertRaises(MatchCountingError):
            replace(guidance, code="overflow.other")
        with self.assertRaises(MatchCountingError):
            replace(guidance, unrestricted_primary_filter_ids=())

        invalid_events = (
            dict(category="result.unknown", flat_index=0, dense_ids=(0, 1, 2, 3, 4, 5)),
            dict(category=ResultCategory.EXACT, flat_index=math.inf, dense_ids=(0, 1, 2, 3, 4, 5)),
            dict(category=ResultCategory.EXACT, flat_index=0, dense_ids=(0, 1, 2, 3, 4, 4)),
            dict(category=ResultCategory.EXACT, flat_index=0, dense_ids=(0, 1, 2, 3, 4, True)),
        )
        for values in invalid_events:
            with self.subTest(values=values), self.assertRaises(MatchCountingError):
                MatchEvent(**values)  # type: ignore[arg-type]
        with self.assertRaises(MatchCountingError):
            count_match_events(context, (_event(0), object()))  # type: ignore[arg-type]
        with self.assertRaises(MatchCountingError):
            count_match_events(context, "events")  # type: ignore[arg-type]

        with self.assertRaises(MatchCountingError):
            MatchCountingResult("request.test", 2, 1, (0, 0, 0), False, None)
        with self.assertRaises(MatchCountingError):
            MatchCountingResult("request.test", 2, 3, (3, 0, 0), False, None)
        overflow = MatchCountingResult("request.test", 2, 3, (3, 0, 0), True, guidance)
        with self.assertRaises(MatchCountingError):
            replace(overflow, guidance=None)

        with self.assertRaises(MatchCountingError):
            ExactMatchCollection(overflow, 3, 3, 0, 1, (_row(0),))
        complete_empty = count_match_events(context, ())
        with self.assertRaises(MatchCountingError):
            ExactMatchCollection(complete_empty, 1, 0, 0, 0, ())
        other_context = replace(context, request_id="request.other")
        with self.assertRaises(MatchCountingError) as mismatch:
            collect_exact_build_matches(other_context, evaluation_context, ())
        self.assertEqual("request-context-mismatch", mismatch.exception.code)
        overlapping = (
            _batch(0, 2, exact=0, rejected=0),
            _batch(1, 3, exact=0, rejected=0),
        )
        with self.assertRaises(MatchCountingError) as overlap:
            collect_exact_build_matches(context, evaluation_context, overlapping)
        self.assertEqual("overlapping-exact-batches", overlap.exception.code)

    def test_records_are_immutable_hashable_and_module_has_no_later_phase_dependencies(self) -> None:
        context = compile_match_counting_context(self._request(cap=1))
        event = _event(0)
        result = count_match_events(context, (event,))
        self.assertIsInstance(hash(context), int)
        self.assertIsInstance(hash(context.overflow_guidance), int)
        self.assertIsInstance(hash(event), int)
        self.assertIsInstance(hash(result), int)
        with self.assertRaises(FrozenInstanceError):
            event.flat_index = 2  # type: ignore[misc]

        source = inspect.getsource(match_counting_module)
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
        self.assertFalse(
            any(
                forbidden in imported.casefold()
                for imported in imports
                for forbidden in (
                    "constraint_distance",
                    "replacement",
                    "cuda",
                    "cupy",
                    "sqlite",
                    "repository",
                    "desktop",
                )
            )
        )
        self.assertNotIn("import time", source.casefold())


if __name__ == "__main__":
    unittest.main()
