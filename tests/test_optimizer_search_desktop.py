from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.desktop import PROTOCOL_VERSION
from src.desktop.optimizer_inventory_service import OptimizerInventoryService
from src.desktop.optimizer_profile_service import OptimizerProfileService
from src.desktop.optimizer_search_controller import (
    OptimizerSearchBusyError,
    OptimizerSearchController,
)
from src.desktop.optimizer_search_service import (
    OptimizerSearchExecution,
    OptimizerSearchService,
    OptimizerSearchServiceError,
)
from src.desktop.protocol import dispatch_message
from src.optimizer.cuda.runtime import (
    CudaDiagnosticStatus,
    CudaExecutionMode,
    CudaRuntimeDiagnostic,
    diagnose_cuda_runtime,
)
from src.optimizer.domain import GEAR_SLOT_ORDER, MAX_RESULT_CAP, GearSet
from src.optimizer.search import run_exact_cpu_search
from tests.test_cpu_orchestration import _gear_row


def _wait_terminal(controller: OptimizerSearchController, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = controller.get_snapshot()
        if snapshot["state"] in {"completed", "overflowed", "cancelled", "failed"}:
            return snapshot
        time.sleep(0.005)
    raise AssertionError(f"optimizer search did not finish: {controller.get_snapshot()}")


def _import_one_exact_build(root: Path) -> None:
    rows = []
    for index, slot in enumerate(GEAR_SLOT_ORDER):
        gear_set = GearSet.SPEED if index < 4 else GearSet.HEALTH
        rows.append(_gear_row(f"private-{index}", slot, gear_set))
    source = root / "private-gear.txt"
    source.write_text(json.dumps({"items": rows}), encoding="utf-8")
    OptimizerInventoryService(root).import_file(source)


def _ready_cuda_diagnostic() -> CudaRuntimeDiagnostic:
    return CudaRuntimeDiagnostic(
        status=CudaDiagnosticStatus.READY,
        mode=CudaExecutionMode.CUDA,
        available=True,
        disabled=False,
        summary="CUDA test device is ready.",
        cupy_version="13.0.0",
        device_count=1,
        selected_device_index=0,
        device_name="Synthetic CUDA device",
        free_vram_bytes=8_000_000_000,
        total_vram_bytes=16_000_000_000,
        driver_version=12_000,
        runtime_version=12_000,
        allocation_probe_bytes=1,
        allocation_probe_succeeded=True,
    )


class _CudaLease:
    def __enter__(self):
        return "synthetic-device-inputs"

    def __exit__(self, _kind, _error, _traceback):
        return None


class OptimizerSearchServiceTests(unittest.TestCase):
    def test_real_cpu_search_publishes_only_a_completed_result_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _import_one_exact_build(root)
            profiles = OptimizerProfileService(root)
            hero_id = profiles.search_heroes("Ras", 1)["results"][0]["heroId"]
            draft = profiles.load_draft(hero_id)["draft"]
            service = OptimizerSearchService(
                root,
                profile_service=profiles,
                cuda_diagnostic=lambda: diagnose_cuda_runtime(disabled=True),
                cpu_batch_size=1,
                result_write_batch_size=1,
            )
            progress = []

            prepared = service.prepare(draft, "request.desktop-test", lambda: False)
            outcome = service.run(
                prepared,
                "run-desktop-test",
                lambda: False,
                lambda *values: progress.append(values),
            )

            self.assertEqual("cpu", prepared.backend)
            self.assertEqual("completed", outcome.state)
            self.assertEqual((1, 0, 0), outcome.category_counts)
            self.assertEqual("run-desktop-test", outcome.result_run_id)
            completed = service.result_store.open_run("run-desktop-test", verify_hashes=True)
            self.assertEqual(1, completed.row_count)
            self.assertTrue(progress)
            self.assertFalse(profiles.profile_directory.exists())
            encoded = json.dumps(outcome.__dict__ if hasattr(outcome, "__dict__") else {
                "state": outcome.state,
                "run": outcome.result_run_id,
            })
            self.assertNotIn("private-0", encoded)
            self.assertNotIn(str(root), encoded)

    def test_injected_cuda_success_projects_progress_without_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _import_one_exact_build(root)
            profiles = OptimizerProfileService(root)
            hero_id = profiles.search_heroes("Ras", 1)["results"][0]["heroId"]
            draft = profiles.load_draft(hero_id)["draft"]
            runner_calls = []

            def cuda_runner(device_inputs, diagnostic, counting_context, **kwargs):
                runner_calls.append((device_inputs, diagnostic.status, counting_context.request_id))
                kwargs["on_progress"](SimpleNamespace(
                    total_permutations=1,
                    evaluated_permutations=1,
                    accepted_category_counts=(0, 0, 0),
                    elapsed_seconds=0.25,
                ))
                return SimpleNamespace(
                    completed=True,
                    failed=False,
                    overflowed=False,
                    total_permutations=1,
                    evaluated_permutations=1,
                    counting=SimpleNamespace(category_counts=(0, 0, 0)),
                    elapsed_seconds=0.25,
                    batches=(),
                )

            service = OptimizerSearchService(
                root,
                profile_service=profiles,
                cuda_diagnostic=_ready_cuda_diagnostic,
                cuda_input_compiler=lambda _slots, _context: "synthetic-host-inputs",
                cuda_transfer=lambda host, diagnostic: _CudaLease(),
                cuda_runner=cuda_runner,
            )
            progress = []
            prepared = service.prepare(draft, "request.cuda-test", lambda: False)
            outcome = service.run(
                prepared,
                "run-cuda-test",
                lambda: False,
                lambda *values: progress.append(values),
            )

            self.assertEqual("cuda", prepared.backend)
            self.assertEqual("completed", outcome.state)
            self.assertEqual("run-cuda-test", outcome.result_run_id)
            self.assertEqual(("cuda", 1, 1, (0, 0, 0), 0.25), progress[-1])
            self.assertEqual("synthetic-device-inputs", runner_calls[0][0])
            self.assertEqual(0, service.result_store.open_run("run-cuda-test").row_count)

    def test_injected_cuda_exception_preserves_safe_terminal_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _import_one_exact_build(root)
            profiles = OptimizerProfileService(root)
            hero_id = profiles.search_heroes("Ras", 1)["results"][0]["heroId"]
            draft = profiles.load_draft(hero_id)["draft"]

            def failing_runner(_device, _diagnostic, _counting, **kwargs):
                kwargs["on_progress"](SimpleNamespace(
                    total_permutations=1,
                    evaluated_permutations=1,
                    accepted_category_counts=(1, 0, 0),
                    elapsed_seconds=0.5,
                ))
                raise RuntimeError("C:\\private\\driver.dll")

            service = OptimizerSearchService(
                root,
                profile_service=profiles,
                cuda_diagnostic=_ready_cuda_diagnostic,
                cuda_input_compiler=lambda _slots, _context: "synthetic-host-inputs",
                cuda_transfer=lambda _host, _diagnostic: _CudaLease(),
                cuda_runner=failing_runner,
            )
            prepared = service.prepare(draft, "request.cuda-failure", lambda: False)
            outcome = service.run(
                prepared,
                "run-cuda-failure",
                lambda: False,
                lambda *_values: None,
            )

            self.assertEqual("failed", outcome.state)
            self.assertEqual(1, outcome.searched_permutations)
            self.assertEqual((1, 0, 0), outcome.category_counts)
            self.assertEqual("cuda-search", outcome.failure_stage)
            self.assertIsNone(outcome.result_run_id)
            self.assertFalse((service.result_store.runs_path / "run-cuda-failure").exists())

    def test_cancel_during_result_staging_aborts_every_owned_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _import_one_exact_build(root)
            profiles = OptimizerProfileService(root)
            hero_id = profiles.search_heroes("Ras", 1)["results"][0]["heroId"]
            draft = profiles.load_draft(hero_id)["draft"]
            service = OptimizerSearchService(
                root,
                profile_service=profiles,
                cuda_diagnostic=lambda: diagnose_cuda_runtime(disabled=True),
            )
            prepared = service.prepare(draft, "request.staging-cancel", lambda: False)
            cpu_result = run_exact_cpu_search(
                prepared.slot_arrays,
                prepared.evaluation_context,
                prepared.counting_context,
                batch_size=1,
                should_cancel=lambda: False,
            )
            service.cpu_runner = lambda *_args, **_kwargs: cpu_result

            outcome = service.run(
                prepared,
                "run-staging-cancel",
                lambda: True,
                lambda *_values: None,
            )

            self.assertEqual("cancelled", outcome.state)
            self.assertIsNone(outcome.result_run_id)
            self.assertEqual([], list(service.result_store.incomplete_path.iterdir()))
            self.assertEqual([], list(service.result_store.locks_path.iterdir()))
            self.assertEqual([], list(service.result_store.runs_path.iterdir()))

    def test_restart_cleanup_removes_only_stale_owned_transactions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = OptimizerSearchService(root)
            writer = service.result_store.begin_run("run-stale-owned")
            writer._close_files()  # Simulate the prior backend process releasing OS handles.
            stale = time.time() - (2 * 24 * 60 * 60)
            os.utime(writer.temporary_path, (stale, stale))
            os.utime(writer.lock_path, (stale, stale))

            restarted = OptimizerSearchService(root)
            removed = restarted.cleanup_stale_results()

            self.assertEqual(2, removed)
            self.assertFalse(writer.temporary_path.exists())
            self.assertFalse(writer.lock_path.exists())

    def test_missing_inventory_and_empty_filtered_slot_are_actionable_and_path_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = OptimizerProfileService(root)
            hero_id = profiles.search_heroes("Ras", 1)["results"][0]["heroId"]
            draft = profiles.load_draft(hero_id)["draft"]
            service = OptimizerSearchService(
                root,
                profile_service=profiles,
                cuda_diagnostic=lambda: diagnose_cuda_runtime(disabled=True),
            )
            with self.assertRaises(OptimizerSearchServiceError) as missing:
                service.prepare(draft, "request.missing", lambda: False)
            self.assertEqual("inventory-missing", missing.exception.code)
            self.assertNotIn(str(root), str(missing.exception))

            rows = [
                _gear_row(f"private-{index}", slot, GearSet.SPEED)
                for index, slot in enumerate(GEAR_SLOT_ORDER[:-1])
            ]
            source = root / "five-pieces.txt"
            source.write_text(json.dumps({"items": rows}), encoding="utf-8")
            OptimizerInventoryService(root).import_file(source)
            with self.assertRaises(OptimizerSearchServiceError) as empty:
                service.prepare(draft, "request.empty-slot", lambda: False)
            self.assertEqual("empty-search-slots", empty.exception.code)
            self.assertIn("slot empty", str(empty.exception))
            self.assertNotIn(str(root), str(empty.exception))

    def test_decimal_and_large_ui_bounds_compile_to_the_binary32_search_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _import_one_exact_build(root)
            profiles = OptimizerProfileService(root)
            hero_id = profiles.search_heroes("Ras", 1)["results"][0]["heroId"]
            draft = profiles.load_draft(hero_id)["draft"]
            draft["primaryStats"]["attack"]["minimum"] = 1200.1
            service = OptimizerSearchService(
                root,
                profile_service=profiles,
                cuda_diagnostic=lambda: diagnose_cuda_runtime(disabled=True),
            )

            prepared = service.prepare(draft, "request.binary32-bounds", lambda: False)

            self.assertEqual("cpu", prepared.backend)
            self.assertGreater(prepared.total_permutations, 0)


class _FakeSearchService:
    def __init__(self, mode: str = "complete") -> None:
        self.mode = mode
        self.run_calls: list[tuple[str, bool]] = []

    def prepare(self, _draft, request_id, should_cancel):
        if should_cancel():
            from src.desktop.optimizer_search_service import OptimizerSearchCancelled
            raise OptimizerSearchCancelled()
        if self.mode == "private-preparation-failure":
            raise OptimizerSearchServiceError(
                "inventory",
                "inventory-unavailable",
                "C:\\Users\\Private\\optimizer.db could not be opened.\nprivate detail",
            )
        return SimpleNamespace(
            request=SimpleNamespace(request_id=request_id),
            backend="cuda" if self.mode == "cuda-failure" else "cpu",
            total_permutations=10,
        )

    def run(self, prepared, run_id, should_cancel, on_progress, *, force_cpu=False):
        self.run_calls.append((run_id, force_cpu))
        backend = "cpu" if force_cpu else prepared.backend
        if self.mode == "at-cap":
            on_progress(backend, MAX_RESULT_CAP, MAX_RESULT_CAP, (2_000_000, 2_000_000, 1_000_000), 1.0)
            return OptimizerSearchExecution(
                "completed",
                MAX_RESULT_CAP,
                MAX_RESULT_CAP,
                (2_000_000, 2_000_000, 1_000_000),
                1.0,
                result_run_id=run_id,
            )
        if self.mode == "overflow":
            on_progress(backend, 6_000_000, MAX_RESULT_CAP + 1, (2_000_000, 2_000_000, 1_000_001), 0.8)
            return OptimizerSearchExecution(
                "overflowed",
                6_000_000,
                MAX_RESULT_CAP + 1,
                (2_000_000, 2_000_000, 1_000_001),
                0.8,
            )
        for searched in (2, 4, 6, 8, 10):
            on_progress(backend, 10, searched, (searched, 0, 0), searched / 10)
            if should_cancel():
                return OptimizerSearchExecution("cancelled", 10, searched, (searched, 0, 0), searched / 10)
            if self.mode == "blocking":
                time.sleep(0.01)
        if self.mode == "cuda-failure" and not force_cpu:
            return OptimizerSearchExecution(
                "failed", 10, 10, (3, 2, 1), 1.0,
                failure_stage="kernel/PRIVATE/PATH",
                failure_code="launch-failed",
            )
        return OptimizerSearchExecution(
            "completed", 10, 10, (10, 0, 0), 1.0,
            result_run_id=run_id,
        )


class OptimizerSearchControllerTests(unittest.TestCase):
    def test_single_flight_progress_coalescing_and_terminal_event(self) -> None:
        events = []
        ticks = iter((0.0, 0.01, 0.02, 0.03, 0.04, 0.2, 0.21, 0.22, 0.23))
        service = _FakeSearchService("blocking")
        controller = OptimizerSearchController(
            service, events.append,
            clock=lambda: next(ticks, 1.0),
            event_interval_seconds=0.1,
            job_id_factory=lambda: "job-one",
            request_id_factory=lambda: "request-one",
            run_id_factory=lambda: "run-one",
        )
        started = controller.start({"private": "draft"})
        with self.assertRaises(OptimizerSearchBusyError):
            controller.start({"private": "second"})
        terminal = _wait_terminal(controller)

        self.assertEqual("completed", terminal["state"])
        self.assertEqual("10", terminal["searchedPermutations"])
        self.assertEqual("10", terminal["categoryCounts"]["exact"])
        self.assertTrue(terminal["resultAvailable"])
        self.assertEqual("run-one", terminal["resultRunId"])
        self.assertGreater(terminal["sequence"], started["sequence"])
        self.assertLess(len(events), 8)
        self.assertEqual("completed", events[-1]["state"])
        self.assertNotIn("rows", json.dumps(events))

    def test_cancel_is_idempotent_and_overflow_never_publishes_a_result(self) -> None:
        service = _FakeSearchService("blocking")
        controller = OptimizerSearchController(service, event_interval_seconds=0)
        started = controller.start({})
        first = controller.cancel(started["jobId"])
        second = controller.cancel(started["jobId"])
        terminal = _wait_terminal(controller)
        self.assertEqual(first, second)
        self.assertEqual("cancelled", terminal["state"])
        self.assertFalse(terminal["resultAvailable"])

        overflow = OptimizerSearchController(
            _FakeSearchService("overflow"),
            job_id_factory=lambda: "job-overflow",
        )
        overflow.start({})
        outcome = _wait_terminal(overflow)
        self.assertEqual("overflowed", outcome["state"])
        self.assertEqual(
            {"exact": "2000000", "oneAway": "2000000", "twoAway": "1000001"},
            outcome["categoryCounts"],
        )
        self.assertEqual(str(MAX_RESULT_CAP + 1), outcome["searchedPermutations"])
        self.assertIsNone(outcome["resultRunId"])

    def test_exact_cap_completion_remains_publishable(self) -> None:
        controller = OptimizerSearchController(
            _FakeSearchService("at-cap"),
            run_id_factory=lambda: "run-exact-cap",
        )
        controller.start({})
        outcome = _wait_terminal(controller)
        self.assertEqual("completed", outcome["state"])
        self.assertEqual(str(MAX_RESULT_CAP), outcome["searchedPermutations"])
        self.assertEqual(str(MAX_RESULT_CAP), str(sum(
            int(value) for value in outcome["categoryCounts"].values()
        )))
        self.assertEqual("run-exact-cap", outcome["resultRunId"])

    def test_close_waits_for_the_worker_to_observe_cancellation(self) -> None:
        controller = OptimizerSearchController(_FakeSearchService("blocking"))
        controller.start({})
        controller.close(timeout_seconds=1.0)
        self.assertEqual("cancelled", controller.get_snapshot()["state"])

    def test_preparation_failure_cannot_expose_a_private_path(self) -> None:
        controller = OptimizerSearchController(_FakeSearchService("private-preparation-failure"))
        controller.start({})
        outcome = _wait_terminal(controller)
        self.assertEqual("failed", outcome["state"])
        encoded = json.dumps(outcome)
        self.assertNotIn("Users", encoded)
        self.assertNotIn("optimizer.db", encoded)
        self.assertEqual(
            "The optimizer search stopped safely. No partial results were kept.",
            outcome["failure"]["message"],
        )

    def test_cuda_failure_is_sanitized_and_explicit_cpu_retry_starts_fresh(self) -> None:
        service = _FakeSearchService("cuda-failure")
        identifiers = iter(("gpu-job", "cpu-job"))
        runs = iter(("gpu-run", "cpu-run"))
        controller = OptimizerSearchController(
            service,
            job_id_factory=lambda: next(identifiers),
            request_id_factory=lambda: "request-recovery",
            run_id_factory=lambda: next(runs),
        )
        controller.start({})
        failed = _wait_terminal(controller)
        self.assertEqual("failed", failed["state"])
        self.assertTrue(failed["failure"]["cpuRecoveryAvailable"])
        self.assertEqual("search", failed["failure"]["stage"])
        self.assertNotIn("PRIVATE", json.dumps(failed))

        retried = controller.retry_cpu("gpu-job")
        self.assertEqual("cpu", retried["backend"])
        completed = _wait_terminal(controller)
        self.assertEqual("completed", completed["state"])
        self.assertEqual("cpu-job", completed["jobId"])
        self.assertEqual("request-recovery", completed["requestId"])
        self.assertEqual("cpu-run", completed["resultRunId"])
        self.assertEqual([("gpu-run", False), ("cpu-run", True)], service.run_calls)


class _FakeSearchController:
    def __init__(self) -> None:
        self.calls = []
        self.snapshot = {
            "sequence": 0, "jobId": None, "requestId": None, "state": "idle",
            "backend": None, "totalPermutations": "0", "searchedPermutations": "0",
            "categoryCounts": {"exact": "0", "oneAway": "0", "twoAway": "0"},
            "elapsedSeconds": 0.0, "canCancel": False, "resultAvailable": False,
            "resultRunId": None, "failure": None,
        }

    def get_snapshot(self):
        self.calls.append(("get", None))
        return self.snapshot

    def start(self, draft):
        self.calls.append(("start", draft))
        return self.snapshot

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))
        return self.snapshot

    def retry_cpu(self, job_id):
        self.calls.append(("retry", job_id))
        return self.snapshot


class OptimizerSearchProtocolTests(unittest.TestCase):
    def test_protocol_exposes_only_get_start_cancel_and_cpu_retry(self) -> None:
        controller = _FakeSearchController()
        cases = (
            ("optimizer.search.get", {}, ("get", None)),
            ("optimizer.search.start", {"draft": {"heroId": "private"}}, ("start", {"heroId": "private"})),
            ("optimizer.search.cancel", {"jobId": "job"}, ("cancel", "job")),
            ("optimizer.search.retry-cpu", {"jobId": "job"}, ("retry", "job")),
        )
        for index, (method, params, call) in enumerate(cases):
            response = dispatch_message(
                {"protocol": PROTOCOL_VERSION, "id": str(index), "method": method, "params": params},
                optimizer_search_controller=controller,
            )
            self.assertTrue(response["ok"])
            self.assertEqual(call, controller.calls[-1])
        encoded = json.dumps([dispatch_message(
            {"protocol": PROTOCOL_VERSION, "id": "get", "method": "optimizer.search.get", "params": {}},
            optimizer_search_controller=controller,
        )])
        self.assertNotIn("rows", encoded)
        self.assertNotIn("itemIds", encoded)

    def test_protocol_rejects_extra_fields_and_missing_service(self) -> None:
        controller = _FakeSearchController()
        invalid = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "bad",
                "method": "optimizer.search.start",
                "params": {"draft": {}, "sourcePath": "C:/private"},
            },
            optimizer_search_controller=controller,
        )
        missing = dispatch_message({
            "protocol": PROTOCOL_VERSION,
            "id": "missing",
            "method": "optimizer.search.get",
            "params": {},
        })
        self.assertEqual("invalid_params", invalid["error"]["code"])
        self.assertEqual("service_unavailable", missing["error"]["code"])
        self.assertEqual([], controller.calls)


if __name__ == "__main__":
    unittest.main()
