from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

from src.optimizer.cuda import (
    CUDA_ALLOCATION_PROBE_BYTES,
    CUDA_DISABLE_ENV_VAR,
    CUDA_REQUIRED_MAJOR,
    CudaDiagnosticStatus,
    CudaExecutionMode,
    CudaRuntimeDiagnostic,
    cuda_disabled_from_environment,
    diagnose_cuda_runtime,
)
from src.optimizer.cuda import runtime as runtime_module
from src.optimizer.search import (
    cpu_orchestration as cpu_orchestration_module,
    exact_evaluation as exact_evaluation_module,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeCudaRuntime:
    def __init__(
        self,
        *,
        device_count: int = 2,
        runtime_version: int = 13020,
        driver_version: int = 13030,
        failure: str | None = None,
    ) -> None:
        self.device_count = device_count
        self.runtime_version = runtime_version
        self.driver_version = driver_version
        self.failure = failure
        self.current_device = 1 if device_count > 1 else 0
        self.calls: list[object] = []

    def _called(self, stage: str) -> None:
        self.calls.append(stage)
        if self.failure == stage:
            raise RuntimeError(f"forced {stage} failure")

    def getDeviceCount(self) -> int:
        self._called("device-count")
        return self.device_count

    def runtimeGetVersion(self) -> int:
        self._called("runtime-version")
        return self.runtime_version

    def driverGetVersion(self) -> int:
        self._called("driver-version")
        return self.driver_version

    def getDevice(self) -> int:
        self._called("get-device")
        return self.current_device

    def setDevice(self, index: int) -> None:
        stage = "restore-device" if index == 1 else "select-device"
        self._called(stage)
        self.current_device = index

    def getDeviceProperties(self, index: int):
        self._called("device-properties")
        if index != 0:
            raise AssertionError("diagnostics must select device zero")
        return {b"name": b"NVIDIA GeForce RTX 5090"}

    def memGetInfo(self):
        self._called("memory-info")
        return 30 << 30, 32 << 30

    def malloc(self, size: int) -> int:
        self.calls.append(("malloc", size))
        if self.failure == "malloc":
            raise MemoryError("forced allocation failure")
        return 4242

    def free(self, pointer: int) -> None:
        self.calls.append(("free", pointer))
        if self.failure == "free":
            raise RuntimeError("forced free failure")


def fake_cupy(runtime: FakeCudaRuntime):
    return SimpleNamespace(
        __version__="14.1.1",
        cuda=SimpleNamespace(runtime=runtime),
    )


def loader_for(runtime: FakeCudaRuntime):
    module = fake_cupy(runtime)

    def load(name: str):
        if name != "cupy":
            raise AssertionError(name)
        return module

    return load


class CudaRuntimeDiagnosticTests(unittest.TestCase):
    def test_ready_probe_reports_complete_device_evidence_and_releases_memory(self) -> None:
        runtime = FakeCudaRuntime()

        result = diagnose_cuda_runtime(module_loader=loader_for(runtime))

        self.assertIs(result.status, CudaDiagnosticStatus.READY)
        self.assertIs(result.mode, CudaExecutionMode.CUDA)
        self.assertTrue(result.available)
        self.assertFalse(result.disabled)
        self.assertEqual("14.1.1", result.cupy_version)
        self.assertEqual(2, result.device_count)
        self.assertEqual(0, result.selected_device_index)
        self.assertEqual("NVIDIA GeForce RTX 5090", result.device_name)
        self.assertEqual(30 << 30, result.free_vram_bytes)
        self.assertEqual(32 << 30, result.total_vram_bytes)
        self.assertEqual(13030, result.driver_version)
        self.assertEqual(13020, result.runtime_version)
        self.assertEqual(CUDA_ALLOCATION_PROBE_BYTES, result.allocation_probe_bytes)
        self.assertTrue(result.allocation_probe_succeeded)
        self.assertIn(("malloc", CUDA_ALLOCATION_PROBE_BYTES), runtime.calls)
        self.assertIn(("free", 4242), runtime.calls)
        self.assertEqual(1, runtime.current_device)
        self.assertEqual("cuda", result.to_dict()["mode"])

    def test_deliberate_disable_short_circuits_loading_and_environment_is_explicit(self) -> None:
        loaded: list[str] = []

        result = diagnose_cuda_runtime(
            disabled=True,
            module_loader=lambda name: loaded.append(name),
        )

        self.assertIs(result.status, CudaDiagnosticStatus.DISABLED)
        self.assertIs(result.mode, CudaExecutionMode.CPU)
        self.assertTrue(result.disabled)
        self.assertEqual([], loaded)
        for value in ("1", "TRUE", " yes ", "On"):
            with self.subTest(value=value):
                self.assertTrue(
                    cuda_disabled_from_environment({CUDA_DISABLE_ENV_VAR: value})
                )
        for value in ("", "0", "false", "anything-else"):
            with self.subTest(value=value):
                self.assertFalse(
                    cuda_disabled_from_environment({CUDA_DISABLE_ENV_VAR: value})
                )

    def test_missing_cupy_is_actionable_cpu_fallback(self) -> None:
        def missing(_: str):
            raise ModuleNotFoundError("No module named 'cupy'")

        result = diagnose_cuda_runtime(module_loader=missing)

        self.assertIs(result.status, CudaDiagnosticStatus.CUPY_UNAVAILABLE)
        self.assertIs(result.mode, CudaExecutionMode.CPU)
        self.assertFalse(result.available)
        self.assertIn("CuPy import failed", result.detail)
        self.assertIsNone(result.cupy_version)

    def test_no_device_retains_versions_without_attempting_allocation(self) -> None:
        runtime = FakeCudaRuntime(device_count=0)

        result = diagnose_cuda_runtime(module_loader=loader_for(runtime))

        self.assertIs(result.status, CudaDiagnosticStatus.NO_DEVICE)
        self.assertEqual(0, result.device_count)
        self.assertEqual(13020, result.runtime_version)
        self.assertEqual(13030, result.driver_version)
        self.assertFalse(any(isinstance(call, tuple) for call in runtime.calls))

    def test_cuda_major_and_driver_runtime_incompatibility_are_explicit(self) -> None:
        cases = (
            (12090, 13000),
            (13020, 12090),
        )
        for runtime_version, driver_version in cases:
            with self.subTest(runtime=runtime_version, driver=driver_version):
                runtime = FakeCudaRuntime(
                    runtime_version=runtime_version,
                    driver_version=driver_version,
                )
                result = diagnose_cuda_runtime(module_loader=loader_for(runtime))
                self.assertIs(result.status, CudaDiagnosticStatus.INCOMPATIBLE)
                self.assertIs(result.mode, CudaExecutionMode.CPU)
                self.assertIn(f"CUDA {CUDA_REQUIRED_MAJOR}.x", result.detail)
                self.assertFalse(any(isinstance(call, tuple) for call in runtime.calls))

    def test_query_failures_restore_selected_device_and_never_allocate(self) -> None:
        for stage in (
            "device-count",
            "runtime-version",
            "driver-version",
            "get-device",
            "select-device",
            "device-properties",
            "memory-info",
            "restore-device",
        ):
            with self.subTest(stage=stage):
                runtime = FakeCudaRuntime(failure=stage)
                result = diagnose_cuda_runtime(module_loader=loader_for(runtime))
                self.assertIs(result.status, CudaDiagnosticStatus.QUERY_FAILED)
                self.assertIs(result.mode, CudaExecutionMode.CPU)
                self.assertIn("failed", result.detail)
                if stage in {"device-properties", "memory-info"}:
                    self.assertEqual(1, runtime.current_device)
                    self.assertFalse(any(isinstance(call, tuple) for call in runtime.calls))

    def test_allocation_and_cleanup_failures_are_recoverable_cpu_fallbacks(self) -> None:
        for stage in ("malloc", "free"):
            with self.subTest(stage=stage):
                runtime = FakeCudaRuntime(failure=stage)
                result = diagnose_cuda_runtime(module_loader=loader_for(runtime))
                self.assertIs(result.status, CudaDiagnosticStatus.ALLOCATION_FAILED)
                self.assertIs(result.mode, CudaExecutionMode.CPU)
                self.assertEqual(CUDA_ALLOCATION_PROBE_BYTES, result.allocation_probe_bytes)
                self.assertFalse(result.allocation_probe_succeeded)
                self.assertEqual(1, runtime.current_device)

        recovered = diagnose_cuda_runtime(
            module_loader=loader_for(FakeCudaRuntime())
        )
        self.assertIs(recovered.status, CudaDiagnosticStatus.READY)

    def test_records_inputs_and_bounds_are_strict_immutable_and_hashable(self) -> None:
        ready = diagnose_cuda_runtime(module_loader=loader_for(FakeCudaRuntime()))
        self.assertIsInstance(hash(ready), int)
        with self.assertRaises(FrozenInstanceError):
            ready.available = False  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "Only a ready"):
            replace(ready, available=False)
        with self.assertRaisesRegex(ValueError, "positive probe"):
            replace(ready, allocation_probe_bytes=0)
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            diagnose_cuda_runtime(allocation_probe_bytes=(64 << 20) + 1)
        with self.assertRaisesRegex(ValueError, "disabled must be"):
            diagnose_cuda_runtime(disabled=1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "string mapping"):
            cuda_disabled_from_environment(object())  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "Only a ready"):
            CudaRuntimeDiagnostic(
                status=CudaDiagnosticStatus.DISABLED,
                mode=CudaExecutionMode.CUDA,
                available=True,
                disabled=True,
                summary="invalid",
            )

    def test_optional_dependency_is_isolated_public_and_frozen_cpu_safe(self) -> None:
        source = inspect.getsource(runtime_module)
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertNotIn("cupy", imports)
        self.assertFalse(
            any(name.startswith(("src.desktop", "src.optimizer.search", "sqlite3")) for name in imports)
        )
        for module in (
            cpu_orchestration_module,
            exact_evaluation_module,
        ):
            self.assertNotIn("src.optimizer.cuda", inspect.getsource(module))

        from src.optimizer import cuda

        self.assertIs(cuda.CudaRuntimeDiagnostic, CudaRuntimeDiagnostic)
        self.assertIs(cuda.diagnose_cuda_runtime, diagnose_cuda_runtime)
        optional = (ROOT / "requirements-cuda.txt").read_text(encoding="utf-8")
        component = (ROOT / "requirements-cuda-component.txt").read_text(encoding="utf-8")
        core = (ROOT / "requirements-core.txt").read_text(encoding="utf-8")
        build = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
        mandatory = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(
            "-r requirements-core.txt\n-r requirements-cuda-component.txt\n",
            optional,
        )
        self.assertEqual("cupy-cuda13x[ctk]==14.1.1\n", component)
        self.assertEqual("-r requirements-build.txt\n", mandatory)
        self.assertIn("-r requirements-core.txt\n", build)
        self.assertNotIn("cupy", "\n".join((mandatory, core, build)).lower())
        self.assertNotIn("pyinstaller", core.lower())
        spec = (ROOT / "packaging" / "e7-core.spec").read_text(encoding="utf-8")
        self.assertIn('"src.optimizer.cuda.runtime"', spec)
        self.assertIn('"cupy"', spec)


if __name__ == "__main__":
    unittest.main()
