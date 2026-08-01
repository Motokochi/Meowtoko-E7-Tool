"""Exact packed-CUDA host result records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral
from typing import Any

import numpy as np

from src.optimizer.cuda.inputs import CUDA_SIGNED_INT64_MAX


_U1 = np.dtype("u1")
_I4 = np.dtype("<i4")
_I8 = np.dtype("<i8")
_U8 = np.dtype("<u8")
_F4 = np.dtype("<f4")
_RESULT_COUNT = "result_count"


class CudaCompactionError(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> CudaCompactionError:
    return CudaCompactionError(code, path, message)


def _integer(
    value: object,
    path: str,
    *,
    minimum: int = 0,
    maximum: int = CUDA_SIGNED_INT64_MAX,
) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _error("invalid-integer", path, "must be an integer; booleans are not accepted.")
    normalized = int(value)
    if normalized < minimum or normalized > maximum:
        raise _error("integer-out-of-range", path, f"must be between {minimum} and {maximum}.")
    return normalized


@dataclass(frozen=True, slots=True)
class CudaCompactionFieldSpec:
    name: str
    dtype: np.dtype[Any]
    dimensions: tuple[int | str, ...]


_OUTPUT_SPECS = (
    CudaCompactionFieldSpec("flat_indices", _I8, (_RESULT_COUNT,)),
    CudaCompactionFieldSpec("dense_item_ids", _I4, (_RESULT_COUNT, 6)),
    CudaCompactionFieldSpec("set_indices", _U1, (_RESULT_COUNT, 6)),
    CudaCompactionFieldSpec("category_codes", _U1, (_RESULT_COUNT,)),
    CudaCompactionFieldSpec("replacement_distances", _U1, (_RESULT_COUNT,)),
    CudaCompactionFieldSpec("effective_final_stats", _I8, (_RESULT_COUNT, 8)),
    CudaCompactionFieldSpec("raw_critical_hit_chances", _I8, (_RESULT_COUNT,)),
    CudaCompactionFieldSpec("derived_metrics", _I8, (_RESULT_COUNT, 15)),
    CudaCompactionFieldSpec("priority_scores", _F4, (_RESULT_COUNT,)),
    CudaCompactionFieldSpec("constraint_distances", _F4, (_RESULT_COUNT,)),
)
_COUNTER_SPECS = (
    CudaCompactionFieldSpec("category_candidate_counts", _U8, (3,)),
    CudaCompactionFieldSpec("out_of_scope_count", _U8, (1,)),
    CudaCompactionFieldSpec("disabled_category_count", _U8, (1,)),
    CudaCompactionFieldSpec("hard_bound_rejected_count", _U8, (1,)),
    CudaCompactionFieldSpec("tolerance_rejected_counts", _U8, (3,)),
    CudaCompactionFieldSpec("emitted_counts", _U8, (3,)),
)

CUDA_COMPACTION_OUTPUT_LAYOUT = _OUTPUT_SPECS
CUDA_COMPACTION_OUTPUT_FIELD_NAMES = tuple(spec.name for spec in _OUTPUT_SPECS)
CUDA_COMPACTION_COUNTER_LAYOUT = _COUNTER_SPECS
CUDA_COMPACTION_COUNTER_FIELD_NAMES = tuple(spec.name for spec in _COUNTER_SPECS)
CUDA_COMPACTION_ROW_BYTES = sum(
    spec.dtype.itemsize
    * math.prod(int(value) for value in spec.dimensions if value != _RESULT_COUNT)
    for spec in _OUTPUT_SPECS
)
CUDA_COMPACTION_COUNTER_BYTES = sum(
    spec.dtype.itemsize * math.prod(int(value) for value in spec.dimensions)
    for spec in _COUNTER_SPECS
)


@dataclass(frozen=True, slots=True)
class CudaCompactionChunkPlan:
    total_permutations: int
    batch_size: int
    batch_count: int
    input_bytes: int
    reported_free_vram_bytes: int
    estimated_peak_bytes: int

    def __post_init__(self) -> None:
        total = _integer(self.total_permutations, "CudaCompactionChunkPlan.total_permutations", minimum=1)
        batch = _integer(self.batch_size, "CudaCompactionChunkPlan.batch_size", minimum=1, maximum=total)
        batches = _integer(self.batch_count, "CudaCompactionChunkPlan.batch_count", minimum=1)
        if batches != (total + batch - 1) // batch:
            raise _error("chunk-count-mismatch", "CudaCompactionChunkPlan.batch_count", "must equal ceil(total/batch_size).")
        inputs = _integer(self.input_bytes, "CudaCompactionChunkPlan.input_bytes")
        free = _integer(self.reported_free_vram_bytes, "CudaCompactionChunkPlan.reported_free_vram_bytes", minimum=1)
        peak = _integer(self.estimated_peak_bytes, "CudaCompactionChunkPlan.estimated_peak_bytes", minimum=1)
        if peak > free:
            raise _error("invalid-memory-plan", "CudaCompactionChunkPlan", "estimated peak must fit reported free VRAM.")
        object.__setattr__(self, "total_permutations", total)
        object.__setattr__(self, "batch_size", batch)
        object.__setattr__(self, "batch_count", batches)
        object.__setattr__(self, "input_bytes", inputs)
        object.__setattr__(self, "reported_free_vram_bytes", free)
        object.__setattr__(self, "estimated_peak_bytes", peak)


def _freeze(values: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    contiguous = np.ascontiguousarray(values)
    return np.frombuffer(contiguous.tobytes(order="C"), dtype=contiguous.dtype).reshape(contiguous.shape)


@dataclass(frozen=True, slots=True, eq=False)
class CudaCompactionHostArray:
    name: str
    values: np.ndarray[Any, Any]

    def __post_init__(self) -> None:
        if self.name not in CUDA_COMPACTION_OUTPUT_FIELD_NAMES + CUDA_COMPACTION_COUNTER_FIELD_NAMES:
            raise _error("unknown-compaction-field", "CudaCompactionHostArray.name", f"unknown field {self.name!r}.")
        if not isinstance(self.values, np.ndarray):
            raise _error("invalid-compaction-array", f"CudaCompactionHostArray.{self.name}", "must be a NumPy array.")
        object.__setattr__(self, "values", _freeze(self.values))

    @property
    def dtype(self) -> np.dtype[Any]:
        return self.values.dtype

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(self.values.shape)

    @property
    def nbytes(self) -> int:
        return int(self.values.nbytes)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, CudaCompactionHostArray)
            and self.name == other.name
            and self.dtype == other.dtype
            and self.shape == other.shape
            and bool(np.array_equal(self.values, other.values))
        )

    def __hash__(self) -> int:
        return hash((self.name, self.dtype.str, self.shape, self.values.tobytes(order="C")))


@dataclass(frozen=True, slots=True)
class CudaCompactionHostBatch:
    start_index: int
    stop_index: int
    accepted_count: int
    maximum_replacement_distance: int
    arrays: tuple[CudaCompactionHostArray, ...]

    def __post_init__(self) -> None:
        start = _integer(self.start_index, "CudaCompactionHostBatch.start_index")
        stop = _integer(self.stop_index, "CudaCompactionHostBatch.stop_index", minimum=start + 1)
        accepted = _integer(
            self.accepted_count,
            "CudaCompactionHostBatch.accepted_count",
            maximum=stop - start,
        )
        if self.maximum_replacement_distance != 0:
            raise _error(
                "near-set-mode-removed",
                "CudaCompactionHostBatch.maximum_replacement_distance",
                "packed results are exact-only.",
            )
        arrays = tuple(self.arrays)
        specs = _OUTPUT_SPECS + _COUNTER_SPECS
        if tuple(item.name for item in arrays) != tuple(spec.name for spec in specs):
            raise _error("compaction-layout-order", "CudaCompactionHostBatch.arrays", "must use the canonical result layout.")
        transferred = arrays[0].shape[0]
        if transferred > accepted:
            raise _error("compaction-prefix-overflow", "CudaCompactionHostBatch.arrays", "transferred rows cannot exceed accepted_count.")
        for spec, item in zip(specs, arrays, strict=True):
            shape = tuple(
                transferred if value == _RESULT_COUNT else int(value)
                for value in spec.dimensions
            )
            if item.dtype != spec.dtype or item.shape != shape:
                raise _error(
                    "compaction-layout-mismatch",
                    f"CudaCompactionHostBatch.{spec.name}",
                    f"must use dtype {spec.dtype.name} and shape {shape!r}.",
                )
        values = {item.name: item.values for item in arrays}
        candidates = tuple(int(value) for value in values["category_candidate_counts"])
        emitted = tuple(int(value) for value in values["emitted_counts"])
        tolerance = tuple(int(value) for value in values["tolerance_rejected_counts"])
        out_of_scope = int(values["out_of_scope_count"][0])
        disabled = int(values["disabled_category_count"][0])
        hard = int(values["hard_bound_rejected_count"][0])
        if (
            candidates[1:] != (0, 0)
            or emitted[1:] != (0, 0)
            or any(tolerance)
            or disabled
            or candidates[0] + out_of_scope != stop - start
            or hard + emitted[0] != candidates[0]
            or emitted[0] != accepted
        ):
            raise _error("exact-accounting-mismatch", "CudaCompactionHostBatch", "exact candidates must partition into rejects and emissions.")
        categories = values["category_codes"]
        if np.any(categories) or np.any(values["replacement_distances"]) or np.any(values["constraint_distances"]):
            raise _error("near-set-row", "CudaCompactionHostBatch", "packed rows must use exact-only zero sentinels.")
        flat = values["flat_indices"]
        if transferred and (np.any(flat < start) or np.any(flat >= stop) or np.any(flat[1:] <= flat[:-1])):
            raise _error("compact-row-order", "CudaCompactionHostBatch.flat_indices", "must be unique, ascending, and inside the batch.")
        if any(len(set(int(value) for value in row)) != 6 for row in values["dense_item_ids"]):
            raise _error("duplicate-dense-id", "CudaCompactionHostBatch.dense_item_ids", "every row must contain six unique IDs.")
        if np.any(values["set_indices"] >= 24):
            raise _error("set-index-out-of-range", "CudaCompactionHostBatch.set_indices", "set indices must be 0..23.")
        if not np.all(np.isfinite(values["priority_scores"])):
            raise _error("nonfinite-priority", "CudaCompactionHostBatch.priority_scores", "must be finite.")
        if transferred == accepted and transferred != emitted[0]:
            raise _error("compact-counter-mismatch", "CudaCompactionHostBatch", "complete rows must match emitted_count.")
        object.__setattr__(self, "start_index", start)
        object.__setattr__(self, "stop_index", stop)
        object.__setattr__(self, "accepted_count", accepted)
        object.__setattr__(self, "arrays", arrays)

    @property
    def transferred_count(self) -> int:
        return self.arrays[0].shape[0]

    @property
    def byte_count(self) -> int:
        return sum(item.nbytes for item in self.arrays)

    def array(self, name: str) -> np.ndarray[Any, Any]:
        names = CUDA_COMPACTION_OUTPUT_FIELD_NAMES + CUDA_COMPACTION_COUNTER_FIELD_NAMES
        try:
            return self.arrays[names.index(name)].values
        except ValueError:
            raise KeyError(name) from None


__all__ = [
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
]
