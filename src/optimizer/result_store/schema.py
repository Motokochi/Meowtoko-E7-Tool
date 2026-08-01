"""Immutable, side-effect-free ABI for retained optimizer result columns.

This module describes numeric arrays only. It never opens a repository, creates
storage, imports CuPy, or materializes one Python object per result row.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral
from typing import Any, Mapping

import numpy as np

from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    FRIBBELS_SET_ORDER,
    GEAR_SLOT_ORDER,
    MAX_RESULT_CAP,
    RESULT_CATEGORY_ORDER as DOMAIN_RESULT_CATEGORY_ORDER,
    SET_CATALOG,
    ResultCategory,
)
from src.optimizer.engine.derived_metrics import DERIVED_METRIC_IDS


RESULT_SCHEMA_ID = "e7.optimizer.result-columns"
RESULT_SCHEMA_VERSION = 2
RESULT_SLOT_ORDER = GEAR_SLOT_ORDER
RESULT_CATEGORY_ORDER = DOMAIN_RESULT_CATEGORY_ORDER
RESULT_PRIMARY_STAT_ORDER = FINAL_STAT_ORDER
RESULT_DERIVED_METRIC_ORDER = DERIVED_METRIC_IDS

RESULT_MAX_DENSE_ITEM_ID = (1 << 31) - 1
RESULT_MAX_ROW_ORDINAL = MAX_RESULT_CAP - 1
NO_REPLACEMENT_METADATA_REFERENCE = None

_U1 = np.dtype("u1")
_I4 = np.dtype("<i4")
_I8 = np.dtype("<i8")
_F4 = np.dtype("<f4")
_U4 = np.dtype("<u4")


class ResultSchemaError(ValueError):
    """Actionable result-schema or column-batch validation failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> ResultSchemaError:
    return ResultSchemaError(code, path, message)


def _integer(value: object, path: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _error("invalid-integer", path, "must be an integer.")
    normalized = int(value)
    if normalized < minimum or normalized > maximum:
        raise _error(
            "integer-out-of-range",
            path,
            f"must be between {minimum} and {maximum}; found {normalized}.",
        )
    return normalized


@dataclass(frozen=True, slots=True)
class ResultColumnSpec:
    """One physical column; ``shape`` excludes the leading row dimension."""

    name: str
    dtype: np.dtype[Any]
    shape: tuple[int, ...]
    semantic_order: tuple[str, ...] = ()
    code_values: tuple[str, ...] = ()
    nullable: bool = False
    sentinel: int | float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise _error("invalid-column-name", "ResultColumnSpec.name", "must be nonempty text.")
        dtype = np.dtype(self.dtype)
        if dtype.hasobject or dtype.itemsize <= 0:
            raise _error("invalid-column-dtype", self.name, "must be a fixed-width numeric dtype.")
        shape = tuple(self.shape)
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in shape):
            raise _error("invalid-column-shape", self.name, "dimensions must be positive integers.")
        semantic = tuple(self.semantic_order)
        code_values = tuple(self.code_values)
        if semantic and len(shape) != 1:
            raise _error("invalid-semantic-axis", self.name, "semantic order requires one vector dimension.")
        if semantic and len(semantic) != shape[0]:
            raise _error("semantic-axis-length", self.name, "semantic order must cover the vector dimension.")
        if code_values and shape:
            raise _error("invalid-code-values", self.name, "coded enum values are only valid for scalar columns.")
        if self.nullable != (self.sentinel is not None):
            raise _error(
                "invalid-null-contract",
                self.name,
                "nullable columns require a sentinel and non-nullable columns must not define one.",
            )
        object.__setattr__(self, "dtype", dtype)
        object.__setattr__(self, "shape", shape)
        object.__setattr__(self, "semantic_order", semantic)
        object.__setattr__(self, "code_values", code_values)

    @property
    def values_per_row(self) -> int:
        return math.prod(self.shape) if self.shape else 1

    @property
    def bytes_per_row(self) -> int:
        return self.dtype.itemsize * self.values_per_row


@dataclass(frozen=True, slots=True)
class ResultRowIdentitySpec:
    """Logical identity deliberately encoded without another physical column."""

    ordinal_dtype: np.dtype[Any]
    maximum_ordinal: int
    stored: bool
    permutation_key_column: str

    def __post_init__(self) -> None:
        dtype = np.dtype(self.ordinal_dtype)
        if dtype.str != _U4.str:
            raise _error("identity-dtype", "ResultRowIdentitySpec.ordinal_dtype", "must be little-endian uint32.")
        if self.stored:
            raise _error("stored-row-ordinal", "ResultRowIdentitySpec.stored", "row ordinal is the physical column offset.")
        object.__setattr__(self, "ordinal_dtype", dtype)


@dataclass(frozen=True, slots=True)
class ResultSchema:
    schema_id: str
    version: int
    maximum_rows: int
    row_identity: ResultRowIdentitySpec
    columns: tuple[ResultColumnSpec, ...]

    def __post_init__(self) -> None:
        columns = tuple(self.columns)
        names = tuple(column.name for column in columns)
        if not columns or len(names) != len(set(names)):
            raise _error("invalid-column-order", "ResultSchema.columns", "must be nonempty with unique names.")
        if self.row_identity.permutation_key_column not in names:
            raise _error("missing-permutation-key", "ResultSchema.row_identity", "must name a physical column.")
        object.__setattr__(self, "columns", columns)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def bytes_per_row(self) -> int:
        return sum(column.bytes_per_row for column in self.columns)

    def column(self, name: str) -> ResultColumnSpec:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(name)


_SLOT_IDS = tuple(slot.value for slot in RESULT_SLOT_ORDER)
_CATEGORY_IDS = tuple(category.value for category in RESULT_CATEGORY_ORDER)
_PRIMARY_STAT_IDS = tuple(stat.value for stat in RESULT_PRIMARY_STAT_ORDER)

RESULT_SCHEMA = ResultSchema(
    schema_id=RESULT_SCHEMA_ID,
    version=RESULT_SCHEMA_VERSION,
    maximum_rows=MAX_RESULT_CAP,
    row_identity=ResultRowIdentitySpec(
        ordinal_dtype=_U4,
        maximum_ordinal=RESULT_MAX_ROW_ORDINAL,
        stored=False,
        permutation_key_column="dense_item_ids",
    ),
    columns=(
        ResultColumnSpec("dense_item_ids", _I4, (6,), _SLOT_IDS),
        ResultColumnSpec("owned_set_indices", _U1, (6,), _SLOT_IDS),
        ResultColumnSpec("category_codes", _U1, (), code_values=_CATEGORY_IDS),
        ResultColumnSpec("replacement_distances", _U1, (), code_values=_CATEGORY_IDS),
        ResultColumnSpec("effective_final_stats", _I8, (8,), _PRIMARY_STAT_IDS),
        ResultColumnSpec("raw_critical_hit_chances", _I8, ()),
        ResultColumnSpec("derived_metrics", _I8, (15,), RESULT_DERIVED_METRIC_ORDER),
        ResultColumnSpec("priority_scores", _F4, ()),
        ResultColumnSpec("constraint_distances", _F4, ()),
        ResultColumnSpec("equipped_item_counts", _U1, ()),
    ),
)
RESULT_COLUMN_NAMES = RESULT_SCHEMA.column_names
RESULT_ROW_BYTES = RESULT_SCHEMA.bytes_per_row


@dataclass(frozen=True, slots=True)
class ResultPayloadProjection:
    row_count: int
    bytes_per_row: int
    payload_bytes: int
    fixed_header_bytes: int = 0
    fixed_manifest_bytes: int = 0
    fixed_index_bytes: int = 0

    @property
    def total_fixed_bytes(self) -> int:
        return self.payload_bytes + self.fixed_header_bytes + self.fixed_manifest_bytes + self.fixed_index_bytes

    @property
    def payload_mib(self) -> float:
        return self.payload_bytes / (1 << 20)

    @property
    def payload_gib(self) -> float:
        return self.payload_bytes / (1 << 30)


def project_result_payload(row_count: object) -> ResultPayloadProjection:
    count = _integer(row_count, "row_count", minimum=0, maximum=RESULT_SCHEMA.maximum_rows)
    return ResultPayloadProjection(count, RESULT_ROW_BYTES, count * RESULT_ROW_BYTES)


def validate_row_ordinal(value: object) -> int:
    return _integer(value, "row_ordinal", minimum=0, maximum=RESULT_MAX_ROW_ORDINAL)


def validate_dense_item_ids(value: object) -> tuple[int, ...]:
    try:
        supplied = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-dense-item-ids", "dense_item_ids", "must be a six-entry sequence.") from None
    if len(supplied) != 6:
        raise _error("dense-item-id-length", "dense_item_ids", "must contain exactly six entries.")
    dense_ids = tuple(
        _integer(item, f"dense_item_ids[{index}]", minimum=0, maximum=RESULT_MAX_DENSE_ITEM_ID)
        for index, item in enumerate(supplied)
    )
    if len(set(dense_ids)) != 6:
        raise _error("duplicate-dense-item-id", "dense_item_ids", "must contain six unique IDs.")
    return dense_ids


def validate_set_signature(value: object) -> tuple[int, ...]:
    try:
        supplied = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-set-signature", "owned_set_indices", "must be a six-entry sequence.") from None
    if len(supplied) != 6:
        raise _error("set-signature-length", "owned_set_indices", "must contain exactly six entries.")
    return tuple(
        _integer(item, f"owned_set_indices[{index}]", minimum=0, maximum=len(FRIBBELS_SET_ORDER) - 1)
        for index, item in enumerate(supplied)
    )


def set_piece_counts(value: object) -> tuple[int, ...]:
    signature = validate_set_signature(value)
    return tuple(signature.count(index) for index in range(len(FRIBBELS_SET_ORDER)))


def set_activation_counts(value: object) -> tuple[int, ...]:
    counts = set_piece_counts(value)
    activations = []
    for gear_set, piece_count in zip(FRIBBELS_SET_ORDER, counts, strict=True):
        metadata = SET_CATALOG[gear_set]
        completed = piece_count // metadata.pieces_required
        activations.append(completed if metadata.stackable else min(completed, 1))
    return tuple(activations)


def encode_result_category(value: object) -> int:
    try:
        category = value if isinstance(value, ResultCategory) else ResultCategory(value)
    except (TypeError, ValueError):
        raise _error("invalid-result-category", "category", "must be exact, one-away, or two-away.") from None
    return RESULT_CATEGORY_ORDER.index(category)


def decode_result_category(value: object) -> ResultCategory:
    code = _integer(value, "category_code", minimum=0, maximum=len(RESULT_CATEGORY_ORDER) - 1)
    return RESULT_CATEGORY_ORDER[code]


@dataclass(frozen=True, slots=True)
class ReplacementMetadataReference:
    """Lazy deterministic key; created only for a requested near-set row."""

    schema_version: int
    row_ordinal: int
    category: ResultCategory
    dense_item_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.schema_version != RESULT_SCHEMA_VERSION:
            raise _error("replacement-schema-version", "schema_version", f"must be {RESULT_SCHEMA_VERSION}.")
        ordinal = validate_row_ordinal(self.row_ordinal)
        try:
            category = self.category if isinstance(self.category, ResultCategory) else ResultCategory(self.category)
        except (TypeError, ValueError):
            raise _error("invalid-result-category", "category", "must be exact, one-away, or two-away.") from None
        if category is ResultCategory.EXACT:
            raise _error("exact-replacement-reference", "category", "exact rows use the no-reference sentinel.")
        dense_ids = validate_dense_item_ids(self.dense_item_ids)
        object.__setattr__(self, "row_ordinal", ordinal)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "dense_item_ids", dense_ids)


def replacement_metadata_reference(
    row_ordinal: object,
    category: object,
    dense_item_ids: object,
) -> ReplacementMetadataReference | None:
    normalized_category = decode_result_category(encode_result_category(category))
    ordinal = validate_row_ordinal(row_ordinal)
    ids = validate_dense_item_ids(dense_item_ids)
    if normalized_category is ResultCategory.EXACT:
        return NO_REPLACEMENT_METADATA_REFERENCE
    return ReplacementMetadataReference(RESULT_SCHEMA_VERSION, ordinal, normalized_category, ids)


def _require_array(
    arrays: Mapping[str, np.ndarray[Any, Any]],
    spec: ResultColumnSpec,
    row_count: int,
) -> np.ndarray[Any, Any]:
    value = arrays[spec.name]
    if not isinstance(value, np.ndarray):
        raise _error("invalid-column-array", spec.name, "must be a NumPy ndarray.")
    expected_shape = (row_count, *spec.shape)
    if value.shape != expected_shape:
        raise _error("column-shape-mismatch", spec.name, f"must have shape {expected_shape!r}; found {value.shape!r}.")
    if value.dtype.str != spec.dtype.str:
        raise _error("column-dtype-mismatch", spec.name, f"must use exact dtype {spec.dtype.str}; found {value.dtype.str}.")
    if not value.flags.c_contiguous:
        raise _error("noncontiguous-column", spec.name, "must be C-contiguous.")
    return value


def validate_result_columns(
    arrays: Mapping[str, np.ndarray[Any, Any]],
    *,
    row_count: object | None = None,
) -> int:
    """Validate a complete physical column batch without copying or narrowing."""

    if not isinstance(arrays, Mapping):
        raise _error("invalid-column-mapping", "arrays", "must be an ordered mapping.")
    if tuple(arrays) != RESULT_COLUMN_NAMES:
        raise _error("column-order-mismatch", "arrays", f"must use canonical order {RESULT_COLUMN_NAMES!r}.")
    first = arrays[RESULT_COLUMN_NAMES[0]]
    inferred = first.shape[0] if isinstance(first, np.ndarray) and first.ndim else 0
    count = inferred if row_count is None else _integer(row_count, "row_count", minimum=0, maximum=MAX_RESULT_CAP)
    if count > MAX_RESULT_CAP:
        raise _error("row-count-overflow", "row_count", f"must not exceed {MAX_RESULT_CAP}.")
    checked = {spec.name: _require_array(arrays, spec, count) for spec in RESULT_SCHEMA.columns}

    dense = checked["dense_item_ids"]
    if np.any(dense < 0):
        raise _error("dense-item-id-out-of-range", "dense_item_ids", "must be nonnegative signed 32-bit IDs.")
    sorted_dense = np.sort(dense, axis=1)
    if count and np.any(sorted_dense[:, 1:] == sorted_dense[:, :-1]):
        raise _error("duplicate-dense-item-id", "dense_item_ids", "every row must contain six unique IDs.")
    sets = checked["owned_set_indices"]
    if np.any(sets >= len(FRIBBELS_SET_ORDER)):
        raise _error("set-index-out-of-range", "owned_set_indices", f"must contain set indices 0..{len(FRIBBELS_SET_ORDER) - 1}.")
    categories = checked["category_codes"]
    distances = checked["replacement_distances"]
    if np.any(categories >= len(RESULT_CATEGORY_ORDER)) or not np.array_equal(categories, distances):
        raise _error("category-distance-mismatch", "category_codes", "category and replacement distance must be equal codes 0..2.")
    if np.any(checked["effective_final_stats"] < 0):
        raise _error("negative-primary-stat", "effective_final_stats", "effective final stats must be nonnegative.")
    if np.any(checked["raw_critical_hit_chances"] < 0):
        raise _error(
            "negative-raw-critical-hit-chance",
            "raw_critical_hit_chances",
            "raw critical hit chances must be nonnegative.",
        )
    priority = checked["priority_scores"]
    if not np.all(np.isfinite(priority)):
        raise _error("nonfinite-priority", "priority_scores", "must contain finite binary32 values.")
    constraint = checked["constraint_distances"]
    if not np.all(np.isfinite(constraint)) or np.any(constraint < 0):
        raise _error("invalid-constraint-distance", "constraint_distances", "must contain finite nonnegative binary32 values.")
    if np.any(constraint[categories == 0] != 0):
        raise _error("exact-constraint-distance", "constraint_distances", "exact rows must have zero normalized distance.")
    if np.any(checked["equipped_item_counts"] > 6):
        raise _error("equipped-count-out-of-range", "equipped_item_counts", "must contain values from 0 through 6.")
    return count


__all__ = [
    "NO_REPLACEMENT_METADATA_REFERENCE",
    "RESULT_CATEGORY_ORDER",
    "RESULT_COLUMN_NAMES",
    "RESULT_DERIVED_METRIC_ORDER",
    "RESULT_MAX_DENSE_ITEM_ID",
    "RESULT_MAX_ROW_ORDINAL",
    "RESULT_PRIMARY_STAT_ORDER",
    "RESULT_ROW_BYTES",
    "RESULT_SCHEMA",
    "RESULT_SCHEMA_ID",
    "RESULT_SCHEMA_VERSION",
    "RESULT_SLOT_ORDER",
    "ReplacementMetadataReference",
    "ResultColumnSpec",
    "ResultPayloadProjection",
    "ResultRowIdentitySpec",
    "ResultSchema",
    "ResultSchemaError",
    "decode_result_category",
    "encode_result_category",
    "project_result_payload",
    "replacement_metadata_reference",
    "set_activation_counts",
    "set_piece_counts",
    "validate_dense_item_ids",
    "validate_result_columns",
    "validate_row_ordinal",
    "validate_set_signature",
]
