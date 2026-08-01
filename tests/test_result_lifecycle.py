from __future__ import annotations

import ast
import csv
import hashlib
import inspect
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.optimizer.result_store import (
    RESULT_EXPORT_FIELD_NAMES,
    RESULT_EXPORT_ID,
    RESULT_EXPORT_VERSION,
    RESULT_DERIVED_METRIC_ORDER,
    RESULT_LIFECYCLE_ID,
    RESULT_LIFECYCLE_VERSION,
    RESULT_REPRODUCIBILITY_FILENAME,
    CompletedResultRun,
    FilteredResultView,
    LifecycleArtifactKind,
    LifecycleDisposition,
    ResultDataVersionEvidence,
    ResultExecutionBackend,
    ResultExecutionEvidence,
    ResultExportError,
    ResultExportFormat,
    ResultExportRequest,
    ResultFilterExecutionStats,
    ResultLifecycleError,
    ResultLifecycleManager,
    ResultLifecyclePolicy,
    ResultLifecycleRequest,
    ResultRunStore,
    ResultSortIndexCache,
    ResultSortRequest,
    build_result_reproducibility_record,
    build_result_sort_index,
    create_base_export_view,
    create_filtered_export_view,
    create_sorted_export_view,
    export_result_view,
    inventory_snapshot_fingerprint,
    load_result_reproducibility,
    persist_result_reproducibility,
    project_result_export,
    search_snapshot_fingerprint,
)
from src.optimizer.result_store import exporting as exporting_module
from src.optimizer.result_store import lifecycle as lifecycle_module
from tests import test_result_resolution as resolution_tests


class ResultLifecycleAndExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        resolution_tests.ResultResolutionTests.setUpClass()

    def setUp(self) -> None:
        self.helper = resolution_tests.ResultResolutionTests(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)

    def _fixture(self, *, row_count: int = 1, run_id: str = "lifecycle-fixture", mutate=None):
        return self.helper._fixture(
            self._exact_sets(),
            row_count=row_count,
            run_id=run_id,
            request_id=f"request.{run_id}",
            mutate_columns=mutate,
        )

    @staticmethod
    def _exact_sets():
        from src.optimizer.domain import GearSet

        return (GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2

    def _request(self, fixture):
        return self.helper._request(request_id=fixture.context.evaluation_context.request_id)

    @staticmethod
    def _data_versions() -> tuple[ResultDataVersionEvidence, ...]:
        return tuple(
            ResultDataVersionEvidence(component, schema, version, f"{index + 1:x}" * 64)
            for index, (component, (schema, version)) in enumerate(
                lifecycle_module.REQUIRED_DATA_VERSION_CONTRACTS.items()
            )
        )

    def _record(self, fixture):
        return build_result_reproducibility_record(
            fixture.run,
            self._request(fixture),
            fixture.context,
            self._data_versions(),
            ResultExecutionEvidence(ResultExecutionBackend.CPU, "python-numpy-reference-v1"),
        )

    @staticmethod
    def _export_request(fixture, view, destination: Path, export_format=ResultExportFormat.CSV, **kwargs):
        return ResultExportRequest(
            fixture.context.session_id,
            fixture.run.run_id,
            view.view_fingerprint,
            destination,
            export_format,
            **kwargs,
        )

    def test_contracts_are_versioned_bounded_and_construction_has_no_io(self) -> None:
        self.assertEqual(1, RESULT_EXPORT_VERSION)
        self.assertEqual(1, RESULT_LIFECYCLE_VERSION)
        self.assertEqual("e7.optimizer.result-export", RESULT_EXPORT_ID)
        self.assertEqual("e7.optimizer.result-lifecycle", RESULT_LIFECYCLE_ID)
        self.assertEqual(43, len(RESULT_EXPORT_FIELD_NAMES))
        projection = project_result_export(5_000_000, 131_072)
        self.assertEqual(131_072, projection.peak_chunk_rows)
        self.assertEqual(233, projection.stored_column_bytes_per_row)
        self.assertEqual(31_064_064, projection.peak_numeric_array_bytes)

        with tempfile.TemporaryDirectory(prefix="e7-lifecycle-construction-") as temporary:
            root = Path(temporary)
            result_root = root / "missing-results"
            cache_root = root / "missing-cache"
            export_root = root / "missing-exports"
            ResultLifecycleManager(result_root, cache_root, export_roots=(export_root,))
            ResultExportRequest("session", "run", "base:fingerprint", root / "result.csv", "csv")
            self.assertFalse(result_root.exists())
            self.assertFalse(cache_root.exists())
            self.assertFalse(export_root.exists())

        invalid = (
            lambda: ResultLifecycleRequest(datetime.now(), lifecycle_id=RESULT_LIFECYCLE_ID),
            lambda: ResultLifecyclePolicy(stale_after_seconds=-1),
            lambda: ResultExecutionEvidence(ResultExecutionBackend.CUDA, "cuda-v1"),
            lambda: project_result_export(1, 0),
        )
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises((ResultLifecycleError, ResultExportError)):
                operation()

    def test_reproducibility_record_pins_every_input_and_persists_atomically(self) -> None:
        fixture = self._fixture()
        record = self._record(fixture)
        manifest = fixture.run.path / "manifest.json"
        manifest_before = manifest.read_bytes()
        checkpoints: list[str] = []
        path = persist_result_reproducibility(fixture.run, record, checkpoint=checkpoints.append)
        self.assertEqual(RESULT_REPRODUCIBILITY_FILENAME, path.name)
        self.assertEqual(["after-reproducibility-fsync"], checkpoints)
        self.assertEqual(manifest_before, manifest.read_bytes())
        self.assertEqual(record, load_result_reproducibility(fixture.run))
        self.assertEqual(path, persist_result_reproducibility(fixture.run, record))
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record.request.to_dict(), payload["request"])
        self.assertEqual(inventory_snapshot_fingerprint(fixture.context.inventory_snapshot), payload["inventorySnapshotSha256"])
        self.assertEqual(search_snapshot_fingerprint(fixture.context), payload["searchSnapshotSha256"])
        self.assertEqual(3, len(payload["dataVersions"]))
        self.assertEqual(5, len(payload["resultContracts"]))
        self.assertEqual(5, len(payload["engineSourceRevisions"]))

    def test_completed_run_and_reproducibility_survive_store_reopen_byte_for_byte(self) -> None:
        fixture = self._fixture(run_id="update-reopen")
        record = self._record(fixture)
        sidecar = persist_result_reproducibility(fixture.run, record)
        manifest = fixture.run.path / "manifest.json"
        manifest_bytes = manifest.read_bytes()
        sidecar_bytes = sidecar.read_bytes()

        reopened = ResultRunStore(fixture.run.path.parent.parent).open_run(
            fixture.run.run_id,
            verify_hashes=True,
        )

        self.assertEqual(fixture.run.row_count, reopened.row_count)
        self.assertEqual(
            tuple(column.sha256 for column in fixture.run.columns),
            tuple(column.sha256 for column in reopened.columns),
        )
        self.assertEqual(record, load_result_reproducibility(reopened))
        self.assertEqual(manifest_bytes, manifest.read_bytes())
        self.assertEqual(sidecar_bytes, sidecar.read_bytes())

    def test_reproducibility_failure_and_tampering_never_publish_valid_evidence(self) -> None:
        fixture = self._fixture(run_id="repro-failure")
        record = self._record(fixture)

        def fail(_name: str) -> None:
            raise OSError("injected")

        with self.assertRaisesRegex(OSError, "injected"):
            persist_result_reproducibility(fixture.run, record, checkpoint=fail)
        self.assertFalse((fixture.run.path / RESULT_REPRODUCIBILITY_FILENAME).exists())
        self.assertEqual([], list(fixture.run.path.glob("*.pending")))

        path = persist_result_reproducibility(fixture.run, record)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["request"]["heroId"] = "hero.changed"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ResultLifecycleError, "reproducibility-digest"):
            load_result_reproducibility(fixture.run)

        malformed = self._fixture(run_id="repro-malformed")
        malformed_path = persist_result_reproducibility(malformed.run, self._record(malformed))
        malformed_data = json.loads(malformed_path.read_text(encoding="utf-8"))
        malformed_data["dataVersions"].pop()
        unsigned = dict(malformed_data)
        unsigned.pop("recordSha256")
        malformed_data["recordSha256"] = hashlib.sha256(
            json.dumps(
                unsigned,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        malformed_path.write_text(
            json.dumps(malformed_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ResultLifecycleError, "data-version-coverage"):
            load_result_reproducibility(malformed.run)

    def test_base_csv_preserves_exact_integer_float_and_owned_item_identity(self) -> None:
        priority_bits = 0x3F800001
        constraint_bits = 0x80000000

        def mutate(columns):
            columns["effective_final_stats"][0] = (9_000_000_001, 2, 3, 4, 5, 6, 7, 8)
            columns["derived_metrics"][0] = tuple(-9_000_000_000 + index for index in range(15))
            columns["priority_scores"][0] = np.asarray([priority_bits], dtype="<u4").view("<f4")[0]
            columns["constraint_distances"][0] = np.asarray([constraint_bits], dtype="<u4").view("<f4")[0]

        fixture = self._fixture(run_id="export-csv", mutate=mutate)
        escaped_id = 'gear,"weapon\nline'
        original_id = fixture.context.slot_arrays.stable_item_id_for_dense_id(
            int(fixture.columns["dense_item_ids"][0, 0])
        )
        renamed_diagnostics = replace(
            fixture.context.slot_arrays.diagnostics,
            decisions=tuple(
                replace(item, stable_item_id=escaped_id)
                if item.stable_item_id == original_id
                else item
                for item in fixture.context.slot_arrays.diagnostics.decisions
            ),
        )
        renamed_arrays = replace(
            fixture.context.slot_arrays,
            diagnostics=renamed_diagnostics,
            dense_id_to_stable_id=tuple(
                (dense_id, escaped_id if stable_id == original_id else stable_id)
                for dense_id, stable_id in fixture.context.slot_arrays.dense_id_to_stable_id
            ),
        )
        renamed_groups = tuple(
            (
                slot,
                tuple(replace(item, item_id=escaped_id) if item.item_id == original_id else item for item in items),
            )
            for slot, items in fixture.context.inventory_snapshot.items_by_slot
        )
        renamed_items = tuple(item for _slot, items in renamed_groups for item in items)
        renamed_snapshot = type(fixture.context.inventory_snapshot)(
            renamed_groups,
            tuple((item.dense_id, item.item_id) for item in renamed_items),
        )
        fixture = replace(
            fixture,
            context=replace(
                fixture.context,
                inventory_snapshot=renamed_snapshot,
                slot_arrays=renamed_arrays,
            ),
        )
        record = self._record(fixture)
        view = create_base_export_view(fixture.run)
        destination = Path(fixture.temporary.name) / "all.csv"
        outcome = export_result_view(
            fixture.run,
            view,
            self._export_request(fixture, view, destination, chunk_rows=1),
            fixture.context,
            record,
        )
        with destination.open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("9000000001", row["primary:final_stat.attack"])
        self.assertEqual("-9000000000", row[f"derived:{RESULT_DERIVED_METRIC_ORDER[0]}"])
        self.assertEqual("3f800001", row["priorityScoreBits"])
        self.assertEqual("80000000", row["constraintDistanceBits"])
        self.assertEqual(escaped_id, row["item:slot.weapon"])
        self.assertEqual("set.speed", row["set:slot.weapon"])
        self.assertEqual(1, outcome.row_count)
        self.assertEqual(1, outcome.chunk_count)
        self.assertEqual(destination.stat().st_size, outcome.file_bytes)
        self.assertEqual(64, len(outcome.sha256))

    def test_json_empty_multichunk_filtered_and_sorted_views_are_exact(self) -> None:
        empty = self._fixture(row_count=0, run_id="export-empty")
        empty_view = create_base_export_view(empty.run)
        empty_path = Path(empty.temporary.name) / "empty.json"
        outcome = export_result_view(
            empty.run,
            empty_view,
            self._export_request(empty, empty_view, empty_path, ResultExportFormat.JSON),
            empty.context,
            self._record(empty),
        )
        self.assertEqual([], json.loads(empty_path.read_text(encoding="utf-8")))
        self.assertEqual(0, outcome.chunk_count)

        def mutate(columns):
            columns["priority_scores"][:] = np.asarray([1.0, 3.0, 2.0], dtype="<f4")
            columns["effective_final_stats"][:, 0] = np.asarray([101, 102, 103], dtype="<i8")

        fixture = self._fixture(row_count=3, run_id="export-views", mutate=mutate)
        record = self._record(fixture)
        filtered = FilteredResultView(
            np.asarray([0, 2], dtype="<u4"),
            ResultFilterExecutionStats(3, 2, 2, 2, 12, 1024),
        )
        filtered_view = create_filtered_export_view(fixture.run, filtered)
        filtered_path = Path(fixture.temporary.name) / "filtered.json"
        filtered_outcome = export_result_view(
            fixture.run,
            filtered_view,
            self._export_request(fixture, filtered_view, filtered_path, ResultExportFormat.JSON, chunk_rows=1),
            fixture.context,
            record,
        )
        self.assertEqual([0, 2], [item["rowOrdinal"] for item in json.loads(filtered_path.read_text(encoding="utf-8"))])
        self.assertEqual(2, filtered_outcome.chunk_count)

        index = build_result_sort_index(fixture.run, ResultSortRequest())
        sorted_view = create_sorted_export_view(fixture.run, index)
        sorted_path = Path(fixture.temporary.name) / "sorted.json"
        export_result_view(
            fixture.run,
            sorted_view,
            self._export_request(fixture, sorted_view, sorted_path, ResultExportFormat.JSON, chunk_rows=2),
            fixture.context,
            record,
        )
        self.assertEqual([1, 2, 0], [item["rowOrdinal"] for item in json.loads(sorted_path.read_text(encoding="utf-8"))])

    def test_export_opens_each_column_once_and_only_gathers_view_chunks(self) -> None:
        fixture = self._fixture(row_count=5, run_id="export-bounded")
        record = self._record(fixture)
        filtered = FilteredResultView(
            np.asarray([3, 4], dtype="<u4"),
            ResultFilterExecutionStats(5, 2, 2, 2, 20, 1024),
        )
        view = create_filtered_export_view(fixture.run, filtered)
        path = Path(fixture.temporary.name) / "bounded.csv"
        calls: list[str] = []
        original = CompletedResultRun.open_column

        def tracked(instance, name):
            calls.append(name)
            return original(instance, name)

        # Corrupt an off-view row. A full-run scan would fail schema validation.
        dense_path = fixture.run.column_spec("dense_item_ids").path
        writable = np.memmap(dense_path, dtype="<i4", mode="r+", shape=(5, 6))
        writable[0, 0] = -1
        writable.flush()
        writable._mmap.close()
        with patch.object(CompletedResultRun, "open_column", tracked):
            outcome = export_result_view(
                fixture.run,
                view,
                self._export_request(fixture, view, path, chunk_rows=1),
                fixture.context,
                record,
            )
            self.assertEqual(10, len(calls))
        self.assertEqual(1, outcome.projection.peak_chunk_rows)
        self.assertEqual(237, outcome.projection.peak_numeric_array_bytes)
        with path.open(encoding="utf-8", newline="") as file:
            self.assertEqual(["3", "4"], [row["rowOrdinal"] for row in csv.DictReader(file)])

    def test_export_cancellation_failure_and_conflict_leave_no_partial_file(self) -> None:
        fixture = self._fixture(row_count=3, run_id="export-atomic")
        record = self._record(fixture)
        view = create_base_export_view(fixture.run)
        root = Path(fixture.temporary.name)

        def fail(name: str) -> None:
            if name == "after-export-chunk:1":
                raise OSError("injected export failure")

        failed = root / "failed.csv"
        with self.assertRaisesRegex(OSError, "injected export failure"):
            export_result_view(
                fixture.run,
                view,
                self._export_request(fixture, view, failed, chunk_rows=1),
                fixture.context,
                record,
                checkpoint=fail,
            )
        self.assertFalse(failed.exists())
        self.assertEqual([], list(root.glob("*.e7-export.tmp")))

        calls = 0

        def cancelled() -> bool:
            nonlocal calls
            calls += 1
            return calls >= 2

        cancelled_path = root / "cancelled.json"
        with self.assertRaisesRegex(ResultExportError, "export-cancelled"):
            export_result_view(
                fixture.run,
                view,
                self._export_request(fixture, view, cancelled_path, ResultExportFormat.JSON),
                fixture.context,
                record,
                cancelled=cancelled,
            )
        self.assertFalse(cancelled_path.exists())

        conflict = root / "conflict.csv"
        conflict.write_text("owned\n", encoding="utf-8")
        with self.assertRaisesRegex(ResultExportError, "export-already-exists"):
            export_result_view(
                fixture.run,
                view,
                self._export_request(fixture, view, conflict),
                fixture.context,
                record,
            )
        self.assertEqual("owned\n", conflict.read_text(encoding="utf-8"))
        export_result_view(
            fixture.run,
            view,
            self._export_request(fixture, view, conflict, overwrite=True),
            fixture.context,
            record,
        )
        self.assertTrue(conflict.read_text(encoding="utf-8").startswith("rowOrdinal,"))

    def test_export_rejects_stale_run_view_session_and_provenance(self) -> None:
        fixture = self._fixture(run_id="export-stale")
        record = self._record(fixture)
        view = create_base_export_view(fixture.run)
        destination = Path(fixture.temporary.name) / "stale.csv"
        cases = (
            replace(self._export_request(fixture, view, destination), session_id="session.other"),
            replace(self._export_request(fixture, view, destination), run_id="run.other"),
            replace(self._export_request(fixture, view, destination), view_fingerprint="base:other"),
        )
        for request in cases:
            with self.subTest(request=request), self.assertRaisesRegex(ResultExportError, "stale-export-authority"):
                export_result_view(fixture.run, view, request, fixture.context, record)
        stale_record = replace(record, inventory_snapshot_sha256="f" * 64)
        with self.assertRaisesRegex(ResultLifecycleError, "stale-reproducibility-evidence"):
            export_result_view(
                fixture.run,
                view,
                self._export_request(fixture, view, destination),
                fixture.context,
                stale_record,
            )
        self.assertFalse(destination.exists())

    def test_lifecycle_dry_run_retention_active_protection_and_real_cleanup(self) -> None:
        fixture = self._fixture(run_id="cleanup-source")
        root = Path(fixture.temporary.name)
        store_root = root / "lifecycle-store"
        store = ResultRunStore(store_root)
        runs = []
        for run_id in ("completed-a", "completed-b", "completed-c"):
            writer = store.begin_run(run_id)
            writer.append(0, fixture.columns)
            runs.append(writer.complete())

        cache_root = root / "cache"
        cache = ResultSortIndexCache(cache_root)
        cached = build_result_sort_index(runs[0], ResultSortRequest(), cache=cache)
        self.assertIsNotNone(cached.cache_path)

        incomplete = store.incomplete_path / f"incomplete.{('a' * 32)}.tmp"
        (incomplete / "columns").mkdir(parents=True)
        staged = store.runs_path / "staged"
        (staged / "columns").mkdir(parents=True)
        lock = store.locks_path / "locked.lock"
        lock.mkdir()
        cache_temp = cache_root / f".{('b' * 64)}.{('c' * 32)}.tmp"
        cache_temp.mkdir()
        export_root = root / "exports"
        export_root.mkdir()
        export_temp = export_root / f".build.csv.{('d' * 32)}.e7-export.tmp"
        export_temp.write_text("partial", encoding="utf-8")
        abandoned_export_temp = export_root / f".old.csv.{('e' * 32)}.e7-export.tmp"
        abandoned_export_temp.write_text("partial", encoding="utf-8")
        unknown = store.incomplete_path / "unknown-artifact"
        unknown.mkdir()
        (unknown / "foreign.txt").write_text("keep", encoding="utf-8")

        manager = ResultLifecycleManager(store_root, cache_root, export_roots=(export_root,))
        request = ResultLifecycleRequest(
            datetime.now(UTC) + timedelta(seconds=1),
            active_run_ids=("completed-a", "incomplete", "locked", "staged"),
            active_index_cache_keys=(cached.cache_key,),
            active_export_temporary_names=(export_temp.name,),
            policy=ResultLifecyclePolicy(stale_after_seconds=0, keep_newest_completed_runs=1),
        )
        dry = manager.clean(request)
        by_path = {item.path: item for item in dry.actions}
        self.assertIs(by_path[runs[0].path].disposition, LifecycleDisposition.PROTECTED_ACTIVE)
        self.assertIs(by_path[runs[-1].path].disposition, LifecycleDisposition.PROTECTED_RETENTION)
        self.assertIs(by_path[incomplete].disposition, LifecycleDisposition.PROTECTED_ACTIVE)
        self.assertIs(by_path[cache_temp].disposition, LifecycleDisposition.ELIGIBLE_DRY_RUN)
        self.assertIs(by_path[export_temp].disposition, LifecycleDisposition.PROTECTED_ACTIVE)
        self.assertIs(by_path[abandoned_export_temp].disposition, LifecycleDisposition.ELIGIBLE_DRY_RUN)
        self.assertTrue(export_temp.exists())
        self.assertGreaterEqual(dry.preserved_unknown_artifacts, 1)

        real = manager.clean(replace(request, active_run_ids=(), active_index_cache_keys=(), active_export_temporary_names=(), dry_run=False))
        self.assertGreaterEqual(real.removed_artifacts, 9)
        self.assertFalse(incomplete.exists())
        self.assertFalse(staged.exists())
        self.assertFalse(lock.exists())
        self.assertFalse(cache_temp.exists())
        self.assertFalse(export_temp.exists())
        self.assertFalse(abandoned_export_temp.exists())
        self.assertTrue(unknown.exists())
        again = manager.clean(replace(request, active_run_ids=(), active_index_cache_keys=(), active_export_temporary_names=(), dry_run=False))
        self.assertEqual(0, again.removed_artifacts)

    def test_lifecycle_preserves_malformed_and_rejects_recognized_links(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-lifecycle-unsafe-") as temporary:
            root = Path(temporary)
            store_root = root / "store"
            cache_root = root / "cache"
            (store_root / ".incomplete").mkdir(parents=True)
            (store_root / ".locks").mkdir()
            (store_root / "runs").mkdir()
            cache_root.mkdir()
            malformed = store_root / ".incomplete" / f"bad.{('a' * 32)}.tmp"
            malformed.mkdir()
            (malformed / "foreign").write_text("keep", encoding="utf-8")
            writer = ResultRunStore(store_root).begin_run("completed-with-foreign-file")
            completed_with_foreign = writer.complete()
            (completed_with_foreign.path / "notes.txt").write_text("user-owned", encoding="utf-8")
            cache_with_foreign = build_result_sort_index(
                completed_with_foreign,
                ResultSortRequest(),
                cache=ResultSortIndexCache(cache_root),
            )
            assert cache_with_foreign.cache_path is not None
            (cache_with_foreign.cache_path / "notes.txt").write_text("user-owned", encoding="utf-8")
            manager = ResultLifecycleManager(store_root, cache_root)
            report = manager.clean(ResultLifecycleRequest(datetime.now(UTC), policy=ResultLifecyclePolicy(0, 0), dry_run=False))
            self.assertTrue(malformed.exists())
            self.assertTrue(completed_with_foreign.path.exists())
            self.assertTrue(cache_with_foreign.cache_path.exists())
            self.assertEqual(3, report.preserved_unknown_artifacts)

            target = root / "target"
            target.mkdir()
            link = store_root / ".incomplete" / f"linked.{('b' * 32)}.tmp"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                link.mkdir()
                original = lifecycle_module._is_linklike
                with patch.object(
                    lifecycle_module,
                    "_is_linklike",
                    side_effect=lambda path: path == link or original(path),
                ), self.assertRaisesRegex(ResultLifecycleError, "unsafe-lifecycle-artifact"):
                    manager.clean(ResultLifecycleRequest(datetime.now(UTC)))
            else:
                with self.assertRaisesRegex(ResultLifecycleError, "unsafe-lifecycle-artifact"):
                    manager.clean(ResultLifecycleRequest(datetime.now(UTC)))
            self.assertTrue(target.exists())

    def test_lifecycle_stale_age_boundary_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-lifecycle-age-") as temporary:
            root = Path(temporary)
            export_root = root / "exports"
            export_root.mkdir()
            boundary = export_root / f".boundary.csv.{('1' * 32)}.e7-export.tmp"
            young = export_root / f".young.csv.{('2' * 32)}.e7-export.tmp"
            active = export_root / f".active.csv.{('3' * 32)}.e7-export.tmp"
            for path in (boundary, young, active):
                path.write_text("partial", encoding="utf-8")
            now = datetime(2030, 1, 1, tzinfo=UTC)
            os.utime(boundary, (now.timestamp() - 10, now.timestamp() - 10))
            os.utime(young, (now.timestamp() - 9, now.timestamp() - 9))
            os.utime(active, (now.timestamp() - 100, now.timestamp() - 100))
            report = ResultLifecycleManager(
                root / "missing-store",
                root / "missing-cache",
                export_roots=(export_root,),
            ).clean(
                ResultLifecycleRequest(
                    now,
                    active_export_temporary_names=(active.name,),
                    policy=ResultLifecyclePolicy(stale_after_seconds=10, keep_newest_completed_runs=0),
                )
            )
            by_path = {item.path: item for item in report.actions}
            self.assertEqual(10, by_path[boundary].age_seconds)
            self.assertIs(by_path[boundary].disposition, LifecycleDisposition.ELIGIBLE_DRY_RUN)
            self.assertEqual(9, by_path[young].age_seconds)
            self.assertIs(by_path[young].disposition, LifecycleDisposition.TOO_YOUNG)
            self.assertIs(by_path[active].disposition, LifecycleDisposition.PROTECTED_ACTIVE)

    def test_modules_have_no_database_gpu_or_hot_row_dependency_and_are_packaged(self) -> None:
        for module in (lifecycle_module, exporting_module):
            source = inspect.getsource(module)
            tree = ast.parse(source)
            imports = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            }
            imports.update(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            self.assertFalse(any(name.startswith(("cupy", "sqlite3")) for name in imports))
            self.assertNotIn("InventoryRepository", source)
            self.assertNotIn("optimizer.db", source)
            self.assertNotIn("user_data", source)
        exporting_source = inspect.getsource(exporting_module)
        self.assertNotIn("ResolvedResultRow", exporting_source)
        self.assertNotIn("resolve_result_page", exporting_source)
        spec = Path("packaging/e7-core.spec").read_text(encoding="utf-8")
        self.assertIn('"src.optimizer.result_store.lifecycle"', spec)
        self.assertIn('"src.optimizer.result_store.exporting"', spec)


if __name__ == "__main__":
    unittest.main()
