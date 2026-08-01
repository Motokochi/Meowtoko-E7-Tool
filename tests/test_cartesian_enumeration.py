from __future__ import annotations

import ast
import inspect
import itertools
import math
import unittest
from dataclasses import FrozenInstanceError, replace

from src.optimizer.domain import (
    GEAR_SLOT_ORDER,
    EquipmentEligibilityReason,
    ItemProjectionMode,
)
from src.optimizer.engine import ItemProjectionEvidence
from src.optimizer.search import (
    CARTESIAN_SLOT_COUNT,
    SEARCH_PREPARATION_EXCLUSION_ORDER,
    CartesianBatch,
    CartesianEnumerationError,
    CartesianEnumerationSummary,
    CartesianSearchSpace,
    SearchItemPreparationDiagnostic,
    SearchPreparationDiagnostics,
    SearchReadySlotArrays,
    SearchSlotArray,
    SearchSlotPreparationDiagnostic,
    create_cartesian_search_space,
    flat_index_to_slot_offsets,
    iter_cartesian_batches,
    slot_offsets_to_flat_index,
)
from src.optimizer.search import cartesian as cartesian_module


def _space(radices: tuple[int, ...]) -> CartesianSearchSpace:
    return CartesianSearchSpace(radices, math.prod(radices))


def _clock(*values: float):
    supplied = iter(values)
    return lambda: next(supplied)


def _prepared_arrays(radices: tuple[int, ...]) -> SearchReadySlotArrays:
    if len(radices) != len(GEAR_SLOT_ORDER) or any(value <= 0 for value in radices):
        raise AssertionError("test fixture requires six positive radices")
    slots = []
    decisions = []
    slot_diagnostics = []
    reverse = []
    next_dense_id = 0
    for slot_index, (slot, count) in enumerate(zip(GEAR_SLOT_ORDER, radices, strict=True)):
        dense_ids = tuple(range(next_dense_id, next_dense_id + count))
        slots.append(
            SearchSlotArray(
                slot=slot,
                dense_ids=dense_ids,
                set_indices=(0,) * count,
                final_stat_contributions=((0.0,) * 8,) * count,
                gear_scores=(0,) * count,
            )
        )
        for item_index, dense_id in enumerate(dense_ids):
            stable_id = f"item.{slot_index}.{item_index:04d}"
            reverse.append((dense_id, stable_id))
            decisions.append(
                SearchItemPreparationDiagnostic(
                    stable_item_id=stable_id,
                    slot=slot,
                    included=True,
                    eligibility_reason=EquipmentEligibilityReason.UNEQUIPPED,
                    exclusion_reason=None,
                    projection_evidence=ItemProjectionEvidence.FRIBBELS_VALID,
                )
            )
        slot_diagnostics.append(
            SearchSlotPreparationDiagnostic(
                slot=slot,
                input_count=count,
                included_count=count,
                exclusion_counts=tuple(
                    (reason, 0) for reason in SEARCH_PREPARATION_EXCLUSION_ORDER
                ),
            )
        )
        next_dense_id += count
    diagnostics = SearchPreparationDiagnostics(
        projection_mode=ItemProjectionMode.CURRENT,
        decisions=tuple(decisions),
        slots=tuple(slot_diagnostics),
        unmatched_excluded_item_ids=(),
    )
    return SearchReadySlotArrays(
        tuple(slots),
        tuple(reverse),
        diagnostics,
        "request.cartesian",
        "hero.cartesian",
        "profile.cartesian",
        (1,) * 8,
    )


class CartesianEnumerationTests(unittest.TestCase):
    def test_every_small_combination_is_visited_once_in_product_order(self) -> None:
        radices = (2, 2, 1, 2, 1, 3)
        search_space = _space(radices)
        enumerator = iter_cartesian_batches(
            search_space,
            batch_size=5,
            clock=_clock(10.0, 12.5),
        )
        batches = tuple(enumerator)
        rows = tuple(row for batch in batches for row in batch.slot_offsets)
        expected = tuple(itertools.product(*(range(radix) for radix in radices)))

        self.assertEqual(expected, rows)
        self.assertEqual(search_space.total_permutations, len(rows))
        self.assertEqual(len(rows), len(set(rows)))
        self.assertEqual(
            tuple(range(search_space.total_permutations)),
            tuple(
                flat_index
                for batch in batches
                for flat_index in range(batch.start_index, batch.stop_index)
            ),
        )
        self.assertEqual(
            CartesianEnumerationSummary(24, 24, 2.5, True, False),
            enumerator.summary,
        )

    def test_flat_index_boundaries_and_round_trips_are_exact(self) -> None:
        search_space = _space((2, 3, 2, 2, 2, 3))
        fixtures = {
            0: (0, 0, 0, 0, 0, 0),
            1: (0, 0, 0, 0, 0, 1),
            2: (0, 0, 0, 0, 0, 2),
            3: (0, 0, 0, 0, 1, 0),
            6: (0, 0, 0, 1, 0, 0),
            12: (0, 0, 1, 0, 0, 0),
            24: (0, 1, 0, 0, 0, 0),
            72: (1, 0, 0, 0, 0, 0),
            143: (1, 2, 1, 1, 1, 2),
        }
        for flat_index, expected in fixtures.items():
            with self.subTest(flat_index=flat_index):
                self.assertEqual(expected, flat_index_to_slot_offsets(search_space, flat_index))
                self.assertEqual(flat_index, slot_offsets_to_flat_index(search_space, expected))
        for flat_index in range(search_space.total_permutations):
            offsets = flat_index_to_slot_offsets(search_space, flat_index)
            self.assertEqual(flat_index, slot_offsets_to_flat_index(search_space, offsets))

    def test_batch_sizes_and_subranges_are_contiguous_and_equivalent(self) -> None:
        search_space = _space((2, 2, 1, 2, 1, 3))
        expected = tuple(itertools.product(*(range(radix) for radix in search_space.radices)))
        for batch_size in (1, 4, 5, 24, 100):
            with self.subTest(batch_size=batch_size):
                enumerator = iter_cartesian_batches(
                    search_space,
                    batch_size,
                    clock=_clock(1.0, 2.0),
                )
                batches = tuple(enumerator)
                self.assertEqual(0, batches[0].start_index)
                self.assertEqual(24, batches[-1].stop_index)
                self.assertTrue(all(batch.count <= batch_size for batch in batches))
                self.assertTrue(
                    all(left.stop_index == right.start_index for left, right in itertools.pairwise(batches))
                )
                self.assertEqual(expected, tuple(row for batch in batches for row in batch.slot_offsets))

        ranged = iter_cartesian_batches(
            search_space,
            6,
            start_index=3,
            stop_index=17,
            clock=_clock(4.0, 5.25),
        )
        ranged_batches = tuple(ranged)
        self.assertEqual(((3, 9), (9, 15), (15, 17)), tuple((item.start_index, item.stop_index) for item in ranged_batches))
        self.assertEqual(expected[3:17], tuple(row for batch in ranged_batches for row in batch.slot_offsets))
        self.assertEqual(CartesianEnumerationSummary(24, 17, 1.25, False, False), ranged.summary)

        empty = iter_cartesian_batches(
            search_space,
            4,
            start_index=7,
            stop_index=7,
            clock=_clock(9.0),
        )
        self.assertEqual((), tuple(empty))
        self.assertEqual(CartesianEnumerationSummary(24, 7, 0.0, False, False), empty.summary)

    def test_huge_product_construction_and_first_batch_are_bounded(self) -> None:
        search_space = _space((1_000_000,) * CARTESIAN_SLOT_COUNT)
        self.assertEqual(10**36, search_space.total_permutations)
        enumerator = iter_cartesian_batches(search_space, 3, clock=_clock(20.0))
        first = next(enumerator)
        self.assertEqual((0, 3), (first.start_index, first.stop_index))
        self.assertEqual(
            (
                (0, 0, 0, 0, 0, 0),
                (0, 0, 0, 0, 0, 1),
                (0, 0, 0, 0, 0, 2),
            ),
            first.slot_offsets,
        )
        self.assertEqual(3, enumerator.searched_count)

    def test_prepared_arrays_compile_only_canonical_slot_lengths(self) -> None:
        arrays = _prepared_arrays((2, 1, 3, 1, 2, 4))
        search_space = create_cartesian_search_space(arrays)
        self.assertEqual((2, 1, 3, 1, 2, 4), search_space.radices)
        self.assertEqual(48, search_space.total_permutations)
        self.assertFalse(hasattr(search_space, "dense_id_to_stable_id"))
        self.assertFalse(hasattr(search_space, "slots"))

        corrupted = object.__new__(SearchReadySlotArrays)
        object.__setattr__(corrupted, "slots", tuple(reversed(arrays.slots)))
        with self.assertRaisesRegex(CartesianEnumerationError, "noncanonical-search-slots"):
            create_cartesian_search_space(corrupted)
        with self.assertRaisesRegex(CartesianEnumerationError, "validated SearchReadySlotArrays"):
            create_cartesian_search_space({"slots": arrays.slots})  # type: ignore[arg-type]

    def test_index_space_batch_and_range_inputs_fail_actionably(self) -> None:
        search_space = _space((2, 1, 1, 1, 2, 3))
        invalid_spaces = (
            ((1, 1, 1, 1, 1), 1),
            ((1, 1, 1, 1, 1, 1, 1), 1),
            ((1, 1, 0, 1, 1, 1), 0),
            ((1, 1, -1, 1, 1, 1), -1),
            ((1, 1, True, 1, 1, 1), 1),
            ((1, 1, 1.0, 1, 1, 1), 1),
            ((1, 1, 1, 1, 1, 1), True),
            ((1, 1, 1, 1, 1, 1), 2),
        )
        for radices, total in invalid_spaces:
            with self.subTest(radices=radices, total=total):
                with self.assertRaises(CartesianEnumerationError):
                    CartesianSearchSpace(radices, total)  # type: ignore[arg-type]

        for flat_index in (-1, True, 1.0, search_space.total_permutations):
            with self.subTest(flat_index=flat_index):
                with self.assertRaises(CartesianEnumerationError):
                    flat_index_to_slot_offsets(search_space, flat_index)  # type: ignore[arg-type]
        invalid_offsets = (
            (0, 0, 0, 0, 0),
            (0, 0, 0, 0, 0, 0, 0),
            (True, 0, 0, 0, 0, 0),
            (0.0, 0, 0, 0, 0, 0),
            (-1, 0, 0, 0, 0, 0),
            (2, 0, 0, 0, 0, 0),
            (0, 0, 0, 0, 2, 0),
            (0, 0, 0, 0, 0, 3),
        )
        for offsets in invalid_offsets:
            with self.subTest(offsets=offsets):
                with self.assertRaises(CartesianEnumerationError):
                    slot_offsets_to_flat_index(search_space, offsets)  # type: ignore[arg-type]

        invalid_iterator_arguments = (
            {"batch_size": 0},
            {"batch_size": True},
            {"batch_size": 1.0},
            {"batch_size": 1, "start_index": -1},
            {"batch_size": 1, "start_index": True},
            {"batch_size": 1, "start_index": 13},
            {"batch_size": 1, "stop_index": -1},
            {"batch_size": 1, "stop_index": True},
            {"batch_size": 1, "stop_index": 13},
            {"batch_size": 1, "start_index": 5, "stop_index": 4},
            {"batch_size": 1, "clock": 3},
        )
        for arguments in invalid_iterator_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(CartesianEnumerationError):
                    iter_cartesian_batches(search_space, **arguments)  # type: ignore[arg-type]

    def test_direct_batches_reject_corrupt_boundaries_and_rows(self) -> None:
        search_space = _space((2, 1, 1, 1, 2, 3))
        valid = CartesianBatch(
            search_space,
            0,
            2,
            ((0, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 1)),
        )
        invalid_changes = (
            {"start_index": True},
            {"start_index": -1},
            {"stop_index": 0},
            {"stop_index": 13},
            {"slot_offsets": valid.slot_offsets[:-1]},
            {"slot_offsets": ((0, 0, 0, 0, 0),) + valid.slot_offsets[1:]},
            {"slot_offsets": ((True, 0, 0, 0, 0, 0),) + valid.slot_offsets[1:]},
            {"slot_offsets": ((0.0, 0, 0, 0, 0, 0),) + valid.slot_offsets[1:]},
            {"slot_offsets": ((2, 0, 0, 0, 0, 0),) + valid.slot_offsets[1:]},
            {"slot_offsets": (valid.slot_offsets[1], valid.slot_offsets[0])},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(CartesianEnumerationError):
                    replace(valid, **changes)
        with self.assertRaisesRegex(CartesianEnumerationError, "validated CartesianSearchSpace"):
            replace(valid, search_space=(2, 1, 1, 1, 2, 3))  # type: ignore[arg-type]

    def test_summaries_clock_and_cancellation_evidence_are_consistent(self) -> None:
        search_space = _space((2, 1, 1, 1, 2, 3))
        enumerator = iter_cartesian_batches(search_space, 2, clock=_clock(5.0, 6.25))
        next(enumerator)
        self.assertEqual(
            CartesianEnumerationSummary(12, 2, 1.25, False, True),
            enumerator.snapshot(cancelled=True),
        )
        with self.assertRaisesRegex(CartesianEnumerationError, "cancelled"):
            enumerator.snapshot(cancelled=1)  # type: ignore[arg-type]

        invalid_summaries = (
            (0, 0, 0.0, False, False),
            (12, -1, 0.0, False, False),
            (12, 13, 0.0, False, False),
            (12, 1, -0.1, False, False),
            (12, 1, math.inf, False, False),
            (12, 1, True, False, False),
            (12, 1, 0.0, 1, False),
            (12, 1, 0.0, False, 0),
            (12, 1, 0.0, True, False),
            (12, 12, 0.0, True, True),
            (12, 12, 0.0, False, False),
        )
        for values in invalid_summaries:
            with self.subTest(values=values):
                with self.assertRaises(CartesianEnumerationError):
                    CartesianEnumerationSummary(*values)  # type: ignore[arg-type]

        with self.assertRaisesRegex(CartesianEnumerationError, "nonmonotonic-clock"):
            iterator = iter_cartesian_batches(search_space, 2, clock=_clock(3.0, 2.0))
            iterator.summary
        with self.assertRaisesRegex(CartesianEnumerationError, "finite number"):
            iter_cartesian_batches(search_space, 2, clock=lambda: math.nan)

    def test_retained_records_are_deeply_immutable_and_hashable(self) -> None:
        source_radices = [2, 1, 1, 1, 2, 3]
        search_space = CartesianSearchSpace(source_radices, 12)  # type: ignore[arg-type]
        source_rows = [[0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 1]]
        batch = CartesianBatch(search_space, 0, 2, source_rows)  # type: ignore[arg-type]
        summary = CartesianEnumerationSummary(12, 2, 0.5, False, False)
        source_radices[0] = 9
        source_rows[0][0] = 1
        self.assertEqual((2, 1, 1, 1, 2, 3), search_space.radices)
        self.assertEqual((0, 0, 0, 0, 0, 0), batch.slot_offsets[0])
        for record in (search_space, batch, summary):
            self.assertIsInstance(hash(record), int)
        with self.assertRaises(FrozenInstanceError):
            search_space.total_permutations = 10  # type: ignore[misc]

    def test_module_has_no_later_phase_dependencies_or_eager_product(self) -> None:
        source = inspect.getsource(cartesian_module)
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        forbidden_fragments = (
            "engine",
            "inventory",
            "replacement",
            "result_store",
            "cuda",
            "desktop",
            "repository",
        )
        self.assertFalse(
            tuple(
                name
                for name in imports
                if any(fragment in name.casefold() for fragment in forbidden_fragments)
            )
        )
        self.assertNotIn("itertools.product", source)


if __name__ == "__main__":
    unittest.main()
