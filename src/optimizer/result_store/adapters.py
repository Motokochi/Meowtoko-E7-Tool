"""Bounded CPU/CUDA batch adapters for the durable result column ABI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from src.optimizer.cuda.compaction import CudaCompactionHostBatch
from src.optimizer.result_store.schema import (
    RESULT_COLUMN_NAMES,
    RESULT_MAX_DENSE_ITEM_ID,
    encode_result_category,
    validate_result_columns,
)
from src.optimizer.search.exact_evaluation import ExactBuildRow
from src.optimizer.search.slot_arrays import SearchReadySlotArrays


class ResultBatchAdapterError(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> ResultBatchAdapterError:
    return ResultBatchAdapterError(code, path, message)


@dataclass(frozen=True, slots=True)
class DenseItemEquippedLookup:
    """Contiguous inventory-snapshot flags indexed by durable dense item ID."""

    flags: tuple[bool, ...]

    def __post_init__(self) -> None:
        try:
            flags = tuple(self.flags)
        except TypeError:
            raise _error("invalid-equipped-lookup", "flags", "must be a boolean sequence.") from None
        if not flags or not all(isinstance(value, bool) for value in flags):
            raise _error("invalid-equipped-lookup", "flags", "must contain one or more booleans.")
        if len(flags) - 1 > RESULT_MAX_DENSE_ITEM_ID:
            raise _error("equipped-lookup-overflow", "flags", "exceeds the signed dense-ID boundary.")
        object.__setattr__(self, "flags", flags)

    @classmethod
    def from_pairs(cls, value: object) -> DenseItemEquippedLookup:
        try:
            pairs = tuple(value)  # type: ignore[arg-type]
        except TypeError:
            raise _error("invalid-equipped-pairs", "value", "must contain dense-ID/boolean pairs.") from None
        normalized = []
        for index, pair in enumerate(pairs):
            try:
                dense_id, equipped = pair
            except (TypeError, ValueError):
                raise _error("invalid-equipped-pair", f"value[{index}]", "must contain dense ID and boolean.") from None
            if isinstance(dense_id, bool) or not isinstance(dense_id, int) or dense_id != index:
                raise _error("noncontiguous-equipped-pairs", f"value[{index}].dense_id", f"must equal {index}.")
            if not isinstance(equipped, bool):
                raise _error("invalid-equipped-flag", f"value[{index}].equipped", "must be boolean.")
            normalized.append(equipped)
        return cls(tuple(normalized))

    def counts(self, dense_item_ids: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
        if not isinstance(dense_item_ids, np.ndarray) or dense_item_ids.dtype.str != "<i4" or dense_item_ids.ndim != 2 or dense_item_ids.shape[1:] != (6,):
            raise _error("dense-id-array", "dense_item_ids", "must use dtype <i4 and shape (rows, 6).")
        if np.any(dense_item_ids < 0) or (dense_item_ids.size and int(dense_item_ids.max()) >= len(self.flags)):
            raise _error("unknown-dense-item-id", "dense_item_ids", "contains an ID absent from the ownership snapshot.")
        flags = np.asarray(self.flags, dtype="u1")
        return flags[dense_item_ids].sum(axis=1, dtype=np.uint8).astype("u1", copy=False)


def result_columns_from_cpu_rows(
    rows: Sequence[ExactBuildRow],
    slot_arrays: SearchReadySlotArrays,
    equipped: DenseItemEquippedLookup,
) -> dict[str, np.ndarray[Any, Any]]:
    if not isinstance(slot_arrays, SearchReadySlotArrays):
        raise _error("invalid-search-arrays", "slot_arrays", "must be SearchReadySlotArrays.")
    if not isinstance(equipped, DenseItemEquippedLookup):
        raise _error("invalid-equipped-lookup", "equipped", "must be DenseItemEquippedLookup.")
    try:
        supplied = tuple(rows)
    except TypeError:
        raise _error("invalid-cpu-rows", "rows", "must be a bounded row sequence.") from None
    if not all(isinstance(row, ExactBuildRow) for row in supplied):
        raise _error("invalid-cpu-row", "rows", "must contain ExactBuildRow values.")
    set_by_dense_id = tuple(
        set_index
        for slot in slot_arrays.slots
        for set_index in slot.set_indices
    )
    count = len(supplied)
    dense = np.empty((count, 6), dtype="<i4")
    sets = np.empty((count, 6), dtype="u1")
    categories = np.empty(count, dtype="u1")
    stats = np.empty((count, 8), dtype="<i8")
    raw_crit = np.empty(count, dtype="<i8")
    metrics = np.empty((count, 15), dtype="<i8")
    priority = np.empty(count, dtype="<f4")
    constraint = np.empty(count, dtype="<f4")
    for index, row in enumerate(supplied):
        dense[index] = row.dense_ids
        sets[index] = tuple(set_by_dense_id[dense_id] for dense_id in row.dense_ids)
        categories[index] = encode_result_category("result.exact")
        stats[index] = row.effective_final_stats
        raw_crit[index] = row.raw_final_stats[4]
        metrics[index] = row.derived_metrics
        priority[index] = row.priority_score
        constraint[index] = 0
    arrays = {
        "dense_item_ids": dense,
        "owned_set_indices": sets,
        "category_codes": categories,
        "replacement_distances": categories.copy(),
        "effective_final_stats": stats,
        "raw_critical_hit_chances": raw_crit,
        "derived_metrics": metrics,
        "priority_scores": priority,
        "constraint_distances": constraint,
        "equipped_item_counts": equipped.counts(dense),
    }
    validate_result_columns(arrays)
    return arrays


def result_columns_from_cuda_batch(
    batch: CudaCompactionHostBatch,
    equipped: DenseItemEquippedLookup,
) -> dict[str, np.ndarray[Any, Any]]:
    if not isinstance(batch, CudaCompactionHostBatch):
        raise _error("invalid-cuda-batch", "batch", "must be CudaCompactionHostBatch.")
    if not isinstance(equipped, DenseItemEquippedLookup):
        raise _error("invalid-equipped-lookup", "equipped", "must be DenseItemEquippedLookup.")
    if batch.transferred_count != batch.accepted_count:
        raise _error(
            "partial-cuda-transfer",
            "batch",
            "durable storage requires every accepted row, not a cap+1 prefix.",
        )
    arrays = {
        "dense_item_ids": batch.array("dense_item_ids"),
        "owned_set_indices": batch.array("set_indices"),
        "category_codes": batch.array("category_codes"),
        "replacement_distances": batch.array("replacement_distances"),
        "effective_final_stats": batch.array("effective_final_stats"),
        "raw_critical_hit_chances": batch.array("raw_critical_hit_chances"),
        "derived_metrics": batch.array("derived_metrics"),
        "priority_scores": batch.array("priority_scores"),
        "constraint_distances": batch.array("constraint_distances"),
        "equipped_item_counts": equipped.counts(batch.array("dense_item_ids")),
    }
    if tuple(arrays) != RESULT_COLUMN_NAMES:
        raise AssertionError("adapter column order drift")
    validate_result_columns(arrays)
    return arrays


__all__ = [
    "DenseItemEquippedLookup",
    "ResultBatchAdapterError",
    "result_columns_from_cpu_rows",
    "result_columns_from_cuda_batch",
]
