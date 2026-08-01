from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.optimizer.cuda.inputs import CudaDeviceInputs
from src.optimizer.cuda.orchestration import (
    CudaSearchTerminalState,
    run_controlled_cuda_search,
)
from src.optimizer.cuda.packed import CudaPackedFilterBatch
from src.optimizer.cuda import packed as packed_module
from src.optimizer.cuda.runtime import (
    CudaDiagnosticStatus,
    CudaExecutionMode,
    CudaRuntimeDiagnostic,
)
from src.optimizer.cuda import orchestration as orchestration_module
from tests.test_cpu_orchestration import CpuOrchestrationTests


def _device_inputs(total: int = 64) -> CudaDeviceInputs:
    inputs = object.__new__(CudaDeviceInputs)
    inputs._cache = None
    inputs._lease_id = 0
    inputs._host_template = SimpleNamespace(
        byte_count=1024,
        total_permutations=total,
    )
    inputs._arrays = ()
    inputs._released = False
    inputs._close_cache_on_release = False
    return inputs


def _diagnostic() -> CudaRuntimeDiagnostic:
    return CudaRuntimeDiagnostic(
        CudaDiagnosticStatus.READY,
        CudaExecutionMode.CUDA,
        True,
        False,
        "Synthetic CUDA device is ready.",
        cupy_version="13.0.0",
        device_count=1,
        selected_device_index=0,
        device_name="Synthetic CUDA device",
        free_vram_bytes=1 << 30,
        total_vram_bytes=2 << 30,
        driver_version=13_000,
        runtime_version=13_000,
        allocation_probe_bytes=1,
        allocation_probe_succeeded=True,
    )


class _Filter:
    def __init__(self, accepted: tuple[int, ...] = (), *, fail: bool = False) -> None:
        self.accepted = accepted
        self.fail = fail
        self.closed = False

    def filter(self, _inputs, _diagnostic, start, stop, *, maximum_captured_matches):
        if self.fail:
            raise RuntimeError("synthetic filter failure")
        captured = self.accepted[:maximum_captured_matches]
        return CudaPackedFilterBatch(
            start,
            stop,
            stop - start,
            len(self.accepted),
            np.asarray(captured, dtype="<i8"),
        )

    def close(self) -> None:
        self.closed = True


class _Materializer:
    def close(self) -> None:
        pass

    def materialize(self, _inputs, _diagnostic, batch):
        count = batch.accepted_count
        return packed_module._compaction_batch(
            batch,
            np.tile(np.arange(6, dtype="<i4"), (count, 1)),
            np.zeros((count, 6), dtype="u1"),
            np.zeros((count, 8), dtype="<i8"),
            np.zeros((count,), dtype="<i8"),
            np.zeros((count, 15), dtype="<i8"),
            np.zeros((count,), dtype="<f4"),
        )


class CudaOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        CpuOrchestrationTests.setUpClass()

    def _counting(self, cap: int):
        return CpuOrchestrationTests("test_full_execution_matches_lower_exact_layers")._fixture(
            cap=cap
        )[3]

    def _run(self, filter_runner: _Filter, *, cap: int = 10, cancel=False):
        with patch.object(
            orchestration_module,
            "CudaPackedExactFilterRunner",
            return_value=filter_runner,
        ), patch.object(
            orchestration_module,
            "CudaPackedExactMaterializer",
            return_value=_Materializer(),
        ):
            return run_controlled_cuda_search(
                _device_inputs(),
                _diagnostic(),
                self._counting(cap),
                should_cancel=lambda: cancel,
            )

    def test_empty_exact_search_completes_with_canonical_accounting(self) -> None:
        result = self._run(_Filter())
        self.assertIs(result.state, CudaSearchTerminalState.COMPLETED)
        self.assertEqual((64, 64, 0), (
            result.total_permutations,
            result.category_candidate_counts[0],
            result.counting.detected_count,
        ))
        self.assertEqual(64, result.hard_bound_rejected_count)

    def test_cap_plus_one_stops_without_materializing_partial_rows(self) -> None:
        result = self._run(_Filter((3, 9)), cap=1)
        self.assertIs(result.state, CudaSearchTerminalState.OVERFLOWED)
        self.assertEqual((2, 0, 0), result.emitted_counts)
        self.assertEqual(2, result.counting.detected_count)
        self.assertEqual(9, result.terminal_flat_index)
        self.assertEqual((), result.batches)

    def test_matching_exact_rows_are_materialized_after_filtering(self) -> None:
        result = self._run(_Filter((3, 9)))
        self.assertIs(result.state, CudaSearchTerminalState.COMPLETED)
        self.assertEqual(2, result.retained_count)
        self.assertEqual((3, 9), tuple(int(value) for value in result.batches[0].array("flat_indices")))

    def test_initial_cancel_and_filter_failure_are_cold_terminal_states(self) -> None:
        cancelled = self._run(_Filter(), cancel=True)
        self.assertIs(cancelled.state, CudaSearchTerminalState.CANCELLED)
        self.assertEqual(0, cancelled.evaluated_permutations)
        self.assertIsNone(cancelled.chunk_plan)

        failed = self._run(_Filter(fail=True))
        self.assertIs(failed.state, CudaSearchTerminalState.FAILED)
        self.assertEqual("packed-filter", failed.failure.stage)
        self.assertFalse(failed.recovery_offer.available)


if __name__ == "__main__":
    unittest.main()
