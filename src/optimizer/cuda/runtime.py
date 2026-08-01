"""Lazy CUDA 13 diagnostics with an explicit CPU fallback contract."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import ModuleType


CUDA_REQUIRED_MAJOR = 13
CUDA_DISABLE_ENV_VAR = "E7_DISABLE_CUDA"
CUDA_ALLOCATION_PROBE_BYTES = 1 << 20
_MAX_ALLOCATION_PROBE_BYTES = 64 << 20
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class CudaDiagnosticStatus(StrEnum):
    """Stable readiness outcomes shared by health and future execution."""

    READY = "ready"
    DISABLED = "disabled"
    CUPY_UNAVAILABLE = "cupy-unavailable"
    NO_DEVICE = "no-device"
    INCOMPATIBLE = "incompatible"
    QUERY_FAILED = "query-failed"
    ALLOCATION_FAILED = "allocation-failed"


class CudaExecutionMode(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{path} must be a non-empty canonical string.")
    return value


def _optional_text(value: object, path: str) -> str | None:
    return None if value is None else _text(value, path)


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{path} must be an integer of at least {minimum}.")
    return value


def _optional_integer(value: object, path: str) -> int | None:
    return None if value is None else _integer(value, path)


@dataclass(frozen=True, slots=True)
class CudaRuntimeDiagnostic:
    """Complete immutable CUDA readiness evidence for one bounded probe."""

    status: CudaDiagnosticStatus
    mode: CudaExecutionMode
    available: bool
    disabled: bool
    summary: str
    detail: str | None = None
    cupy_version: str | None = None
    device_count: int = 0
    selected_device_index: int | None = None
    device_name: str | None = None
    free_vram_bytes: int | None = None
    total_vram_bytes: int | None = None
    driver_version: int | None = None
    runtime_version: int | None = None
    allocation_probe_bytes: int = 0
    allocation_probe_succeeded: bool = False

    def __post_init__(self) -> None:
        try:
            status = CudaDiagnosticStatus(self.status)
            mode = CudaExecutionMode(self.mode)
        except (TypeError, ValueError):
            raise ValueError("CUDA diagnostic status and mode must be canonical values.") from None
        if not isinstance(self.available, bool) or not isinstance(self.disabled, bool):
            raise ValueError("CUDA diagnostic availability flags must be booleans.")
        ready = status is CudaDiagnosticStatus.READY
        if self.available is not ready or (mode is CudaExecutionMode.CUDA) is not ready:
            raise ValueError("Only a ready CUDA diagnostic may select CUDA execution.")
        if self.disabled is not (status is CudaDiagnosticStatus.DISABLED):
            raise ValueError("CUDA disabled evidence must agree with diagnostic status.")

        summary = _text(self.summary, "CudaRuntimeDiagnostic.summary")
        detail = _optional_text(self.detail, "CudaRuntimeDiagnostic.detail")
        cupy_version = _optional_text(
            self.cupy_version,
            "CudaRuntimeDiagnostic.cupy_version",
        )
        device_count = _integer(
            self.device_count,
            "CudaRuntimeDiagnostic.device_count",
        )
        selected = _optional_integer(
            self.selected_device_index,
            "CudaRuntimeDiagnostic.selected_device_index",
        )
        if selected is not None and selected >= device_count:
            raise ValueError("Selected CUDA device index must be inside device_count.")
        device_name = _optional_text(
            self.device_name,
            "CudaRuntimeDiagnostic.device_name",
        )
        free_vram = _optional_integer(
            self.free_vram_bytes,
            "CudaRuntimeDiagnostic.free_vram_bytes",
        )
        total_vram = _optional_integer(
            self.total_vram_bytes,
            "CudaRuntimeDiagnostic.total_vram_bytes",
        )
        if (free_vram is None) is not (total_vram is None):
            raise ValueError("CUDA free and total VRAM evidence must be supplied together.")
        if free_vram is not None and total_vram is not None and free_vram > total_vram:
            raise ValueError("CUDA free VRAM must not exceed total VRAM.")
        driver_version = _optional_integer(
            self.driver_version,
            "CudaRuntimeDiagnostic.driver_version",
        )
        runtime_version = _optional_integer(
            self.runtime_version,
            "CudaRuntimeDiagnostic.runtime_version",
        )
        probe_bytes = _integer(
            self.allocation_probe_bytes,
            "CudaRuntimeDiagnostic.allocation_probe_bytes",
        )
        if not isinstance(self.allocation_probe_succeeded, bool):
            raise ValueError("CUDA allocation probe result must be a boolean.")

        imported_statuses = {
            CudaDiagnosticStatus.NO_DEVICE,
            CudaDiagnosticStatus.INCOMPATIBLE,
            CudaDiagnosticStatus.QUERY_FAILED,
            CudaDiagnosticStatus.ALLOCATION_FAILED,
            CudaDiagnosticStatus.READY,
        }
        if (status in imported_statuses) is not (cupy_version is not None):
            raise ValueError("CuPy version evidence must agree with successful module loading.")
        if status is CudaDiagnosticStatus.NO_DEVICE and device_count != 0:
            raise ValueError("A no-device diagnostic must report zero devices.")
        if status in {
            CudaDiagnosticStatus.INCOMPATIBLE,
            CudaDiagnosticStatus.ALLOCATION_FAILED,
            CudaDiagnosticStatus.READY,
        } and device_count < 1:
            raise ValueError("This CUDA diagnostic status requires a detected device.")
        if status in {CudaDiagnosticStatus.READY, CudaDiagnosticStatus.ALLOCATION_FAILED}:
            if (
                selected is None
                or device_name is None
                or free_vram is None
                or total_vram is None
                or driver_version is None
                or runtime_version is None
            ):
                raise ValueError("Ready/allocation diagnostics require complete device evidence.")
            if probe_bytes < 1:
                raise ValueError("Ready/allocation diagnostics require a positive probe size.")
        elif probe_bytes != 0:
            raise ValueError("Only ready/allocation diagnostics may retain a probe size.")
        if self.allocation_probe_succeeded is not ready:
            raise ValueError("Only a ready diagnostic may report a successful allocation probe.")
        failure_detail_statuses = {
            CudaDiagnosticStatus.CUPY_UNAVAILABLE,
            CudaDiagnosticStatus.INCOMPATIBLE,
            CudaDiagnosticStatus.QUERY_FAILED,
            CudaDiagnosticStatus.ALLOCATION_FAILED,
        }
        if (status in failure_detail_statuses) is not (detail is not None):
            raise ValueError("CUDA failure detail must agree with diagnostic status.")

        object.__setattr__(self, "status", status)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "detail", detail)
        object.__setattr__(self, "cupy_version", cupy_version)
        object.__setattr__(self, "device_count", device_count)
        object.__setattr__(self, "selected_device_index", selected)
        object.__setattr__(self, "device_name", device_name)
        object.__setattr__(self, "free_vram_bytes", free_vram)
        object.__setattr__(self, "total_vram_bytes", total_vram)
        object.__setattr__(self, "driver_version", driver_version)
        object.__setattr__(self, "runtime_version", runtime_version)
        object.__setattr__(self, "allocation_probe_bytes", probe_bytes)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status.value,
            "mode": self.mode.value,
            "available": self.available,
            "disabled": self.disabled,
            "summary": self.summary,
            "cupyVersion": self.cupy_version,
            "deviceCount": self.device_count,
            "selectedDeviceIndex": self.selected_device_index,
            "deviceName": self.device_name,
            "freeVramBytes": self.free_vram_bytes,
            "totalVramBytes": self.total_vram_bytes,
            "driverVersion": self.driver_version,
            "runtimeVersion": self.runtime_version,
            "allocationProbeBytes": self.allocation_probe_bytes,
            "allocationProbeSucceeded": self.allocation_probe_succeeded,
        }
        if self.detail is not None:
            result["detail"] = self.detail
        return result


def cuda_disabled_from_environment(environment: Mapping[str, str]) -> bool:
    """Return whether the explicit process-local CUDA opt-out is enabled."""

    if not isinstance(environment, Mapping):
        raise ValueError("environment must be a string mapping.")
    value = environment.get(CUDA_DISABLE_ENV_VAR, "")
    if not isinstance(value, str):
        raise ValueError(f"{CUDA_DISABLE_ENV_VAR} must be a string when supplied.")
    return value.strip().lower() in _TRUTHY


def _failure_detail(stage: str, error: BaseException) -> str:
    message = " ".join(str(error).split()) or "no additional detail"
    return f"{stage}: {type(error).__name__}: {message}"


def _cupy_version(module: object) -> str:
    value = getattr(module, "__version__", "unknown")
    return str(value).strip() or "unknown"


def _device_name(properties: object) -> str:
    if not isinstance(properties, Mapping):
        raise TypeError("CUDA device properties did not return a mapping.")
    raw_name = properties.get("name", properties.get(b"name"))
    if isinstance(raw_name, bytes):
        name = raw_name.decode("utf-8", errors="replace")
    else:
        name = str(raw_name or "")
    return _text(name.strip(), "CUDA device name")


def _diagnostic(
    status: CudaDiagnosticStatus,
    summary: str,
    *,
    detail: str | None = None,
    cupy_version: str | None = None,
    device_count: int = 0,
    selected_device_index: int | None = None,
    device_name: str | None = None,
    free_vram_bytes: int | None = None,
    total_vram_bytes: int | None = None,
    driver_version: int | None = None,
    runtime_version: int | None = None,
    allocation_probe_bytes: int = 0,
    allocation_probe_succeeded: bool = False,
) -> CudaRuntimeDiagnostic:
    ready = status is CudaDiagnosticStatus.READY
    return CudaRuntimeDiagnostic(
        status=status,
        mode=CudaExecutionMode.CUDA if ready else CudaExecutionMode.CPU,
        available=ready,
        disabled=status is CudaDiagnosticStatus.DISABLED,
        summary=summary,
        detail=detail,
        cupy_version=cupy_version,
        device_count=device_count,
        selected_device_index=selected_device_index,
        device_name=device_name,
        free_vram_bytes=free_vram_bytes,
        total_vram_bytes=total_vram_bytes,
        driver_version=driver_version,
        runtime_version=runtime_version,
        allocation_probe_bytes=allocation_probe_bytes,
        allocation_probe_succeeded=allocation_probe_succeeded,
    )


def diagnose_cuda_runtime(
    *,
    disabled: bool = False,
    module_loader: Callable[[str], ModuleType | object] = importlib.import_module,
    allocation_probe_bytes: int = CUDA_ALLOCATION_PROBE_BYTES,
) -> CudaRuntimeDiagnostic:
    """Probe CUDA 13 once without importing CuPy until the probe is requested."""

    if not isinstance(disabled, bool):
        raise ValueError("disabled must be a boolean.")
    probe_bytes = _integer(allocation_probe_bytes, "allocation_probe_bytes", minimum=1)
    if probe_bytes > _MAX_ALLOCATION_PROBE_BYTES:
        raise ValueError(
            f"allocation_probe_bytes must not exceed {_MAX_ALLOCATION_PROBE_BYTES}."
        )
    if disabled:
        return _diagnostic(
            CudaDiagnosticStatus.DISABLED,
            "CPU fallback is active because CUDA was deliberately disabled.",
        )

    try:
        cupy = module_loader("cupy")
    except Exception as error:
        return _diagnostic(
            CudaDiagnosticStatus.CUPY_UNAVAILABLE,
            "CPU fallback is active because the optional CUDA runtime is not installed.",
            detail=_failure_detail("CuPy import failed", error),
        )
    version = _cupy_version(cupy)

    try:
        runtime = getattr(getattr(cupy, "cuda"), "runtime")
        device_count = int(runtime.getDeviceCount())
        runtime_version = int(runtime.runtimeGetVersion())
        driver_version = int(runtime.driverGetVersion())
    except Exception as error:
        return _diagnostic(
            CudaDiagnosticStatus.QUERY_FAILED,
            "CPU fallback is active because CUDA runtime discovery failed.",
            detail=_failure_detail("CUDA runtime query failed", error),
            cupy_version=version,
        )

    if device_count < 0 or runtime_version < 0 or driver_version < 0:
        return _diagnostic(
            CudaDiagnosticStatus.QUERY_FAILED,
            "CPU fallback is active because CUDA returned invalid diagnostic values.",
            detail="CUDA runtime query returned a negative count or version.",
            cupy_version=version,
        )
    if device_count == 0:
        return _diagnostic(
            CudaDiagnosticStatus.NO_DEVICE,
            "CPU fallback is active because no CUDA device was detected.",
            cupy_version=version,
            driver_version=driver_version,
            runtime_version=runtime_version,
        )

    runtime_major = runtime_version // 1000
    driver_major = driver_version // 1000
    if runtime_major != CUDA_REQUIRED_MAJOR or driver_major < CUDA_REQUIRED_MAJOR:
        return _diagnostic(
            CudaDiagnosticStatus.INCOMPATIBLE,
            "CPU fallback is active because the CUDA driver/runtime is incompatible.",
            detail=(
                f"Expected CUDA {CUDA_REQUIRED_MAJOR}.x with a driver capable of CUDA "
                f"{CUDA_REQUIRED_MAJOR}.x; found runtime {runtime_version} and driver "
                f"API {driver_version}."
            ),
            cupy_version=version,
            device_count=device_count,
            driver_version=driver_version,
            runtime_version=runtime_version,
        )

    original_device: int | None = None
    selected = False
    device_name: str | None = None
    free_vram: int | None = None
    total_vram: int | None = None
    failure_status: CudaDiagnosticStatus | None = None
    failure_summary: str | None = None
    failure_detail: str | None = None
    probe_succeeded = False

    try:
        original_device = int(runtime.getDevice())
        runtime.setDevice(0)
        selected = True
        properties = runtime.getDeviceProperties(0)
        device_name = _device_name(properties)
        free_vram, total_vram = (
            int(value) for value in runtime.memGetInfo()
        )
        if free_vram < 0 or total_vram < 0 or free_vram > total_vram:
            raise ValueError("CUDA returned invalid free/total VRAM values.")

        pointer = None
        try:
            pointer = runtime.malloc(probe_bytes)
        except Exception as error:
            failure_status = CudaDiagnosticStatus.ALLOCATION_FAILED
            failure_summary = "CPU fallback is active because the CUDA allocation probe failed."
            failure_detail = _failure_detail("CUDA allocation probe failed", error)
        else:
            try:
                runtime.free(pointer)
            except Exception as error:
                failure_status = CudaDiagnosticStatus.ALLOCATION_FAILED
                failure_summary = "CPU fallback is active because CUDA probe cleanup failed."
                failure_detail = _failure_detail("CUDA allocation cleanup failed", error)
            else:
                probe_succeeded = True
    except Exception as error:
        failure_status = CudaDiagnosticStatus.QUERY_FAILED
        failure_summary = "CPU fallback is active because CUDA device discovery failed."
        failure_detail = _failure_detail("CUDA device query failed", error)
    finally:
        if selected and original_device is not None and original_device != 0:
            try:
                runtime.setDevice(original_device)
            except Exception as error:
                restore_detail = _failure_detail("CUDA device restore failed", error)
                failure_status = CudaDiagnosticStatus.QUERY_FAILED
                failure_summary = "CPU fallback is active because CUDA device restoration failed."
                failure_detail = (
                    restore_detail
                    if failure_detail is None
                    else f"{failure_detail}; {restore_detail}"
                )
                probe_succeeded = False

    if failure_status is CudaDiagnosticStatus.QUERY_FAILED:
        return _diagnostic(
            failure_status,
            failure_summary or "CPU fallback is active because CUDA device discovery failed.",
            detail=failure_detail or "CUDA device query failed without detail.",
            cupy_version=version,
            device_count=device_count,
        )
    if failure_status is CudaDiagnosticStatus.ALLOCATION_FAILED:
        return _diagnostic(
            failure_status,
            failure_summary or "CPU fallback is active because the CUDA allocation probe failed.",
            detail=failure_detail or "CUDA allocation probe failed without detail.",
            cupy_version=version,
            device_count=device_count,
            selected_device_index=0,
            device_name=device_name,
            free_vram_bytes=free_vram,
            total_vram_bytes=total_vram,
            driver_version=driver_version,
            runtime_version=runtime_version,
            allocation_probe_bytes=probe_bytes,
        )
    if not probe_succeeded:
        raise RuntimeError("CUDA diagnostic reached an impossible probe state.")
    return _diagnostic(
        CudaDiagnosticStatus.READY,
        f"CUDA acceleration is ready on {device_name}.",
        cupy_version=version,
        device_count=device_count,
        selected_device_index=0,
        device_name=device_name,
        free_vram_bytes=free_vram,
        total_vram_bytes=total_vram,
        driver_version=driver_version,
        runtime_version=runtime_version,
        allocation_probe_bytes=probe_bytes,
        allocation_probe_succeeded=True,
    )


__all__ = [
    "CUDA_ALLOCATION_PROBE_BYTES",
    "CUDA_DISABLE_ENV_VAR",
    "CUDA_REQUIRED_MAJOR",
    "CudaDiagnosticStatus",
    "CudaExecutionMode",
    "CudaRuntimeDiagnostic",
    "cuda_disabled_from_environment",
    "diagnose_cuda_runtime",
]
