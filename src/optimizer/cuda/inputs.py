"""Compact numeric host inputs and lazy device-buffer ownership for CUDA search."""

from __future__ import annotations

import importlib
import math
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Any

import numpy as np

from src.optimizer.domain import FINAL_STAT_ORDER, FRIBBELS_SET_ORDER, SET_CATALOG, GearSet
from src.optimizer.engine.set_evaluation import (
    _APPLICATION_BY_FINAL_STAT,
    _numeric_contribution,
)
from src.optimizer.engine.priority_scoring import calculate_item_priority_score
from src.optimizer.engine.stat_aggregation import _f32
from src.optimizer.search.cartesian import CARTESIAN_SLOT_COUNT
from src.optimizer.search.exact_evaluation import (
    DERIVED_METRIC_COUNT,
    FINAL_STAT_COUNT,
    ExactBuildEvaluationContext,
    ExactBuildEvaluationError,
    validate_exact_build_search_context,
)
from src.optimizer.search.set_patterns import SET_PATTERN_VECTOR_LENGTH
from src.optimizer.search.slot_arrays import SearchReadySlotArrays


CUDA_SIGNED_INT8_MIN = -(1 << 7)
CUDA_SIGNED_INT8_MAX = (1 << 7) - 1
CUDA_UNSIGNED_INT8_MAX = (1 << 8) - 1
CUDA_SIGNED_INT32_MAX = (1 << 31) - 1
CUDA_SIGNED_INT64_MAX = (1 << 63) - 1
CUDA_SKILL_COUNT = 3

_I1 = np.dtype("i1")
_U1 = np.dtype("u1")
_I4 = np.dtype("<i4")
_I8 = np.dtype("<i8")
_F4 = np.dtype("<f4")

_ITEM_COUNT = "item_count"
_NUMERIC_SET_OPERATION_COUNT = sum(
    len(_APPLICATION_BY_FINAL_STAT[stat]) for stat in FINAL_STAT_ORDER
)


class CudaInputError(ValueError):
    """Actionable fixed-width compilation or device-transfer failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> CudaInputError:
    return CudaInputError(code, path, message)


@dataclass(frozen=True, slots=True)
class CudaInputFieldSpec:
    """One canonical structure-of-arrays field declaration."""

    name: str
    dtype: np.dtype[Any]
    dimensions: tuple[int | str, ...]


_FIELD_SPECS = (
    CudaInputFieldSpec("slot_offsets", _I8, (CARTESIAN_SLOT_COUNT,)),
    CudaInputFieldSpec("slot_radices", _I8, (CARTESIAN_SLOT_COUNT,)),
    CudaInputFieldSpec("total_permutations", _I8, (1,)),
    CudaInputFieldSpec("dense_item_ids", _I4, (_ITEM_COUNT,)),
    CudaInputFieldSpec("item_set_indices", _U1, (_ITEM_COUNT,)),
    CudaInputFieldSpec("item_stat_contributions", _F4, (_ITEM_COUNT, FINAL_STAT_COUNT)),
    CudaInputFieldSpec("item_gear_scores", _I4, (_ITEM_COUNT,)),
    CudaInputFieldSpec("item_priority_scores", _I4, (_ITEM_COUNT,)),
    CudaInputFieldSpec("projection_mode_code", _U1, (1,)),
    CudaInputFieldSpec("base_stats", _F4, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("final_stat_multipliers", _F4, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("set_insertion_base_stats", _F4, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("post_set_modifier_contributions", _F4, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("artifact_flat_stats", _F4, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("required_piece_counts", _U1, (SET_PATTERN_VECTOR_LENGTH,)),
    CudaInputFieldSpec("target_activation_counts", _U1, (SET_PATTERN_VECTOR_LENGTH,)),
    CudaInputFieldSpec(
        "target_numeric_set_contributions",
        _F4,
        (SET_PATTERN_VECTOR_LENGTH, FINAL_STAT_COUNT),
    ),
    CudaInputFieldSpec("set_pieces_required", _U1, (SET_PATTERN_VECTOR_LENGTH,)),
    CudaInputFieldSpec("set_stackable_flags", _U1, (SET_PATTERN_VECTOR_LENGTH,)),
    CudaInputFieldSpec(
        "set_unit_numeric_contributions",
        _F4,
        (SET_PATTERN_VECTOR_LENGTH, FINAL_STAT_COUNT),
    ),
    CudaInputFieldSpec(
        "numeric_set_operation_indices",
        _U1,
        (_NUMERIC_SET_OPERATION_COUNT,),
    ),
    CudaInputFieldSpec(
        "numeric_set_operation_stat_indices",
        _U1,
        (_NUMERIC_SET_OPERATION_COUNT,),
    ),
    CudaInputFieldSpec("set_penetration_flags", _U1, (SET_PATTERN_VECTOR_LENGTH,)),
    CudaInputFieldSpec("set_percent_damage_bonuses", _F4, (SET_PATTERN_VECTOR_LENGTH,)),
    CudaInputFieldSpec("primary_minimum_values", _F4, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("primary_minimum_present", _U1, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("primary_maximum_values", _F4, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("primary_maximum_present", _U1, (FINAL_STAT_COUNT,)),
    CudaInputFieldSpec("derived_minimum_values", _F4, (DERIVED_METRIC_COUNT,)),
    CudaInputFieldSpec("derived_minimum_present", _U1, (DERIVED_METRIC_COUNT,)),
    CudaInputFieldSpec("derived_maximum_values", _F4, (DERIVED_METRIC_COUNT,)),
    CudaInputFieldSpec("derived_maximum_present", _U1, (DERIVED_METRIC_COUNT,)),
    CudaInputFieldSpec("metric_target_defense", _F4, (1,)),
    CudaInputFieldSpec("penetration_bonus_multiplier", _F4, (1,)),
    CudaInputFieldSpec("target_penetration_set_multiplier", _F4, (1,)),
    CudaInputFieldSpec("target_percent_damage_multiplier", _F4, (1,)),
    CudaInputFieldSpec("maximum_replacement_distance", _U1, (1,)),
    CudaInputFieldSpec("near_set_tolerance", _F4, (1,)),
    CudaInputFieldSpec("skill_indices", _U1, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_kind_codes", _U1, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_hit_type_codes", _I1, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_rates", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_powers", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_self_hp_scaling", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_self_attack_scaling", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_self_defense_scaling", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_self_speed_scaling", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_extra_attack_scaling", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_extra_defense_scaling", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_increased_values", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_critical_damage_increases", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_target_defenses", _F4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_target_counts", _I4, (CUDA_SKILL_COUNT,)),
    CudaInputFieldSpec("skill_penetrations", _F4, (CUDA_SKILL_COUNT,)),
)

CUDA_INPUT_LAYOUT = tuple(_FIELD_SPECS)
CUDA_INPUT_FIELD_NAMES = tuple(spec.name for spec in _FIELD_SPECS)
_SPEC_BY_NAME = {spec.name: spec for spec in _FIELD_SPECS}


def _expected_shape(spec: CudaInputFieldSpec, item_count: int) -> tuple[int, ...]:
    return tuple(item_count if value == _ITEM_COUNT else int(value) for value in spec.dimensions)


def _immutable_array(values: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    contiguous = np.ascontiguousarray(values)
    frozen = np.frombuffer(contiguous.tobytes(order="C"), dtype=contiguous.dtype)
    return frozen.reshape(contiguous.shape)


@dataclass(frozen=True, slots=True, eq=False)
class CudaHostArray:
    """One deeply immutable, C-contiguous host field."""

    name: str
    values: np.ndarray[Any, Any]

    def __post_init__(self) -> None:
        if self.name not in _SPEC_BY_NAME:
            raise _error("unknown-host-field", "CudaHostArray.name", f"unknown field {self.name!r}.")
        if not isinstance(self.values, np.ndarray):
            raise _error("invalid-host-array", f"CudaHostArray.{self.name}", "must be a NumPy array.")
        frozen = _immutable_array(self.values)
        object.__setattr__(self, "values", frozen)

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
            isinstance(other, CudaHostArray)
            and self.name == other.name
            and self.dtype == other.dtype
            and self.shape == other.shape
            and bool(np.array_equal(self.values, other.values))
        )

    def __hash__(self) -> int:
        return hash((self.name, self.dtype.str, self.shape, self.values.tobytes(order="C")))


@dataclass(frozen=True, slots=True)
class CudaSearchDimensions:
    """Validated six-slot dimensions that fit the CUDA signed-width contract."""

    radices: tuple[int, ...]
    slot_offsets: tuple[int, ...]
    total_items: int
    total_permutations: int


def _python_integer(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _error("invalid-integer", path, "must be an integer; booleans are not accepted.")
    result = int(value)
    if result < minimum:
        raise _error("integer-out-of-range", path, f"must be at least {minimum}; found {result}.")
    return result


def validate_cuda_search_dimensions(radices: Sequence[int]) -> CudaSearchDimensions:
    """Guard CPU-arbitrary-precision dimensions before any fixed-width allocation."""

    try:
        checked = tuple(radices)
    except TypeError:
        raise _error("invalid-radices", "radices", "must be a six-entry integer sequence.") from None
    if len(checked) != CARTESIAN_SLOT_COUNT:
        raise _error(
            "cuda-slot-count",
            "radices",
            f"must contain exactly {CARTESIAN_SLOT_COUNT} canonical slot lengths.",
        )
    normalized_values: list[int] = []
    for index, value in enumerate(checked):
        normalized = _python_integer(value, f"radices[{index}]", minimum=0)
        if normalized == 0:
            raise _error(
                "empty-cuda-slot",
                f"radices[{index}]",
                "CUDA search requires at least one retained item in every canonical slot.",
            )
        normalized_values.append(normalized)
    normalized = tuple(normalized_values)
    total_permutations = math.prod(normalized)
    if total_permutations > CUDA_SIGNED_INT64_MAX:
        raise _error(
            "permutation-total-overflow",
            "radices",
            "Cartesian product exceeds signed 64-bit CUDA flat-index capacity; use the CPU path or stricter gear filters.",
        )
    total_items = sum(normalized)
    if total_items - 1 > CUDA_SIGNED_INT32_MAX:
        raise _error(
            "dense-id-overflow",
            "radices",
            "flattened item count exceeds signed 32-bit dense-ID capacity; use the CPU path or stricter gear filters.",
        )
    offsets: list[int] = []
    next_offset = 0
    for radix in normalized:
        offsets.append(next_offset)
        next_offset += radix
    return CudaSearchDimensions(
        radices=normalized,
        slot_offsets=tuple(offsets),
        total_items=total_items,
        total_permutations=total_permutations,
    )


def _numeric_values(values: object, path: str) -> tuple[object, ...]:
    try:
        supplied = np.asarray(values, dtype=object)
    except Exception as error:
        raise _error("invalid-array-values", path, f"could not read numeric values: {error}") from error
    return tuple(supplied.flat)


def _fixed_integer_array(
    name: str,
    values: object,
    dtype: np.dtype[Any],
    shape: tuple[int, ...],
) -> CudaHostArray:
    info = np.iinfo(dtype)
    checked: list[int] = []
    for index, value in enumerate(_numeric_values(values, name)):
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise _error("invalid-integer", f"{name}[{index}]", "must be an integer; booleans are not accepted.")
        numeric = int(value)
        if numeric < int(info.min) or numeric > int(info.max):
            raise _error(
                "integer-overflow",
                f"{name}[{index}]",
                f"value {numeric} cannot fit {dtype.name} ({info.min}..{info.max}).",
            )
        checked.append(numeric)
    if len(checked) != math.prod(shape):
        raise _error("array-shape", name, f"must have shape {shape!r}.")
    return CudaHostArray(name, np.asarray(checked, dtype=dtype).reshape(shape))


def _binary32_array(name: str, values: object, shape: tuple[int, ...]) -> CudaHostArray:
    checked: list[float] = []
    for index, value in enumerate(_numeric_values(values, name)):
        if isinstance(value, bool) or not isinstance(value, Real):
            raise _error("invalid-number", f"{name}[{index}]", "must be a finite number.")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise _error("invalid-number", f"{name}[{index}]", "must be a finite number.")
        with np.errstate(over="ignore", invalid="ignore"):
            narrowed = np.float32(numeric)
        if not np.isfinite(narrowed):
            raise _error(
                "binary32-overflow",
                f"{name}[{index}]",
                f"value {numeric!r} cannot fit finite binary32.",
            )
        checked.append(float(narrowed))
    if len(checked) != math.prod(shape):
        raise _error("array-shape", name, f"must have shape {shape!r}.")
    return CudaHostArray(name, np.asarray(checked, dtype=_F4).reshape(shape))


def _range_arrays(
    prefix: str,
    values: Sequence[float | None],
    length: int,
) -> tuple[CudaHostArray, CudaHostArray]:
    if len(values) != length:
        raise _error("array-shape", prefix, f"must contain exactly {length} entries.")
    numeric_values = tuple(0 if value is None else value for value in values)
    present = tuple(0 if value is None else 1 for value in values)
    return (
        _binary32_array(f"{prefix}_values", numeric_values, (length,)),
        _fixed_integer_array(f"{prefix}_present", present, _U1, (length,)),
    )


def _binary32_divide(left: int | float, right: int | float) -> float:
    denominator = _f32(right)
    return _f32(_f32(left) / denominator)


def _penetration_bonus(target_defense: float) -> float:
    numerator = _f32(_binary32_divide(target_defense, 300) + _f32(1))
    denominator = _f32(_f32(_f32(0.00283333) * _f32(target_defense)) + _f32(1))
    return _binary32_divide(numerator, denominator)


def _set_numeric_inputs(base_stats: Sequence[float]) -> dict[str, tuple[object, ...]]:
    base = dict(zip(FINAL_STAT_ORDER, base_stats, strict=True))
    unit_rows = []
    for gear_set in FRIBBELS_SET_ORDER:
        contribution = dict(_numeric_contribution(gear_set, 1, base))
        unit_rows.append(tuple(_f32(contribution.get(stat, 0)) for stat in FINAL_STAT_ORDER))

    operation_sets: list[int] = []
    operation_stats: list[int] = []
    for stat_index, stat in enumerate(FINAL_STAT_ORDER):
        for gear_set in _APPLICATION_BY_FINAL_STAT[stat]:
            operation_sets.append(SET_CATALOG[gear_set].fribbels_index)
            operation_stats.append(stat_index)

    percent_damage = [0.0] * SET_PATTERN_VECTOR_LENGTH
    percent_damage[SET_CATALOG[GearSet.RAGE].fribbels_index] = _f32(0.3)
    percent_damage[SET_CATALOG[GearSet.TORRENT].fribbels_index] = _f32(0.1)
    percent_damage[SET_CATALOG[GearSet.FERVOR].fribbels_index] = _f32(0.2)
    penetration_flags = [0] * SET_PATTERN_VECTOR_LENGTH
    penetration_flags[SET_CATALOG[GearSet.PENETRATION].fribbels_index] = 1
    return {
        "pieces": tuple(SET_CATALOG[item].pieces_required for item in FRIBBELS_SET_ORDER),
        "stackable": tuple(int(SET_CATALOG[item].stackable) for item in FRIBBELS_SET_ORDER),
        "unit_rows": tuple(unit_rows),
        "operation_sets": tuple(operation_sets),
        "operation_stats": tuple(operation_stats),
        "penetration": tuple(penetration_flags),
        "percent_damage": tuple(percent_damage),
    }


@dataclass(frozen=True, slots=True)
class CudaHostInputs:
    """Complete numeric host record; stable identities and domain objects are absent."""

    arrays: tuple[CudaHostArray, ...]

    def __post_init__(self) -> None:
        arrays = tuple(self.arrays)
        if tuple(item.name for item in arrays) != CUDA_INPUT_FIELD_NAMES:
            raise _error(
                "host-layout-order",
                "CudaHostInputs.arrays",
                "must contain every canonical CUDA input field exactly once and in order.",
            )
        dense = arrays[CUDA_INPUT_FIELD_NAMES.index("dense_item_ids")]
        item_count = dense.shape[0]
        for spec, item in zip(_FIELD_SPECS, arrays, strict=True):
            expected_shape = _expected_shape(spec, item_count)
            if item.dtype != spec.dtype or item.shape != expected_shape:
                raise _error(
                    "host-layout-mismatch",
                    f"CudaHostInputs.{spec.name}",
                    f"must use dtype {spec.dtype.name} and shape {expected_shape!r}; found {item.dtype.name} {item.shape!r}.",
                )
        by_name = {item.name: item.values for item in arrays}
        dimensions = validate_cuda_search_dimensions(
            tuple(int(value) for value in by_name["slot_radices"])
        )
        if dimensions.slot_offsets != tuple(int(value) for value in by_name["slot_offsets"]):
            raise _error(
                "slot-offset-mismatch",
                "CudaHostInputs.slot_offsets",
                "must contain the six canonical starts derived from slot_radices.",
            )
        if dimensions.total_items != item_count:
            raise _error(
                "flattened-item-count-mismatch",
                "CudaHostInputs.dense_item_ids",
                "length must equal the sum of slot_radices.",
            )
        if dimensions.total_permutations != int(by_name["total_permutations"][0]):
            raise _error(
                "cartesian-total-mismatch",
                "CudaHostInputs.total_permutations",
                "must equal the exact product of slot_radices.",
            )
        dense_ids = by_name["dense_item_ids"]
        if any(int(value) != index for index, value in enumerate(dense_ids)):
            raise _error(
                "noncanonical-dense-ids",
                "CudaHostInputs.dense_item_ids",
                "must be contiguous from zero in flattened slot order.",
            )
        if np.any(by_name["item_set_indices"] >= SET_PATTERN_VECTOR_LENGTH):
            raise _error(
                "set-index-out-of-range",
                "CudaHostInputs.item_set_indices",
                "entries must be canonical Fribbels set indices 0..23.",
            )
        flag_fields = (
            "set_stackable_flags",
            "set_penetration_flags",
            "primary_minimum_present",
            "primary_maximum_present",
            "derived_minimum_present",
            "derived_maximum_present",
        )
        for name in flag_fields:
            if np.any(by_name[name] > 1):
                raise _error(
                    "invalid-flag",
                    f"CudaHostInputs.{name}",
                    "flags must contain only numeric 0 or 1 values.",
                )
        object.__setattr__(self, "arrays", arrays)

    def array(self, name: str) -> np.ndarray[Any, Any]:
        try:
            index = CUDA_INPUT_FIELD_NAMES.index(name)
        except ValueError:
            raise KeyError(name) from None
        return self.arrays[index].values

    @property
    def item_count(self) -> int:
        return int(self.array("dense_item_ids").shape[0])

    @property
    def total_permutations(self) -> int:
        return int(self.array("total_permutations")[0])

    @property
    def byte_count(self) -> int:
        return sum(item.nbytes for item in self.arrays)

    @property
    def layout_signature(self) -> tuple[tuple[str, str, tuple[int, ...]], ...]:
        return tuple((item.name, item.dtype.str, item.shape) for item in self.arrays)


def compile_cuda_host_inputs(
    slot_arrays: SearchReadySlotArrays,
    context: ExactBuildEvaluationContext,
) -> CudaHostInputs:
    """Compile validated CPU oracle state into the fixed-width CUDA input layout."""

    if not isinstance(slot_arrays, SearchReadySlotArrays):
        raise _error("invalid-search-arrays", "slot_arrays", "must be SearchReadySlotArrays.")
    if not isinstance(context, ExactBuildEvaluationContext):
        raise _error("invalid-exact-context", "context", "must be ExactBuildEvaluationContext.")
    try:
        radices = validate_exact_build_search_context(context, slot_arrays)
    except ExactBuildEvaluationError as error:
        raise _error(error.code, error.path, error.message) from error
    dimensions = validate_cuda_search_dimensions(radices)
    if dimensions.total_items != slot_arrays.total_items:
        raise _error(
            "flattened-item-count-mismatch",
            "slot_arrays.total_items",
            "must equal the sum of the six canonical slot radices.",
        )

    dense_ids = tuple(value for slot in slot_arrays.slots for value in slot.dense_ids)
    if dense_ids != tuple(range(dimensions.total_items)):
        raise _error(
            "noncanonical-dense-ids",
            "slot_arrays.slots",
            "dense IDs must be contiguous in canonical flattened slot order.",
        )
    set_indices = tuple(value for slot in slot_arrays.slots for value in slot.set_indices)
    contributions = tuple(
        row for slot in slot_arrays.slots for row in slot.final_stat_contributions
    )
    gear_scores = tuple(value for slot in slot_arrays.slots for value in slot.gear_scores)
    try:
        priority_scores = tuple(
            calculate_item_priority_score(
                context.base_stats,
                context.priorities,
                row,
            )[1]
            for row in contributions
        )
    except (ValueError, OverflowError) as error:
        raise _error(
            "binary32-overflow",
            "item_priority_scores",
            f"per-item priority calculation could not be represented: {error}",
        ) from error
    set_inputs = _set_numeric_inputs(context.base_stats)

    arrays: list[CudaHostArray] = [
        _fixed_integer_array("slot_offsets", dimensions.slot_offsets, _I8, (CARTESIAN_SLOT_COUNT,)),
        _fixed_integer_array("slot_radices", dimensions.radices, _I8, (CARTESIAN_SLOT_COUNT,)),
        _fixed_integer_array("total_permutations", (dimensions.total_permutations,), _I8, (1,)),
        _fixed_integer_array("dense_item_ids", dense_ids, _I4, (dimensions.total_items,)),
        _fixed_integer_array("item_set_indices", set_indices, _U1, (dimensions.total_items,)),
        _binary32_array(
            "item_stat_contributions",
            contributions,
            (dimensions.total_items, FINAL_STAT_COUNT),
        ),
        _fixed_integer_array("item_gear_scores", gear_scores, _I4, (dimensions.total_items,)),
        _fixed_integer_array(
            "item_priority_scores",
            priority_scores,
            _I4,
            (dimensions.total_items,),
        ),
        _fixed_integer_array("projection_mode_code", (context.projection_mode_code,), _U1, (1,)),
        _binary32_array("base_stats", context.base_stats, (FINAL_STAT_COUNT,)),
        _binary32_array("final_stat_multipliers", context.final_stat_multipliers, (FINAL_STAT_COUNT,)),
        _binary32_array("set_insertion_base_stats", context.set_insertion_base_stats, (FINAL_STAT_COUNT,)),
        _binary32_array(
            "post_set_modifier_contributions",
            context.post_set_modifier_contributions,
            (FINAL_STAT_COUNT,),
        ),
        _binary32_array("artifact_flat_stats", context.artifact_flat_stats, (FINAL_STAT_COUNT,)),
        _fixed_integer_array(
            "required_piece_counts",
            context.required_piece_counts,
            _U1,
            (SET_PATTERN_VECTOR_LENGTH,),
        ),
        _fixed_integer_array(
            "target_activation_counts",
            context.activation_counts,
            _U1,
            (SET_PATTERN_VECTOR_LENGTH,),
        ),
        _binary32_array(
            "target_numeric_set_contributions",
            context.numeric_set_contributions,
            (SET_PATTERN_VECTOR_LENGTH, FINAL_STAT_COUNT),
        ),
        _fixed_integer_array(
            "set_pieces_required", set_inputs["pieces"], _U1, (SET_PATTERN_VECTOR_LENGTH,)
        ),
        _fixed_integer_array(
            "set_stackable_flags", set_inputs["stackable"], _U1, (SET_PATTERN_VECTOR_LENGTH,)
        ),
        _binary32_array(
            "set_unit_numeric_contributions",
            set_inputs["unit_rows"],
            (SET_PATTERN_VECTOR_LENGTH, FINAL_STAT_COUNT),
        ),
        _fixed_integer_array(
            "numeric_set_operation_indices",
            set_inputs["operation_sets"],
            _U1,
            (_NUMERIC_SET_OPERATION_COUNT,),
        ),
        _fixed_integer_array(
            "numeric_set_operation_stat_indices",
            set_inputs["operation_stats"],
            _U1,
            (_NUMERIC_SET_OPERATION_COUNT,),
        ),
        _fixed_integer_array(
            "set_penetration_flags",
            set_inputs["penetration"],
            _U1,
            (SET_PATTERN_VECTOR_LENGTH,),
        ),
        _binary32_array(
            "set_percent_damage_bonuses",
            set_inputs["percent_damage"],
            (SET_PATTERN_VECTOR_LENGTH,),
        ),
    ]
    arrays.extend(_range_arrays("primary_minimum", context.primary_minimums, FINAL_STAT_COUNT))
    arrays.extend(_range_arrays("primary_maximum", context.primary_maximums, FINAL_STAT_COUNT))
    arrays.extend(_range_arrays("derived_minimum", context.derived_minimums, DERIVED_METRIC_COUNT))
    arrays.extend(_range_arrays("derived_maximum", context.derived_maximums, DERIVED_METRIC_COUNT))
    arrays.extend(
        (
            _binary32_array("metric_target_defense", (context.metric_target_defense,), (1,)),
            _binary32_array(
                "penetration_bonus_multiplier",
                (_penetration_bonus(context.metric_target_defense),),
                (1,),
            ),
            _binary32_array(
                "target_penetration_set_multiplier",
                (context.penetration_set_multiplier,),
                (1,),
            ),
            _binary32_array(
                "target_percent_damage_multiplier",
                (context.percent_damage_multiplier,),
                (1,),
            ),
            _fixed_integer_array(
                "maximum_replacement_distance",
                (0,),
                _U1,
                (1,),
            ),
            _binary32_array("near_set_tolerance", (0,), (1,)),
        )
    )
    skills = context.skills
    integer_skill_fields = (
        ("skill_indices", "skill_index", _U1),
        ("skill_kind_codes", "kind_code", _U1),
        ("skill_hit_type_codes", "hit_type_code", _I1),
    )
    for name, attribute, dtype in integer_skill_fields:
        arrays.append(
            _fixed_integer_array(
                name,
                tuple(getattr(skill, attribute) for skill in skills),
                dtype,
                (CUDA_SKILL_COUNT,),
            )
        )
    float_skill_fields = (
        ("skill_rates", "rate"),
        ("skill_powers", "power"),
        ("skill_self_hp_scaling", "self_hp_scaling"),
        ("skill_self_attack_scaling", "self_attack_scaling"),
        ("skill_self_defense_scaling", "self_defense_scaling"),
        ("skill_self_speed_scaling", "self_speed_scaling"),
        ("skill_extra_attack_scaling", "extra_attack_scaling"),
        ("skill_extra_defense_scaling", "extra_defense_scaling"),
        ("skill_increased_values", "increased_value"),
        ("skill_critical_damage_increases", "critical_damage_increase"),
        ("skill_target_defenses", "target_defense"),
    )
    for name, attribute in float_skill_fields:
        arrays.append(
            _binary32_array(
                name,
                tuple(getattr(skill, attribute) for skill in skills),
                (CUDA_SKILL_COUNT,),
            )
        )
    arrays.append(
        _fixed_integer_array(
            "skill_target_counts",
            tuple(skill.target_count for skill in skills),
            _I4,
            (CUDA_SKILL_COUNT,),
        )
    )
    arrays.append(
        _binary32_array(
            "skill_penetrations",
            tuple(skill.penetration for skill in skills),
            (CUDA_SKILL_COUNT,),
        )
    )
    return CudaHostInputs(tuple(arrays))


@dataclass(frozen=True, slots=True, eq=False)
class CudaDeviceArray:
    """One named device allocation borrowed by a live lease."""

    name: str
    dtype: np.dtype[Any]
    shape: tuple[int, ...]
    value: object


def _load_cupy_array_api() -> object:
    return importlib.import_module("cupy")


def _device_scope(api: object, device_index: int) -> AbstractContextManager[object]:
    try:
        return api.cuda.Device(device_index)  # type: ignore[attr-defined, no-any-return]
    except Exception as error:
        raise _error(
            "device-selection-failed",
            "array_api.cuda.Device",
            f"could not select CUDA device {device_index}: {error}",
        ) from error


def _release_device_values(api: object, values: Iterable[object]) -> None:
    release = getattr(api, "release", None)
    if release is None:
        return
    first_error: Exception | None = None
    for value in values:
        try:
            release(value)
        except Exception as error:  # pragma: no branch - tests exercise continued cleanup
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise _error("device-release-failed", "device_buffers", str(first_error)) from first_error


def _copy_host_to_device(api: object, destination: object, source: np.ndarray) -> None:
    """Copy one canonical NumPy input without allocating a CuPy staging array."""

    setter = getattr(destination, "set", None)
    if callable(setter):
        setter(source)
        return
    api.copyto(destination, source)  # type: ignore[attr-defined]


class CudaDeviceBufferCache:
    """Own reusable device allocations and issue one non-overlapping lease at a time."""

    def __init__(self, *, array_api_loader: Callable[[], object] | None = None) -> None:
        self._array_api_loader = array_api_loader or _load_cupy_array_api
        self._api: object | None = None
        self._buffers: tuple[CudaDeviceArray, ...] = ()
        self._layout_signature: tuple[tuple[str, str, tuple[int, ...]], ...] | None = None
        self._device_index: int | None = None
        self._active_lease: int | None = None
        self._next_lease = 1
        self._closed = False
        self._allocation_count = 0
        self._reuse_count = 0
        self._replacement_count = 0

    @property
    def allocation_count(self) -> int:
        return self._allocation_count

    @property
    def reuse_count(self) -> int:
        return self._reuse_count

    @property
    def replacement_count(self) -> int:
        return self._replacement_count

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def has_active_lease(self) -> bool:
        return self._active_lease is not None

    def _load_api(self) -> object:
        if self._api is not None:
            return self._api
        try:
            api = self._array_api_loader()
        except Exception as error:
            raise _error(
                "device-api-unavailable",
                "array_api_loader",
                f"ready CUDA evidence could not load the array API: {error}",
            ) from error
        self._api = api
        return api

    def _clear_cached(self, *, suppress_errors: bool) -> None:
        api = self._api
        values = tuple(item.value for item in self._buffers)
        self._buffers = ()
        self._layout_signature = None
        self._device_index = None
        if api is None or not values:
            return
        try:
            _release_device_values(api, values)
        except CudaInputError:
            if not suppress_errors:
                raise

    def transfer(self, host_inputs: CudaHostInputs, diagnostic: object) -> "CudaDeviceInputs":
        from src.optimizer.cuda.runtime import (
            CudaDiagnosticStatus,
            CudaExecutionMode,
            CudaRuntimeDiagnostic,
        )

        if self._closed:
            raise _error("device-cache-closed", "cache", "cannot transfer into a closed device cache.")
        if not isinstance(host_inputs, CudaHostInputs):
            raise _error("invalid-host-inputs", "host_inputs", "must be CudaHostInputs.")
        if not isinstance(diagnostic, CudaRuntimeDiagnostic):
            raise _error("invalid-cuda-diagnostic", "diagnostic", "must be CudaRuntimeDiagnostic.")
        if not (
            diagnostic.status is CudaDiagnosticStatus.READY
            and diagnostic.mode is CudaExecutionMode.CUDA
            and diagnostic.available
            and diagnostic.selected_device_index is not None
        ):
            raise _error(
                "cuda-not-ready",
                "diagnostic.status",
                f"device transfer requires ready P06-T01 evidence; found {diagnostic.status.value}.",
            )
        if self._active_lease is not None:
            raise _error(
                "device-buffers-in-use",
                "cache",
                "release the active device-input lease before reusing or replacing its buffers.",
            )

        api = self._load_api()
        device_index = diagnostic.selected_device_index
        signature = host_inputs.layout_signature
        compatible = self._layout_signature == signature and self._device_index == device_index
        try:
            scope = _device_scope(api, device_index)
            with scope:
                if compatible:
                    try:
                        for host, device in zip(host_inputs.arrays, self._buffers, strict=True):
                            _copy_host_to_device(api, device.value, host.values)
                    except Exception as error:
                        self._clear_cached(suppress_errors=True)
                        raise _error(
                            "device-transfer-failed",
                            "host_inputs",
                            f"compatible buffer copy failed and the cache was discarded: {error}",
                        ) from error
                    self._reuse_count += 1
                else:
                    new_buffers: list[CudaDeviceArray] = []
                    try:
                        for host in host_inputs.arrays:
                            value = api.empty(host.shape, dtype=host.dtype)  # type: ignore[attr-defined]
                            device_shape = tuple(value.shape)
                            device_dtype = np.dtype(value.dtype)
                            if device_shape != host.shape or device_dtype != host.dtype:
                                raise ValueError(
                                    f"{host.name} allocation changed {host.dtype.name} {host.shape!r} "
                                    f"to {device_dtype.name} {device_shape!r}"
                                )
                            new_buffers.append(
                                CudaDeviceArray(host.name, host.dtype, host.shape, value)
                            )
                    except Exception as error:
                        try:
                            _release_device_values(api, (item.value for item in new_buffers))
                        except CudaInputError:
                            pass
                        raise _error(
                            "device-allocation-failed",
                            "host_inputs",
                            f"device input allocation failed: {error}",
                        ) from error
                    try:
                        for host, device in zip(host_inputs.arrays, new_buffers, strict=True):
                            _copy_host_to_device(api, device.value, host.values)
                    except Exception as error:
                        try:
                            _release_device_values(api, (item.value for item in new_buffers))
                        except CudaInputError:
                            pass
                        raise _error(
                            "device-transfer-failed",
                            "host_inputs",
                            f"device input copy failed: {error}",
                        ) from error

                    had_buffers = bool(self._buffers)
                    if had_buffers:
                        try:
                            _release_device_values(api, (item.value for item in self._buffers))
                        except CudaInputError:
                            try:
                                _release_device_values(api, (item.value for item in new_buffers))
                            except CudaInputError:
                                pass
                            self._buffers = ()
                            self._layout_signature = None
                            self._device_index = None
                            raise
                    self._buffers = tuple(new_buffers)
                    self._layout_signature = signature
                    self._device_index = device_index
                    self._allocation_count += len(new_buffers)
                    if had_buffers:
                        self._replacement_count += 1
        except CudaInputError:
            raise
        except Exception as error:
            raise _error("device-selection-failed", "array_api.cuda.Device", str(error)) from error

        lease_id = self._next_lease
        self._next_lease += 1
        self._active_lease = lease_id
        return CudaDeviceInputs(self, lease_id, host_inputs, self._buffers)

    def _release_lease(self, lease_id: int) -> None:
        if self._active_lease != lease_id:
            return
        self._active_lease = None

    def _read_back(self, lease_id: int, host_template: CudaHostInputs) -> CudaHostInputs:
        if self._active_lease != lease_id:
            raise _error("device-lease-released", "device_inputs", "cannot read a released device-input lease.")
        api = self._api
        if api is None or self._device_index is None:
            raise _error("device-cache-empty", "cache", "device buffers are not available.")
        arrays: list[CudaHostArray] = []
        try:
            with _device_scope(api, self._device_index):
                for template, device in zip(host_template.arrays, self._buffers, strict=True):
                    values = np.asarray(api.asnumpy(device.value))  # type: ignore[attr-defined]
                    if tuple(values.shape) != template.shape or values.dtype != template.dtype:
                        raise ValueError(
                            f"{template.name} readback changed dtype or shape to {values.dtype} {values.shape!r}"
                        )
                    arrays.append(CudaHostArray(template.name, values))
        except CudaInputError:
            raise
        except Exception as error:
            raise _error("device-readback-failed", "device_inputs", str(error)) from error
        return CudaHostInputs(tuple(arrays))

    def close(self) -> None:
        if self._closed:
            return
        if self._active_lease is not None:
            raise _error(
                "device-buffers-in-use",
                "cache",
                "release the active device-input lease before closing its cache.",
            )
        try:
            if self._api is not None and self._device_index is not None:
                with _device_scope(self._api, self._device_index):
                    self._clear_cached(suppress_errors=False)
            else:
                self._clear_cached(suppress_errors=False)
        finally:
            self._closed = True

    def __enter__(self) -> "CudaDeviceBufferCache":
        if self._closed:
            raise _error("device-cache-closed", "cache", "cannot enter a closed device cache.")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class CudaDeviceInputs:
    """A live borrowing lease over cache-owned device arrays."""

    __slots__ = (
        "_cache",
        "_lease_id",
        "_host_template",
        "_arrays",
        "_released",
        "_close_cache_on_release",
    )

    def __init__(
        self,
        cache: CudaDeviceBufferCache,
        lease_id: int,
        host_template: CudaHostInputs,
        arrays: tuple[CudaDeviceArray, ...],
        *,
        close_cache_on_release: bool = False,
    ) -> None:
        self._cache = cache
        self._lease_id = lease_id
        self._host_template = host_template
        self._arrays = arrays
        self._released = False
        self._close_cache_on_release = close_cache_on_release

    @property
    def released(self) -> bool:
        return self._released

    @property
    def arrays(self) -> tuple[CudaDeviceArray, ...]:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        return self._arrays

    @property
    def byte_count(self) -> int:
        return self._host_template.byte_count

    @property
    def total_permutations(self) -> int:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        return self._host_template.total_permutations

    @property
    def maximum_replacement_distance(self) -> int:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        return int(self._host_template.array("maximum_replacement_distance")[0])

    @property
    def near_set_tolerance(self) -> float:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        return float(self._host_template.array("near_set_tolerance")[0])

    @property
    def layout_signature(self) -> tuple[tuple[str, str, tuple[int, ...]], ...]:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        return self._host_template.layout_signature

    @property
    def device_index(self) -> int:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        if self._cache._device_index is None:
            raise _error("device-cache-empty", "cache", "device buffers are not available.")
        return self._cache._device_index

    @property
    def array_api(self) -> object:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        if self._cache._api is None:
            raise _error("device-cache-empty", "cache", "device buffers are not available.")
        return self._cache._api

    def array(self, name: str) -> object:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        try:
            index = CUDA_INPUT_FIELD_NAMES.index(name)
        except ValueError:
            raise KeyError(name) from None
        return self._arrays[index].value

    def host_array(self, name: str) -> np.ndarray[Any, Any]:
        """Expose the immutable compilation input without a device readback."""

        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        return self._host_template.array(name)

    def to_host(self) -> CudaHostInputs:
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        return self._cache._read_back(self._lease_id, self._host_template)

    def release(self) -> None:
        if self._released:
            return
        self._cache._release_lease(self._lease_id)
        self._released = True
        if self._close_cache_on_release:
            self._cache.close()

    def __enter__(self) -> "CudaDeviceInputs":
        if self._released:
            raise _error("device-lease-released", "device_inputs", "device-input lease has been released.")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def transfer_cuda_inputs(
    host_inputs: CudaHostInputs,
    diagnostic: object,
    *,
    cache: CudaDeviceBufferCache | None = None,
    array_api_loader: Callable[[], object] | None = None,
) -> CudaDeviceInputs:
    """Transfer through a reusable cache; callers explicitly release the returned lease."""

    if cache is not None and array_api_loader is not None:
        raise _error(
            "ambiguous-device-cache",
            "array_api_loader",
            "supply either an existing cache or an array API loader, not both.",
        )
    selected_cache = cache or CudaDeviceBufferCache(array_api_loader=array_api_loader)
    lease = selected_cache.transfer(host_inputs, diagnostic)
    if cache is None:
        lease._close_cache_on_release = True
    return lease


__all__ = [
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
]
