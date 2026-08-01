from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.optimizer.cuda.compaction import CudaCompactionHostArray, CudaCompactionHostBatch
from src.optimizer.domain import RESULT_CATEGORY_ORDER
from src.optimizer.result_store import (
    RESULT_COLUMN_NAMES,
    RESULT_ROW_BYTES,
    RESULT_RUN_FORMAT_ID,
    RESULT_RUN_FORMAT_VERSION,
    DenseItemEquippedLookup,
    ResultBatchAdapterError,
    ResultRunError,
    ResultRunStore,
    ResultRunWriterState,
    project_result_run_storage,
    result_columns_from_cpu_rows,
    result_columns_from_cuda_batch,
    validate_result_columns,
)
from src.optimizer.search import (
    create_cartesian_search_space,
    evaluate_exact_build_batch,
    iter_cartesian_batches,
)
from tests.test_cpu_orchestration import CpuOrchestrationTests
from tests.test_result_schema import _valid_columns


def _lookup(size: int = 1000) -> DenseItemEquippedLookup:
    return DenseItemEquippedLookup(tuple(index % 2 == 0 for index in range(size)))


def _cuda_batch(arrays: dict[str, np.ndarray]) -> CudaCompactionHostBatch:
    arrays["category_codes"].fill(0)
    arrays["replacement_distances"].fill(0)
    arrays["constraint_distances"].fill(0)
    count = validate_result_columns(arrays)
    categories = arrays["category_codes"]
    emitted = np.array(
        [np.count_nonzero(categories == index) for index in range(len(RESULT_CATEGORY_ORDER))],
        dtype="<u8",
    )
    values = (
        np.arange(count, dtype="<i8"),
        arrays["dense_item_ids"],
        arrays["owned_set_indices"],
        arrays["category_codes"],
        arrays["replacement_distances"],
        arrays["effective_final_stats"],
        arrays["raw_critical_hit_chances"],
        arrays["derived_metrics"],
        arrays["priority_scores"],
        arrays["constraint_distances"],
        emitted.copy(),
        np.zeros(1, dtype="<u8"),
        np.zeros(1, dtype="<u8"),
        np.zeros(1, dtype="<u8"),
        np.zeros(3, dtype="<u8"),
        emitted,
    )
    names = (
        "flat_indices",
        "dense_item_ids",
        "set_indices",
        "category_codes",
        "replacement_distances",
        "effective_final_stats",
        "raw_critical_hit_chances",
        "derived_metrics",
        "priority_scores",
        "constraint_distances",
        "category_candidate_counts",
        "out_of_scope_count",
        "disabled_category_count",
        "hard_bound_rejected_count",
        "tolerance_rejected_counts",
        "emitted_counts",
    )
    return CudaCompactionHostBatch(
        0,
        max(count, 1),
        count,
        0,
        tuple(CudaCompactionHostArray(name, value) for name, value in zip(names, values, strict=True)),
    )


class ResultStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        CpuOrchestrationTests.setUpClass()

    def test_store_construction_and_import_do_not_touch_the_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-parent-") as parent:
            root = Path(parent) / "not-created"
            store = ResultRunStore(root)
            self.assertEqual(root.resolve(), store.root.resolve())
            self.assertFalse(root.exists())

    def test_zero_row_run_publishes_complete_manifest_and_empty_columns(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            store = ResultRunStore(Path(temporary) / "store")
            writer = store.begin_run("zero")
            completed = writer.complete(0)
            self.assertIs(writer.state, ResultRunWriterState.PUBLISHED)
            self.assertEqual(0, completed.row_count)
            self.assertEqual(0, completed.payload_bytes)
            self.assertGreater(completed.manifest_bytes, 0)
            self.assertEqual(completed.manifest_bytes, completed.storage_bytes)
            self.assertEqual((completed,), store.list_completed_runs())
            for name in RESULT_COLUMN_NAMES:
                column = completed.open_column(name)
                self.assertEqual((0, *store.open_run("zero").column_spec(name).row_shape), column.shape)
                self.assertFalse(column.flags.writeable)
            manifest = json.loads((completed.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(RESULT_RUN_FORMAT_ID, manifest["formatId"])
            self.assertEqual(RESULT_RUN_FORMAT_VERSION, manifest["formatVersion"])
            self.assertEqual("completed", manifest["state"])
            self.assertFalse(any(store.incomplete_path.iterdir()))

    def test_storage_projection_includes_exact_manifest_and_no_raw_column_headers(self) -> None:
        zero = project_result_run_storage("zero", 0)
        one = project_result_run_storage("one", 1)
        cap = project_result_run_storage("cap", 5_000_000)
        self.assertEqual(0, zero.column_header_bytes)
        self.assertEqual(233, one.payload_bytes)
        self.assertEqual(1_165_000_000, cap.payload_bytes)
        self.assertEqual(zero.payload_bytes + zero.manifest_bytes, zero.published_bytes)
        self.assertEqual(one.published_bytes, one.transaction_peak_bytes)
        self.assertEqual(cap.payload_bytes + cap.manifest_bytes, cap.transaction_peak_bytes)
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            store = ResultRunStore(temporary)
            zero_run = store.begin_run("zero").complete()
            one_writer = store.begin_run("one")
            one_writer.append(0, _valid_columns(1))
            one_run = one_writer.complete()
            self.assertEqual(zero.manifest_bytes, zero_run.manifest_bytes)
            self.assertEqual(one.manifest_bytes, one_run.manifest_bytes)

    def test_single_and_multiple_batches_round_trip_exact_bytes_and_binary32_bits(self) -> None:
        first = _valid_columns(2)
        second = _valid_columns(3)
        second["dense_item_ids"] += 100
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            store = ResultRunStore(temporary)
            writer = store.begin_run("round-trip", maximum_rows=10)
            self.assertEqual(2, writer.append(0, first))
            self.assertEqual(5, writer.append(2, second))
            completed = writer.complete(5)
            self.assertEqual(5 * RESULT_ROW_BYTES, completed.payload_bytes)
            self.assertEqual(completed.payload_bytes + completed.manifest_bytes, completed.storage_bytes)
            verified = store.open_run("round-trip", verify_hashes=True)
            for name in RESULT_COLUMN_NAMES:
                expected = np.concatenate((first[name], second[name]), axis=0)
                actual = verified.open_column(name)
                self.assertIsInstance(actual, np.memmap)
                self.assertEqual(expected.dtype.str, actual.dtype.str)
                self.assertEqual(expected.tobytes(), actual.tobytes())
                actual._mmap.close()
            expected_bits = np.concatenate((first["priority_scores"], second["priority_scores"])).view("<u4")
            priority_map = verified.open_column("priority_scores")
            actual_bits = priority_map.view("<u4")
            np.testing.assert_array_equal(expected_bits, actual_bits)
            priority_map._mmap.close()

    def test_append_order_cap_schema_errors_and_terminal_states_never_publish(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            store = ResultRunStore(temporary)
            writer = store.begin_run("bad-order", maximum_rows=2)
            with self.assertRaisesRegex(ResultRunError, "noncontiguous-append"):
                writer.append(1, _valid_columns(1))
            self.assertIs(writer.state, ResultRunWriterState.FAILED)
            with self.assertRaisesRegex(ResultRunError, "writer-not-open"):
                writer.append(0, _valid_columns(1))
            writer.abort("invalid-order")
            writer.abort("idempotent")
            self.assertIs(writer.state, ResultRunWriterState.ABORTED)
            self.assertEqual((), store.list_completed_runs())

            overflow = store.begin_run("overflow", maximum_rows=1)
            with self.assertRaisesRegex(ResultRunError, "result-cap-overflow"):
                overflow.append(0, _valid_columns(2))
            overflow.abort("cap+1")
            self.assertFalse((store.runs_path / "overflow").exists())

            mismatch = store.begin_run("mismatch")
            mismatch.append(0, _valid_columns(1))
            with self.assertRaisesRegex(ResultRunError, "terminal-row-count-mismatch"):
                mismatch.complete(0)
            mismatch.abort("terminal-mismatch")

            published = store.begin_run("published")
            published.complete()
            with self.assertRaisesRegex(ResultRunError, "published-run-abort"):
                published.abort()
            with self.assertRaisesRegex(ResultRunError, "writer-not-open"):
                published.append(0, _valid_columns(1))

    def test_context_exception_cancellation_and_crash_remnants_are_invisible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            store = ResultRunStore(temporary)
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                with store.begin_run("cancelled") as writer:
                    writer.append(0, _valid_columns(1))
                    raise RuntimeError("cancelled")
            self.assertEqual((), store.list_completed_runs())
            self.assertFalse(any(store.incomplete_path.iterdir()))

            crashed = store.begin_run("crashed")
            crashed.append(0, _valid_columns(1))
            crashed._close_files()
            second_process_view = ResultRunStore(temporary)
            self.assertEqual((), second_process_view.list_completed_runs())
            with self.assertRaisesRegex(ResultRunError, "run-writer-active"):
                second_process_view.begin_run("crashed")
            crashed.abort("test-cleanup")

    def test_simulated_failure_at_every_append_and_publish_checkpoint_is_not_visible(self) -> None:
        checkpoints = (
            "before-batch-write",
            "after-column-write:dense_item_ids",
            "after-batch-write",
            "after-column-fsync",
            "after-column-verification",
            "after-manifest-fsync",
            "before-directory-stage",
            "before-manifest-publish",
        )
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            store = ResultRunStore(temporary)
            for index, target in enumerate(checkpoints):
                with self.subTest(checkpoint=target):
                    def fail(name: str, *, selected: str = target) -> None:
                        if name == selected:
                            raise RuntimeError(f"crash at {name}")

                    writer = store.begin_run(f"fault-{index}", checkpoint=fail)
                    if target in {"before-batch-write", "after-column-write:dense_item_ids", "after-batch-write"}:
                        with self.assertRaisesRegex(RuntimeError, "crash at"):
                            writer.append(0, _valid_columns(1))
                    else:
                        writer.append(0, _valid_columns(1))
                        with self.assertRaisesRegex(RuntimeError, "crash at"):
                            writer.complete(1)
                    self.assertIs(writer.state, ResultRunWriterState.FAILED)
                    self.assertNotIn(f"fault-{index}", tuple(run.run_id for run in store.list_completed_runs()))
                    self.assertFalse(any(store.root.rglob("manifest.json")))
                    writer.abort("fault-cleanup")

    def test_concurrent_writer_lock_and_run_id_path_safety(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            store = ResultRunStore(temporary)
            writer = store.begin_run("same")
            with self.assertRaisesRegex(ResultRunError, "run-writer-active"):
                store.begin_run("same")
            writer.abort()
            replacement = store.begin_run("same")
            replacement.complete()
            with self.assertRaisesRegex(ResultRunError, "run-already-exists"):
                store.begin_run("same")
            for invalid in ("", ".", "..", "../escape", "a/b", "a\\b", " space", "x" * 129):
                with self.subTest(run_id=invalid):
                    with self.assertRaisesRegex(ResultRunError, "invalid-run-id"):
                        store.begin_run(invalid)

    def test_symlinked_or_reparse_internal_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            root = Path(temporary) / "store"
            root.mkdir()
            with patch(
                "src.optimizer.result_store.storage._is_linklike",
                side_effect=lambda path: path.name == ".incomplete",
            ):
                with self.assertRaisesRegex(ResultRunError, "unsafe-storage-link"):
                    ResultRunStore(root).begin_run("unsafe")

    def test_corrupt_truncated_wrong_version_and_digest_runs_are_rejected_and_not_listed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-store-") as temporary:
            store = ResultRunStore(temporary)

            bad_json = store.begin_run("bad-json")
            bad_json.complete()
            (store.runs_path / "bad-json" / "manifest.json").write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ResultRunError, "invalid-manifest-json"):
                store.open_run("bad-json")

            wrong_version = store.begin_run("wrong-version")
            version_run = wrong_version.complete()
            manifest_path = version_run.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["formatVersion"] = 2
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ResultRunError, "manifest-identity"):
                store.open_run("wrong-version")

            truncated = store.begin_run("truncated")
            truncated_run = truncated.complete()
            dense_path = truncated_run.column_spec("dense_item_ids").path
            dense_path.write_bytes(b"x")
            with self.assertRaisesRegex(ResultRunError, "column-file-size"):
                store.open_run("truncated")

            digest = store.begin_run("digest")
            digest.append(0, _valid_columns(1))
            digest_run = digest.complete()
            metric_path = digest_run.column_spec("derived_metrics").path
            payload = bytearray(metric_path.read_bytes())
            payload[0] ^= 0xFF
            metric_path.write_bytes(payload)
            store.open_run("digest", verify_hashes=False)
            with self.assertRaisesRegex(ResultRunError, "column-file-digest"):
                store.open_run("digest", verify_hashes=True)

            self.assertEqual(("digest",), tuple(run.run_id for run in store.list_completed_runs()))

    def test_cpu_adapter_uses_canonical_rows_and_checked_equipped_snapshot(self) -> None:
        source = CpuOrchestrationTests("test_full_execution_matches_lower_exact_layers")
        _, slot_arrays, exact, _ = source._fixture()
        space = create_cartesian_search_space(slot_arrays)
        boundary = next(iter_cartesian_batches(space, space.total_permutations))
        result = evaluate_exact_build_batch(exact, slot_arrays, boundary)
        lookup = _lookup(slot_arrays.total_items)
        columns = result_columns_from_cpu_rows(result.rows, slot_arrays, lookup)
        self.assertEqual(len(result.rows), validate_result_columns(columns))
        expected_counts = tuple(sum(dense_id % 2 == 0 for dense_id in row.dense_ids) for row in result.rows)
        self.assertEqual(expected_counts, tuple(int(value) for value in columns["equipped_item_counts"]))
        self.assertEqual(
            tuple(row.priority_score for row in result.rows),
            tuple(float(value) for value in columns["priority_scores"]),
        )

    def test_cuda_adapter_reuses_compact_arrays_and_only_derives_equipped_counts(self) -> None:
        source = _valid_columns(3)
        batch = _cuda_batch(source)
        columns = result_columns_from_cuda_batch(batch, _lookup())
        self.assertEqual(3, validate_result_columns(columns))
        self.assertIs(batch.array("dense_item_ids"), columns["dense_item_ids"])
        self.assertIs(batch.array("set_indices"), columns["owned_set_indices"])
        self.assertIs(batch.array("priority_scores"), columns["priority_scores"])
        self.assertEqual((3, 3, 3), tuple(int(value) for value in columns["equipped_item_counts"]))

        partial_values = []
        for item in batch.arrays:
            values = item.values
            if item.name in {
                "flat_indices",
                "dense_item_ids",
                "set_indices",
                "category_codes",
                "replacement_distances",
                "effective_final_stats",
                "raw_critical_hit_chances",
                "derived_metrics",
                "priority_scores",
                "constraint_distances",
            }:
                values = values[:2]
            partial_values.append(CudaCompactionHostArray(item.name, values))
        partial = CudaCompactionHostBatch(0, 3, 3, 0, tuple(partial_values))
        with self.assertRaisesRegex(ResultBatchAdapterError, "partial-cuda-transfer"):
            result_columns_from_cuda_batch(partial, _lookup())

    def test_ownership_lookup_requires_contiguous_boolean_snapshot_and_known_ids(self) -> None:
        lookup = DenseItemEquippedLookup.from_pairs(((0, True), (1, False), (2, True), (3, False), (4, True), (5, False)))
        dense = np.arange(6, dtype="<i4").reshape(1, 6)
        self.assertEqual((3,), tuple(int(value) for value in lookup.counts(dense)))
        for invalid in (
            ((1, True),),
            ((0, 1),),
            ((0, True), (2, False)),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ResultBatchAdapterError):
                    DenseItemEquippedLookup.from_pairs(invalid)
        with self.assertRaisesRegex(ResultBatchAdapterError, "unknown-dense-item-id"):
            lookup.counts(np.array([[0, 1, 2, 3, 4, 6]], dtype="<i4"))


if __name__ == "__main__":
    unittest.main()
