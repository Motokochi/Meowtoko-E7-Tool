from __future__ import annotations

import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from src.optimizer.domain import (
    FRIBBELS_SET_ORDER,
    GEAR_SLOT_ORDER,
    RESULT_CATEGORY_ORDER,
    SET_CATALOG,
    GearSet,
    ResultCategory,
    SetPattern,
)
from src.optimizer.result_store import (
    RESULT_DERIVED_METRIC_ORDER,
    RESULT_FILTER_ID,
    RESULT_FILTER_VERSION,
    RESULT_PRIMARY_STAT_ORDER,
    FilterScopeDecision,
    InclusiveFloat32Range,
    InclusiveInt64Range,
    OriginalResultScope,
    ResultFilterError,
    ResultFilterRequest,
    ResultRunStore,
    SetCountFilter,
    SlotItemFilter,
    assess_filter_scope,
    filter_completed_result_run,
)
from src.optimizer.search import compile_set_pattern
from tests.test_result_schema import _valid_columns


def _set_index(gear_set: GearSet) -> int:
    return SET_CATALOG[gear_set].fribbels_index


TARGET = compile_set_pattern(SetPattern((GearSet.SPEED, GearSet.HEALTH)))


def _columns() -> dict[str, np.ndarray]:
    count = 9
    arrays = _valid_columns(count)
    speed = _set_index(GearSet.SPEED)
    health = _set_index(GearSet.HEALTH)
    defense = _set_index(GearSet.DEFENSE)
    critical = _set_index(GearSet.CRITICAL)
    arrays["owned_set_indices"] = np.asarray(
        (
            (speed, speed, speed, speed, health, health),
            (speed, speed, speed, speed, health, defense),
            (speed, speed, speed, speed, speed, health),
            (speed, speed, speed, health, defense, defense),
            (speed, speed, speed, speed, speed, speed),
            (health, health, health, health, health, health),
            (critical, critical, critical, critical, critical, critical),
            (speed, speed, health, health, defense, defense),
            (speed, speed, speed, speed, health, health),
        ),
        dtype="u1",
    )
    arrays["category_codes"] = np.asarray((0, 1, 1, 2, 2, 2, 2, 2, 0), dtype="u1")
    arrays["replacement_distances"] = arrays["category_codes"].copy()
    arrays["effective_final_stats"] = (
        np.arange(count * len(RESULT_PRIMARY_STAT_ORDER), dtype="<i8").reshape(count, -1) * 10
    )
    arrays["raw_critical_hit_chances"] = arrays["effective_final_stats"][:, 4].copy()
    arrays["effective_final_stats"][0, 4] = 100
    arrays["raw_critical_hit_chances"][0] = 135
    arrays["derived_metrics"] = (
        np.arange(count * len(RESULT_DERIVED_METRIC_ORDER), dtype="<i8").reshape(count, -1) - 70
    )
    priority_bits = np.asarray([0x3F800000 + index for index in range(count)], dtype="<u4")
    arrays["priority_scores"] = priority_bits.view("<f4")
    arrays["constraint_distances"] = np.asarray(
        (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.0), dtype="<f4"
    )
    arrays["equipped_item_counts"] = np.asarray((0, 1, 2, 3, 4, 5, 6, 0, 1), dtype="u1")
    return arrays


def _completed(arrays: dict[str, np.ndarray], root: str):
    store = ResultRunStore(Path(root) / "results")
    writer = store.begin_run("filter-fixture")
    writer.append(0, arrays)
    return writer.complete()


def _scope(baseline: ResultFilterRequest | None = None) -> OriginalResultScope:
    return OriginalResultScope.create(baseline or ResultFilterRequest(), TARGET)


def _within(value: int | float, interval: object) -> bool:
    minimum = getattr(interval, "minimum")
    maximum = getattr(interval, "maximum")
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _row_matches(arrays: dict[str, np.ndarray], row: int, request: ResultFilterRequest) -> bool:
    category = RESULT_CATEGORY_ORDER[int(arrays["category_codes"][row])]
    if category not in request.categories:
        return False
    if not _within(int(arrays["replacement_distances"][row]), request.replacement_distance):
        return False
    primary = arrays["effective_final_stats"][row].copy()
    primary[4] = arrays["raw_critical_hit_chances"][row]
    for value, interval in zip(primary, request.primary_ranges, strict=True):
        if not _within(int(value), interval):
            return False
    for value, interval in zip(arrays["derived_metrics"][row], request.derived_ranges, strict=True):
        if not _within(int(value), interval):
            return False
    if not _within(float(arrays["priority_scores"][row]), request.priority_score):
        return False
    if not _within(float(arrays["constraint_distances"][row]), request.constraint_distance):
        return False
    if not _within(int(arrays["equipped_item_counts"][row]), request.equipped_count):
        return False
    dense = tuple(int(item) for item in arrays["dense_item_ids"][row])
    if request.included_dense_item_ids and not set(dense).intersection(request.included_dense_item_ids):
        return False
    if set(dense).intersection(request.excluded_dense_item_ids):
        return False
    for item in request.slot_item_filters:
        if dense[item.slot_index] not in item.allowed_dense_item_ids:
            return False
    sets = tuple(int(item) for item in arrays["owned_set_indices"][row])
    for item in request.set_count_filters:
        pieces = sets.count(item.set_index)
        if not _within(pieces, item.piece_count):
            return False
        metadata = SET_CATALOG[FRIBBELS_SET_ORDER[item.set_index]]
        activations = pieces // metadata.pieces_required
        if not metadata.stackable:
            activations = min(activations, 1)
        if not _within(activations, item.activation_count):
            return False
    return True


def _oracle(arrays: dict[str, np.ndarray], request: ResultFilterRequest) -> tuple[int, ...]:
    return tuple(row for row in range(len(arrays["category_codes"])) if _row_matches(arrays, row, request))


class ResultFilterContractTests(unittest.TestCase):
    def test_version_defaults_axes_hashes_and_duplicate_canonicalization_are_stable(self) -> None:
        first = ResultFilterRequest(
            categories=(ResultCategory.TWO_AWAY, ResultCategory.EXACT, ResultCategory.EXACT),
            included_dense_item_ids=(9, 3, 9),
            excluded_dense_item_ids=(20, 10, 20),
            slot_item_filters=(SlotItemFilter(2, (8, 7, 8)), SlotItemFilter(2, (7, 8))),
            set_count_filters=(SetCountFilter(3, InclusiveInt64Range(2, 4)),) * 2,
        )
        second = ResultFilterRequest(
            categories=(ResultCategory.EXACT, ResultCategory.TWO_AWAY),
            included_dense_item_ids=(3, 9),
            excluded_dense_item_ids=(10, 20),
            slot_item_filters=(SlotItemFilter(2, (7, 8)),),
            set_count_filters=(SetCountFilter(3, InclusiveInt64Range(2, 4)),),
        )
        self.assertEqual(RESULT_FILTER_ID, first.filter_id)
        self.assertEqual(RESULT_FILTER_VERSION, first.version)
        self.assertEqual(len(RESULT_PRIMARY_STAT_ORDER), len(first.primary_ranges))
        self.assertEqual(len(RESULT_DERIVED_METRIC_ORDER), len(first.derived_ranges))
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))

    def test_empty_selection_meanings_are_explicit(self) -> None:
        self.assertEqual((), ResultFilterRequest(categories=()).categories)
        self.assertEqual((), ResultFilterRequest(included_dense_item_ids=()).included_dense_item_ids)
        self.assertEqual((), ResultFilterRequest(excluded_dense_item_ids=()).excluded_dense_item_ids)
        self.assertEqual((), SlotItemFilter(0, ()).allowed_dense_item_ids)

    def test_invalid_ranges_axes_numbers_counts_and_versions_are_actionable(self) -> None:
        invalid = (
            lambda: InclusiveInt64Range(2, 1),
            lambda: InclusiveInt64Range(True, 1),
            lambda: InclusiveFloat32Range(float("nan"), None),
            lambda: InclusiveFloat32Range(None, float("inf")),
            lambda: InclusiveFloat32Range(1e100, None),
            lambda: ResultFilterRequest(primary_ranges=(InclusiveInt64Range(),)),
            lambda: ResultFilterRequest(version=2),
            lambda: ResultFilterRequest(equipped_count=InclusiveInt64Range(0, 7)),
            lambda: ResultFilterRequest(replacement_distance=InclusiveInt64Range(-1, 2)),
            lambda: ResultFilterRequest(included_dense_item_ids=(-1,)),
            lambda: SlotItemFilter(6, (1,)),
            lambda: SetCountFilter(24),
        )
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(ResultFilterError) as raised:
                operation()
            self.assertTrue(raised.exception.code)
            self.assertTrue(raised.exception.path)

    def test_binary32_endpoints_are_explicit_and_preserve_adjacent_bits(self) -> None:
        lower = np.asarray([0x3F800001], dtype="<u4").view("<f4")[0]
        upper = np.asarray([0x3F800002], dtype="<u4").view("<f4")[0]
        interval = InclusiveFloat32Range(float(lower), float(upper))
        self.assertEqual(int(np.asarray([interval.minimum], dtype="<f4").view("<u4")[0]), 0x3F800001)
        self.assertEqual(int(np.asarray([interval.maximum], dtype="<f4").view("<u4")[0]), 0x3F800002)

    def test_scope_equal_tightening_and_every_broadening_direction(self) -> None:
        primary = list(ResultFilterRequest().primary_ranges)
        primary[0] = InclusiveInt64Range(10, 100)
        baseline = ResultFilterRequest(
            categories=(ResultCategory.EXACT, ResultCategory.ONE_AWAY),
            primary_ranges=tuple(primary),
            priority_score=InclusiveFloat32Range(0.5, 2.0),
            included_dense_item_ids=(1, 2, 3),
            excluded_dense_item_ids=(8,),
            slot_item_filters=(SlotItemFilter(0, (1, 2)),),
            set_count_filters=(SetCountFilter(3, InclusiveInt64Range(3, 6)),),
        )
        scope = _scope(baseline)
        self.assertIs(FilterScopeDecision.EQUAL, assess_filter_scope(scope, baseline).decision)
        narrower_primary = list(primary)
        narrower_primary[0] = InclusiveInt64Range(20, 90)
        tighter = replace(
            baseline,
            categories=(ResultCategory.EXACT,),
            primary_ranges=tuple(narrower_primary),
            included_dense_item_ids=(1,),
            excluded_dense_item_ids=(8, 9),
            slot_item_filters=(SlotItemFilter(0, (1,)),),
        )
        self.assertIs(FilterScopeDecision.TIGHTENING, assess_filter_scope(scope, tighter).decision)

        broadenings = (
            replace(baseline, categories=RESULT_CATEGORY_ORDER),
            replace(baseline, primary_ranges=tuple([InclusiveInt64Range(9, 100), *primary[1:]])),
            replace(baseline, primary_ranges=tuple([InclusiveInt64Range(10, 101), *primary[1:]])),
            replace(baseline, primary_ranges=tuple([InclusiveInt64Range(), *primary[1:]])),
            replace(baseline, included_dense_item_ids=(1, 2, 3, 4)),
            replace(baseline, excluded_dense_item_ids=()),
            replace(baseline, slot_item_filters=()),
            replace(baseline, set_count_filters=()),
        )
        for request in broadenings:
            with self.subTest(request=request):
                assessment = assess_filter_scope(scope, request)
                self.assertIs(FilterScopeDecision.RERUN_REQUIRED, assessment.decision)
                self.assertTrue(assessment.reasons)

    def test_refiltering_compares_to_base_scope_not_a_previous_view(self) -> None:
        scope = _scope()
        stat_ranges = list(ResultFilterRequest().primary_ranges)
        stat_ranges[0] = InclusiveInt64Range(500, None)
        very_tight = ResultFilterRequest(primary_ranges=tuple(stat_ranges))
        stat_ranges[0] = InclusiveInt64Range(100, None)
        widened_view = ResultFilterRequest(primary_ranges=tuple(stat_ranges))
        self.assertIs(FilterScopeDecision.TIGHTENING, assess_filter_scope(scope, very_tight).decision)
        self.assertIs(FilterScopeDecision.TIGHTENING, assess_filter_scope(scope, widened_view).decision)


class ResultFilterExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="e7-result-filter-")
        self.arrays = _columns()
        self.run = _completed(self.arrays, self.temporary.name)
        self.scope = _scope()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assertFilter(self, request: ResultFilterRequest, *, chunk_rows: int = 3) -> None:
        outcome = filter_completed_result_run(self.run, request, self.scope, chunk_rows=chunk_rows)
        self.assertFalse(outcome.rerun_required)
        self.assertIsNotNone(outcome.view)
        assert outcome.view is not None
        self.assertEqual(_oracle(self.arrays, request), tuple(int(item) for item in outcome.view.row_ordinals))
        self.assertEqual("<u4", outcome.view.row_ordinals.dtype.str)
        self.assertFalse(outcome.view.row_ordinals.flags.writeable)

    def test_zero_all_first_middle_last_and_chunk_boundaries_preserve_stable_ordinals(self) -> None:
        all_outcome = filter_completed_result_run(self.run, ResultFilterRequest(), self.scope, chunk_rows=4)
        assert all_outcome.view is not None
        self.assertEqual(tuple(range(9)), tuple(all_outcome.view.row_ordinals))
        self.assertEqual(9 * 4, all_outcome.view.stats.ordinal_capacity_bytes)
        self.assertEqual(4, all_outcome.view.stats.peak_chunk_rows)
        self.assertEqual(4 * 128, all_outcome.view.stats.temporary_byte_upper_bound)
        self.assertFilter(ResultFilterRequest(categories=()), chunk_rows=4)
        self.assertFilter(ResultFilterRequest(included_dense_item_ids=(0,)))
        self.assertFilter(ResultFilterRequest(included_dense_item_ids=(26,)))
        self.assertFilter(ResultFilterRequest(included_dense_item_ids=(53,)))

    def test_every_numeric_axis_uses_inclusive_signed_or_binary32_semantics(self) -> None:
        for axis in range(len(RESULT_PRIMARY_STAT_ORDER)):
            ranges = list(ResultFilterRequest().primary_ranges)
            value = int(
                self.arrays["raw_critical_hit_chances"][4]
                if axis == 4
                else self.arrays["effective_final_stats"][4, axis]
            )
            ranges[axis] = InclusiveInt64Range(value, value)
            self.assertFilter(ResultFilterRequest(primary_ranges=tuple(ranges)))
        for axis in range(len(RESULT_DERIVED_METRIC_ORDER)):
            ranges = list(ResultFilterRequest().derived_ranges)
            value = int(self.arrays["derived_metrics"][2, axis])
            ranges[axis] = InclusiveInt64Range(value, value)
            self.assertFilter(ResultFilterRequest(derived_ranges=tuple(ranges)))
        priority = float(self.arrays["priority_scores"][3])
        self.assertFilter(ResultFilterRequest(priority_score=InclusiveFloat32Range(priority, priority)))
        constraint = float(self.arrays["constraint_distances"][4])
        self.assertFilter(ResultFilterRequest(constraint_distance=InclusiveFloat32Range(constraint, constraint)))
        self.assertFilter(ResultFilterRequest(equipped_count=InclusiveInt64Range(1, 4)))
        self.assertFilter(ResultFilterRequest(replacement_distance=InclusiveInt64Range(1, 2)))

    def test_category_item_exclusion_and_per_slot_predicates_compose_with_and(self) -> None:
        request = ResultFilterRequest(
            categories=(ResultCategory.ONE_AWAY, ResultCategory.TWO_AWAY),
            included_dense_item_ids=(12, 13, 14, 15, 16, 17, 24),
            excluded_dense_item_ids=(15,),
            slot_item_filters=(SlotItemFilter(0, (12, 18, 24)), SlotItemFilter(5, (17, 23, 29))),
            equipped_count=InclusiveInt64Range(1, 5),
        )
        self.assertFilter(request, chunk_rows=2)
        self.assertFilter(ResultFilterRequest(slot_item_filters=(SlotItemFilter(0, ()),)))

    def test_set_piece_and_activation_counts_include_repeated_stackable_sets(self) -> None:
        health = _set_index(GearSet.HEALTH)
        speed = _set_index(GearSet.SPEED)
        self.assertFilter(
            ResultFilterRequest(
                set_count_filters=(SetCountFilter(health, InclusiveInt64Range(6, 6), InclusiveInt64Range(3, 3)),)
            )
        )
        self.assertFilter(
            ResultFilterRequest(
                set_count_filters=(SetCountFilter(speed, InclusiveInt64Range(6, 6), InclusiveInt64Range(1, 1)),)
            )
        )

    def test_randomized_combinations_match_deliberately_simple_row_oracle(self) -> None:
        generator = random.Random(77103)
        for _ in range(80):
            primary = list(ResultFilterRequest().primary_ranges)
            derived = list(ResultFilterRequest().derived_ranges)
            primary_axis = generator.randrange(len(primary))
            derived_axis = generator.randrange(len(derived))
            primary_minimum = generator.choice((None, generator.randrange(0, 650)))
            primary_maximum = generator.choice((None, generator.randrange(50, 800)))
            if primary_minimum is not None and primary_maximum is not None and primary_minimum > primary_maximum:
                primary_minimum, primary_maximum = primary_maximum, primary_minimum
            primary[primary_axis] = InclusiveInt64Range(primary_minimum, primary_maximum)
            low = generator.randrange(-70, 60)
            high = generator.randrange(low, 70)
            derived[derived_axis] = InclusiveInt64Range(low, high)
            categories = tuple(item for item in RESULT_CATEGORY_ORDER if generator.choice((False, True)))
            request = ResultFilterRequest(
                categories=categories,
                primary_ranges=tuple(primary),
                derived_ranges=tuple(derived),
                equipped_count=InclusiveInt64Range(generator.randrange(0, 4), generator.randrange(4, 7)),
                included_dense_item_ids=() if generator.choice((False, True)) else (generator.randrange(54),),
                excluded_dense_item_ids=() if generator.choice((False, True)) else (generator.randrange(54),),
            )
            self.assertFilter(request, chunk_rows=generator.randrange(1, 7))

    def test_rerun_required_does_not_expose_a_partial_view(self) -> None:
        baseline = replace(ResultFilterRequest(), categories=(ResultCategory.EXACT,))
        outcome = filter_completed_result_run(
            self.run,
            ResultFilterRequest(),
            _scope(baseline),
        )
        self.assertTrue(outcome.rerun_required)
        self.assertIsNone(outcome.view)

    def test_zero_row_completed_memmap_fixture(self) -> None:
        root = Path(self.temporary.name) / "empty"
        empty = ResultRunStore(root).begin_run("empty-run").complete()
        outcome = filter_completed_result_run(empty, ResultFilterRequest(), self.scope, chunk_rows=1)
        assert outcome.view is not None
        self.assertEqual(0, outcome.view.row_ordinals.size)
        self.assertEqual(0, outcome.view.stats.peak_chunk_rows)
        self.assertEqual(0, outcome.view.stats.ordinal_capacity_bytes)

    def test_chunk_size_validation_is_bounded(self) -> None:
        for invalid in (0, 1_000_001, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ResultFilterError):
                filter_completed_result_run(self.run, ResultFilterRequest(), self.scope, chunk_rows=invalid)


if __name__ == "__main__":
    unittest.main()
