from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.optimizer.domain import GearSet, SetPattern
from src.optimizer.result_store import (
    CONSTRAINT_DISTANCE_SORT_KEY,
    DEFAULT_INDEX_CACHE_BYTES,
    EQUIPPED_COUNT_SORT_KEY,
    MAX_PAGE_SIZE,
    PRIORITY_SCORE_SORT_KEY,
    REPLACEMENT_COUNT_SORT_KEY,
    RESULT_DERIVED_SORT_KEYS,
    RESULT_INDEX_FILENAME,
    RESULT_INDEX_ID,
    RESULT_INDEX_MANIFEST_NAME,
    RESULT_INDEX_VERSION,
    RESULT_PAGE_ID,
    RESULT_PAGE_VERSION,
    RESULT_PRIMARY_SORT_KEYS,
    RESULT_SORT_KEYS,
    CompletedResultSortIndex,
    FilteredResultView,
    InclusiveInt64Range,
    OriginalResultScope,
    ResultFilterExecutionStats,
    ResultFilterRequest,
    ResultIndexError,
    ResultPageRequest,
    ResultSortDirection,
    ResultSortIndexCache,
    ResultSortRequest,
    ResultRunStore,
    build_result_sort_index,
    filter_completed_result_run,
    page_result_sort_index,
    project_result_sort_index,
    project_result_sort_cache_entry,
)
from src.optimizer.search import compile_set_pattern
from tests.test_result_schema import _valid_columns


def _columns(row_count: int = 13) -> dict[str, np.ndarray]:
    arrays = _valid_columns(row_count)
    rows = np.arange(row_count, dtype="<i8")
    stats = np.empty((row_count, 8), dtype="<i8")
    metrics = np.empty((row_count, 15), dtype="<i8")
    for axis in range(8):
        stats[:, axis] = ((rows * (axis + 1)) % 5) * 10 + axis
    for axis in range(15):
        metrics[:, axis] = ((rows * (axis + 2)) % 7) - 4 - axis
    arrays["effective_final_stats"] = stats
    arrays["raw_critical_hit_chances"] = stats[:, 4].copy()
    arrays["effective_final_stats"][0, 4] = 100
    arrays["raw_critical_hit_chances"][0] = 135
    arrays["derived_metrics"] = metrics
    priority_bits = np.asarray(
        [0x3F800000 + (row % 4) for row in range(row_count)],
        dtype="<u4",
    )
    arrays["priority_scores"] = priority_bits.view("<f4")
    categories = np.asarray([row % 3 for row in range(row_count)], dtype="u1")
    arrays["category_codes"] = categories
    arrays["replacement_distances"] = categories.copy()
    arrays["constraint_distances"] = np.asarray(
        [0.0 if category == 0 else (row % 4) / 10 for row, category in enumerate(categories)],
        dtype="<f4",
    )
    arrays["equipped_item_counts"] = np.asarray([row % 4 for row in range(row_count)], dtype="u1")
    return arrays


def _completed(arrays: dict[str, np.ndarray], root: Path, run_id: str = "sort-fixture"):
    writer = ResultRunStore(root / "runs").begin_run(run_id)
    writer.append(0, arrays)
    return writer.complete()


def _view(ordinals: tuple[int, ...]) -> FilteredResultView:
    values = np.asarray(ordinals, dtype="<u4")
    stats = ResultFilterExecutionStats(
        scanned_rows=max(ordinals, default=-1) + 1,
        matched_rows=len(ordinals),
        chunk_rows=10,
        peak_chunk_rows=min(10, len(ordinals)),
        ordinal_capacity_bytes=len(ordinals) * 4,
        temporary_byte_upper_bound=0,
    )
    return FilteredResultView(values, stats)


def _key_values(arrays: dict[str, np.ndarray], request: ResultSortRequest) -> np.ndarray:
    key = request.sort_key
    column = arrays[key.column_name]
    return column if key.axis_index is None else column[:, key.axis_index]


def _oracle(
    arrays: dict[str, np.ndarray],
    request: ResultSortRequest,
    source: tuple[int, ...] | None = None,
) -> tuple[int, ...]:
    ordinals = tuple(range(len(arrays["category_codes"]))) if source is None else source
    values = _key_values(arrays, request)
    if request.direction is ResultSortDirection.ASCENDING:
        return tuple(sorted(ordinals, key=lambda row: (values[row].item(), row)))
    return tuple(sorted(ordinals, key=lambda row: (-values[row].item(), row)))


def _close_index(index: CompletedResultSortIndex) -> None:
    mapping = getattr(index.row_ordinals, "_mmap", None)
    if mapping is not None:
        mapping.close()


class ResultIndexContractTests(unittest.TestCase):
    def test_sort_catalog_covers_every_axis_and_scalar_with_stable_unique_ids(self) -> None:
        self.assertEqual(8, len(RESULT_PRIMARY_SORT_KEYS))
        self.assertEqual(15, len(RESULT_DERIVED_SORT_KEYS))
        self.assertEqual(27, len(RESULT_SORT_KEYS))
        self.assertEqual(27, len({item.key_id for item in RESULT_SORT_KEYS}))
        self.assertEqual(
            (
                PRIORITY_SCORE_SORT_KEY,
                CONSTRAINT_DISTANCE_SORT_KEY,
                REPLACEMENT_COUNT_SORT_KEY,
                EQUIPPED_COUNT_SORT_KEY,
            ),
            RESULT_SORT_KEYS[-4:],
        )

    def test_requests_are_versioned_canonical_immutable_and_hash_stable(self) -> None:
        first = ResultSortRequest(
            sort_key=RESULT_PRIMARY_SORT_KEYS[2].key_id,
            direction="ascending",  # type: ignore[arg-type]
        )
        second = ResultSortRequest(
            sort_key=RESULT_PRIMARY_SORT_KEYS[2],
            direction=ResultSortDirection.ASCENDING,
        )
        self.assertEqual(RESULT_INDEX_ID, first.index_id)
        self.assertEqual(RESULT_INDEX_VERSION, first.version)
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        page = ResultPageRequest(page_index=2, page_size=17)
        self.assertEqual(RESULT_PAGE_ID, page.page_id)
        self.assertEqual(RESULT_PAGE_VERSION, page.version)
        self.assertIsInstance(hash(page), int)

    def test_invalid_sort_page_and_projection_inputs_are_actionable(self) -> None:
        invalid = (
            lambda: ResultSortRequest(index_id="wrong"),
            lambda: ResultSortRequest(version=2),
            lambda: ResultSortRequest(sort_key="unknown"),
            lambda: ResultSortRequest(direction="sideways"),
            lambda: ResultPageRequest(page_id="wrong"),
            lambda: ResultPageRequest(version=2),
            lambda: ResultPageRequest(page_index=-1),
            lambda: ResultPageRequest(page_size=0),
            lambda: ResultPageRequest(page_size=MAX_PAGE_SIZE + 1),
            lambda: project_result_sort_index(-1),
            lambda: project_result_sort_index(1, "unknown"),
            lambda: ResultSortIndexCache(123),  # type: ignore[arg-type]
        )
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(ResultIndexError) as raised:
                operation()
            self.assertTrue(raised.exception.code)
            self.assertTrue(raised.exception.path)

    def test_five_million_projection_and_default_cache_budget_are_explicit(self) -> None:
        integer = project_result_sort_index(5_000_000, RESULT_PRIMARY_SORT_KEYS[0])
        binary32 = project_result_sort_index(5_000_000, PRIORITY_SCORE_SORT_KEY)
        byte = project_result_sort_index(5_000_000, REPLACEMENT_COUNT_SORT_KEY)
        self.assertEqual(20_000_000, integer.source_ordinal_bytes)
        self.assertEqual(40_000_000, integer.key_value_bytes)
        self.assertEqual(40_000_000, integer.sort_order_bytes)
        self.assertEqual(40_000_000, integer.sort_workspace_bytes)
        self.assertEqual(20_000_000, integer.output_index_bytes)
        self.assertEqual(160_000_000, integer.declared_peak_array_bytes)
        self.assertEqual(140_000_000, binary32.declared_peak_array_bytes)
        self.assertEqual(125_000_000, byte.declared_peak_array_bytes)
        self.assertEqual(20_000_000, integer.cache_index_bytes)
        self.assertGreaterEqual(DEFAULT_INDEX_CACHE_BYTES, 8 * integer.cache_index_bytes)
        base_cache = project_result_sort_cache_entry(5_000_000, ResultSortRequest(sort_key=RESULT_PRIMARY_SORT_KEYS[0]))
        filtered_cache = project_result_sort_cache_entry(
            5_000_000,
            ResultSortRequest(sort_key=RESULT_PRIMARY_SORT_KEYS[0]),
            filtered_view=True,
        )
        self.assertEqual(20_000_000, base_cache.index_bytes)
        self.assertEqual(base_cache.index_bytes + base_cache.manifest_bytes, base_cache.total_bytes)
        self.assertGreater(filtered_cache.manifest_bytes, base_cache.manifest_bytes)
        self.assertLess(filtered_cache.manifest_bytes, 1_000)


class ResultIndexExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="e7-result-index-")
        self.root = Path(self.temporary.name)
        self.arrays = _columns()
        self.run = _completed(self.arrays, self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assertSort(
        self,
        request: ResultSortRequest,
        *,
        view: FilteredResultView | None = None,
    ) -> CompletedResultSortIndex:
        index = build_result_sort_index(self.run, request, view=view)
        source = None if view is None else tuple(int(item) for item in view.row_ordinals)
        self.assertEqual(_oracle(self.arrays, request, source), tuple(int(item) for item in index.row_ordinals))
        self.assertEqual("<u4", index.row_ordinals.dtype.str)
        self.assertFalse(index.row_ordinals.flags.writeable)
        return index

    def test_every_sort_key_and_direction_matches_simple_reference(self) -> None:
        for key in RESULT_SORT_KEYS:
            for direction in ResultSortDirection:
                with self.subTest(key=key.key_id, direction=direction):
                    self.assertSort(ResultSortRequest(sort_key=key, direction=direction))

    def test_filtered_empty_single_and_noncontiguous_views_sort_only_their_members(self) -> None:
        request = ResultSortRequest(
            sort_key=RESULT_DERIVED_SORT_KEYS[3],
            direction=ResultSortDirection.DESCENDING,
        )
        for ordinals in ((), (7,), (0, 2, 5, 8, 12)):
            with self.subTest(ordinals=ordinals):
                self.assertSort(request, view=_view(ordinals))

    def test_actual_p07_filter_view_flows_directly_into_sorting_and_paging(self) -> None:
        filter_request = ResultFilterRequest(equipped_count=InclusiveInt64Range(1, 2))
        scope = OriginalResultScope.create(
            ResultFilterRequest(),
            compile_set_pattern(SetPattern((GearSet.SPEED, GearSet.HEALTH))),
        )
        outcome = filter_completed_result_run(self.run, filter_request, scope, chunk_rows=4)
        assert outcome.view is not None
        request = ResultSortRequest(
            sort_key=RESULT_DERIVED_SORT_KEYS[4],
            direction=ResultSortDirection.DESCENDING,
        )
        index = self.assertSort(request, view=outcome.view)
        page = page_result_sort_index(index, ResultPageRequest(page_size=3))
        self.assertEqual(tuple(index.row_ordinals[:3]), tuple(page.row_ordinals))

    def test_equal_values_keep_ascending_physical_ordinal_ties_in_both_directions(self) -> None:
        for key in (
            RESULT_PRIMARY_SORT_KEYS[0],
            RESULT_DERIVED_SORT_KEYS[0],
            PRIORITY_SCORE_SORT_KEY,
            CONSTRAINT_DISTANCE_SORT_KEY,
            REPLACEMENT_COUNT_SORT_KEY,
            EQUIPPED_COUNT_SORT_KEY,
        ):
            values = _key_values(self.arrays, ResultSortRequest(sort_key=key))
            for direction in ResultSortDirection:
                index = self.assertSort(ResultSortRequest(sort_key=key, direction=direction))
                actual = tuple(int(item) for item in index.row_ordinals)
                for value in np.unique(values):
                    tied = tuple(row for row in actual if values[row] == value)
                    self.assertEqual(tuple(sorted(tied)), tied)

    def test_adjacent_binary32_and_negative_signed_metrics_are_not_narrowed(self) -> None:
        priority = self.assertSort(
            ResultSortRequest(
                sort_key=PRIORITY_SCORE_SORT_KEY,
                direction=ResultSortDirection.ASCENDING,
            )
        )
        bits = self.arrays["priority_scores"][priority.row_ordinals].view("<u4")
        self.assertTrue(np.all(bits[1:] >= bits[:-1]))
        metrics = self.assertSort(
            ResultSortRequest(
                sort_key=RESULT_DERIVED_SORT_KEYS[14],
                direction=ResultSortDirection.ASCENDING,
            )
        )
        values = self.arrays["derived_metrics"][metrics.row_ordinals, 14]
        self.assertTrue(np.any(values < 0))
        self.assertTrue(np.all(values[1:] >= values[:-1]))

    def test_page_metadata_and_first_middle_last_partial_and_out_of_range_slices(self) -> None:
        index = self.assertSort(ResultSortRequest(sort_key=RESULT_PRIMARY_SORT_KEYS[4]))
        expected = tuple(int(item) for item in index.row_ordinals)
        for page_index, expected_slice in (
            (0, expected[0:5]),
            (1, expected[5:10]),
            (2, expected[10:13]),
            (3, ()),
        ):
            page = page_result_sort_index(index, ResultPageRequest(page_index=page_index, page_size=5))
            self.assertEqual(expected_slice, tuple(int(item) for item in page.row_ordinals))
            self.assertEqual(13, page.total_rows)
            self.assertEqual(3, page.page_count)
            self.assertEqual(page_index == 3, page.out_of_range)
            self.assertLessEqual(page.row_ordinals.nbytes, 5 * 4)
            self.assertFalse(page.row_ordinals.flags.writeable)
        first = page_result_sort_index(index, ResultPageRequest(page_index=0, page_size=5))
        middle = page_result_sort_index(index, ResultPageRequest(page_index=1, page_size=5))
        last = page_result_sort_index(index, ResultPageRequest(page_index=2, page_size=5))
        self.assertFalse(first.has_previous)
        self.assertTrue(first.has_next)
        self.assertTrue(middle.has_previous and middle.has_next)
        self.assertTrue(last.has_previous)
        self.assertFalse(last.has_next)

    def test_empty_page_zero_is_valid_and_later_empty_pages_are_out_of_range(self) -> None:
        index = self.assertSort(ResultSortRequest(), view=_view(()))
        first = page_result_sort_index(index, ResultPageRequest(page_index=0, page_size=10))
        later = page_result_sort_index(index, ResultPageRequest(page_index=2, page_size=10))
        self.assertEqual(0, first.page_count)
        self.assertFalse(first.out_of_range)
        self.assertTrue(later.out_of_range)
        self.assertEqual((), tuple(first.row_ordinals))
        self.assertEqual((), tuple(later.row_ordinals))

    def test_invalid_view_nonfinite_key_and_memory_budget_fail_before_presenting_an_index(self) -> None:
        with self.assertRaisesRegex(ResultIndexError, "view-ordinal-out-of-range"):
            build_result_sort_index(self.run, ResultSortRequest(), view=_view((999,)))
        with self.assertRaisesRegex(ResultIndexError, "sort-memory-budget"):
            build_result_sort_index(
                self.run,
                ResultSortRequest(sort_key=RESULT_PRIMARY_SORT_KEYS[0]),
                maximum_build_array_bytes=100,
            )
        priority_path = self.run.column_spec("priority_scores").path
        with priority_path.open("r+b") as file:
            file.write(np.asarray([np.nan], dtype="<f4").tobytes())
        with self.assertRaisesRegex(ResultIndexError, "nonfinite-sort-key"):
            build_result_sort_index(self.run, ResultSortRequest())


class ResultIndexCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="e7-result-index-cache-")
        self.root = Path(self.temporary.name)
        self.arrays = _columns()
        self.run = _completed(self.arrays, self.root, "cache-fixture")
        self.cache_root = self.root / "cache"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_construction_does_not_touch_explicit_root(self) -> None:
        cache = ResultSortIndexCache(self.cache_root)
        self.assertEqual(self.cache_root, cache.root)
        self.assertFalse(self.cache_root.exists())

    def test_non_directory_cache_root_is_rejected_without_touching_run_data(self) -> None:
        bad_root = self.root / "cache-file"
        bad_root.write_text("not a cache directory", encoding="utf-8")
        with self.assertRaisesRegex(ResultIndexError, "unsafe-cache-root"):
            build_result_sort_index(
                self.run,
                ResultSortRequest(),
                cache=ResultSortIndexCache(bad_root),
            )
        self.assertEqual("not a cache directory", bad_root.read_text(encoding="utf-8"))

    def test_first_build_publishes_atomic_index_and_second_build_is_memmapped_hit(self) -> None:
        cache = ResultSortIndexCache(self.cache_root)
        request = ResultSortRequest(sort_key=RESULT_PRIMARY_SORT_KEYS[1])
        first = build_result_sort_index(self.run, request, cache=cache)
        self.assertFalse(first.stats.cache_hit)
        self.assertTrue(first.stats.cache_published)
        self.assertIsNotNone(first.cache_path)
        assert first.cache_path is not None
        self.assertTrue((first.cache_path / RESULT_INDEX_FILENAME).is_file())
        self.assertTrue((first.cache_path / RESULT_INDEX_MANIFEST_NAME).is_file())
        manifest = json.loads((first.cache_path / RESULT_INDEX_MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(first.cache_key, manifest["cacheKey"])
        self.assertEqual(first.row_count * 4, manifest["indexBytes"])
        projection = project_result_sort_cache_entry(first.row_count, request)
        self.assertEqual(
            projection.manifest_bytes,
            (first.cache_path / RESULT_INDEX_MANIFEST_NAME).stat().st_size,
        )
        expected = tuple(int(item) for item in first.row_ordinals)
        _close_index(first)
        second = build_result_sort_index(self.run, request, cache=cache)
        self.assertTrue(second.stats.cache_hit)
        self.assertFalse(second.stats.cache_published)
        self.assertIsInstance(second.row_ordinals, np.memmap)
        self.assertEqual(expected, tuple(int(item) for item in second.row_ordinals))
        self.assertEqual(first.cache_key, second.cache_key)
        _close_index(second)

    def test_cache_key_changes_with_run_view_key_and_direction(self) -> None:
        cache = ResultSortIndexCache(self.cache_root)
        base = build_result_sort_index(self.run, ResultSortRequest(), cache=cache)
        view = build_result_sort_index(self.run, ResultSortRequest(), view=_view((0, 2, 4)), cache=cache)
        ascending = build_result_sort_index(
            self.run,
            ResultSortRequest(direction=ResultSortDirection.ASCENDING),
            cache=cache,
        )
        metric = build_result_sort_index(
            self.run,
            ResultSortRequest(sort_key=RESULT_DERIVED_SORT_KEYS[0]),
            cache=cache,
        )
        self.assertEqual(4, len({base.cache_key, view.cache_key, ascending.cache_key, metric.cache_key}))
        for item in (base, view, ascending, metric):
            _close_index(item)

    def test_corrupt_index_and_manifestless_final_are_invalidated_and_rebuilt(self) -> None:
        request = ResultSortRequest(sort_key=RESULT_DERIVED_SORT_KEYS[2])
        cache = ResultSortIndexCache(self.cache_root)
        first = build_result_sort_index(self.run, request, cache=cache)
        expected = tuple(int(item) for item in first.row_ordinals)
        assert first.cache_path is not None
        index_path = first.cache_path / RESULT_INDEX_FILENAME
        _close_index(first)
        with index_path.open("r+b") as file:
            original = file.read(1)
            file.seek(0)
            file.write(bytes((original[0] ^ 0xFF,)))
        rebuilt = build_result_sort_index(self.run, request, cache=cache)
        self.assertFalse(rebuilt.stats.cache_hit)
        self.assertEqual(expected, tuple(int(item) for item in rebuilt.row_ordinals))
        cache_key = rebuilt.cache_key
        _close_index(rebuilt)

        cache._discard(cache_key)  # exact app-owned fixture entry
        staged = self.cache_root / cache_key
        staged.mkdir()
        (staged / RESULT_INDEX_FILENAME).write_bytes(b"incomplete")
        restored = build_result_sort_index(self.run, request, cache=cache)
        self.assertFalse(restored.stats.cache_hit)
        self.assertEqual(expected, tuple(int(item) for item in restored.row_ordinals))
        self.assertTrue((staged / RESULT_INDEX_MANIFEST_NAME).is_file())
        _close_index(restored)

    def test_injected_prepublication_failure_never_creates_a_completed_entry(self) -> None:
        request = ResultSortRequest(sort_key=RESULT_PRIMARY_SORT_KEYS[0])

        def fail(name: str) -> None:
            if name == "before-cache-publish":
                raise RuntimeError("simulated interruption")

        cache = ResultSortIndexCache(self.cache_root, checkpoint=fail)
        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            build_result_sort_index(self.run, request, cache=cache)
        if self.cache_root.exists():
            self.assertFalse(any(path.name == RESULT_INDEX_MANIFEST_NAME for path in self.cache_root.rglob("*")))
        recovered = build_result_sort_index(
            self.run,
            request,
            cache=ResultSortIndexCache(self.cache_root),
        )
        self.assertFalse(recovered.stats.cache_hit)
        self.assertTrue(recovered.stats.cache_published)
        _close_index(recovered)

    def test_disk_and_entry_budgets_skip_oversized_and_evict_completed_entries(self) -> None:
        tiny_root = self.root / "tiny-cache"
        tiny = ResultSortIndexCache(tiny_root, maximum_bytes=10, maximum_entries=1)
        uncached = build_result_sort_index(self.run, ResultSortRequest(), cache=tiny)
        self.assertFalse(uncached.stats.cache_published)
        self.assertIsNone(uncached.cache_path)
        self.assertFalse(tiny_root.exists())

        bounded = ResultSortIndexCache(self.cache_root, maximum_bytes=10_000, maximum_entries=2)
        indexes = []
        for key in RESULT_PRIMARY_SORT_KEYS[:3]:
            indexes.append(build_result_sort_index(self.run, ResultSortRequest(sort_key=key), cache=bounded))
        completed = [
            path
            for path in self.cache_root.iterdir()
            if path.is_dir() and (path / RESULT_INDEX_MANIFEST_NAME).is_file()
        ]
        self.assertEqual(2, len(completed))
        self.assertLessEqual(
            sum(sum(file.stat().st_size for file in path.iterdir()) for path in completed),
            bounded.maximum_bytes,
        )
        for item in indexes:
            _close_index(item)


if __name__ == "__main__":
    unittest.main()
