"""Optional exact CUDA diagnostics and numeric boundaries."""

from __future__ import annotations

import importlib

from src.optimizer.cuda.runtime import (
    CUDA_ALLOCATION_PROBE_BYTES,
    CUDA_DISABLE_ENV_VAR,
    CUDA_REQUIRED_MAJOR,
    CudaDiagnosticStatus,
    CudaExecutionMode,
    CudaRuntimeDiagnostic,
    cuda_disabled_from_environment,
    diagnose_cuda_runtime,
)


_EXPORT_MODULES = {
    "src.optimizer.cuda.inputs": frozenset({
        "CUDA_INPUT_FIELD_NAMES",
        "CUDA_INPUT_LAYOUT",
        "CUDA_SIGNED_INT32_MAX",
        "CUDA_SIGNED_INT64_MAX",
        "CUDA_SKILL_COUNT",
        "CudaDeviceArray",
        "CudaDeviceBufferCache",
        "CudaDeviceInputs",
        "CudaHostArray",
        "CudaHostInputs",
        "CudaInputError",
        "CudaInputFieldSpec",
        "CudaSearchDimensions",
        "compile_cuda_host_inputs",
        "transfer_cuda_inputs",
        "validate_cuda_search_dimensions",
    }),
    "src.optimizer.cuda.compaction": frozenset({
        "CUDA_COMPACTION_COUNTER_BYTES",
        "CUDA_COMPACTION_COUNTER_FIELD_NAMES",
        "CUDA_COMPACTION_COUNTER_LAYOUT",
        "CUDA_COMPACTION_OUTPUT_FIELD_NAMES",
        "CUDA_COMPACTION_OUTPUT_LAYOUT",
        "CUDA_COMPACTION_ROW_BYTES",
        "CudaCompactionChunkPlan",
        "CudaCompactionError",
        "CudaCompactionFieldSpec",
        "CudaCompactionHostArray",
        "CudaCompactionHostBatch",
    }),
    "src.optimizer.cuda.packed": frozenset({
        "CUDA_PACKED_DEFAULT_CHUNK_PERMUTATIONS",
        "CUDA_PACKED_FILTER_KERNEL_NAME",
        "CUDA_PACKED_FILTER_KERNEL_SOURCE",
        "CUDA_PACKED_MATERIALIZE_KERNEL_NAME",
        "CUDA_PACKED_MATERIALIZE_KERNEL_SOURCE",
        "CUDA_PACKED_PERMUTATIONS_PER_WORD",
        "CUDA_PACKED_TARGET_PERMUTATIONS_PER_SECOND",
        "CUDA_PACKED_THREADS",
        "CudaPackedExactFilterRunner",
        "CudaPackedExactMaterializer",
        "CudaPackedExactSignature",
        "CudaPackedFilterBatch",
        "CudaPackedFilterError",
    }),
    "src.optimizer.cuda.orchestration": frozenset({
        "CudaCancellationPredicate",
        "CudaClock",
        "CudaCpuRecoveryAdapter",
        "CudaCpuRecoveryOffer",
        "CudaCpuRecoveryRequest",
        "CudaCpuRecoveryResult",
        "CudaProgressCallback",
        "CudaSearchFailure",
        "CudaSearchOrchestrationError",
        "CudaSearchProgress",
        "CudaSearchResult",
        "CudaSearchTerminalState",
        "run_controlled_cuda_search",
    }),
}


def __getattr__(name: str) -> object:
    for module_name, exports in _EXPORT_MODULES.items():
        if name in exports:
            value = getattr(importlib.import_module(module_name), name)
            globals()[name] = value
            return value
    raise AttributeError(name)


__all__ = [
    "CUDA_ALLOCATION_PROBE_BYTES",
    "CUDA_DISABLE_ENV_VAR",
    "CUDA_REQUIRED_MAJOR",
    "CudaDiagnosticStatus",
    "CudaExecutionMode",
    "CudaRuntimeDiagnostic",
    "cuda_disabled_from_environment",
    "diagnose_cuda_runtime",
    *sorted(name for exports in _EXPORT_MODULES.values() for name in exports),
]
