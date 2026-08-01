"""Immutable filter contracts and chunked views over completed result runs.

The hot path operates only on P07 numeric columns.  It deliberately does not
resolve inventory records or construct one Python object per result row.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from numbers import Integral, Real
from typing import Any, Callable, Iterable

import numpy as np

from src.optimizer.domain import (
    FRIBBELS_SET_ORDER,
    GEAR_SLOT_ORDER,
    RESULT_CATEGORY_ORDER,
    SET_CATALOG,
    GearSlot,
    ResultCategory,
)
from src.optimizer.result_store.schema import (
    RESULT_DERIVED_METRIC_ORDER,
    RESULT_MAX_DENSE_ITEM_ID,
    RESULT_PRIMARY_STAT_ORDER,
    encode_result_category,
)
from src.optimizer.result_store.storage import CompletedResultRun
from src.optimizer.search.set_patterns import CompiledSetPattern


RESULT_FILTER_ID = "e7.optimizer.result-filter"
RESULT_FILTER_VERSION = 1
DEFAULT_FILTER_CHUNK_ROWS = 131_072
MAX_FILTER_CHUNK_ROWS = 1_000_000

_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_FLOAT32_MAX = float(np.finfo(np.float32).max)
_SET_COUNT = len(FRIBBELS_SET_ORDER)
_SLOT_COUNT = len(GEAR_SLOT_ORDER)
_ALL_CATEGORIES = tuple(RESULT_CATEGORY_ORDER)
_SET_PIECES_REQUIRED = tuple(SET_CATALOG[item].pieces_required for item in FRIBBELS_SET_ORDER)
_SET_STACKABLE = tuple(SET_CATALOG[item].stackable for item in FRIBBELS_SET_ORDER)


class ResultFilterError(ValueError):
    """Actionable result-filter contract or execution failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


class ResultFilterCancelled(RuntimeError):
    """Raised at a chunk boundary when a desktop view is superseded."""


def _error(code: str, path: str, message: str) -> ResultFilterError:
    return ResultFilterError(code, path, message)


def _integer(value: object, path: str, minimum: int, maximum: int) -> int:
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


def _binary32(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error("invalid-number", path, "must be a real number.")
    supplied = float(value)
    if not math.isfinite(supplied):
        raise _error("nonfinite-number", path, "must be finite; NaN and infinity are rejected.")
    if abs(supplied) > _FLOAT32_MAX:
        raise _error("binary32-overflow", path, "must be representable as finite binary32.")
    normalized = float(np.float32(supplied))
    return 0.0 if normalized == 0.0 else normalized


@dataclass(frozen=True, slots=True)
class InclusiveInt64Range:
    """An inclusive signed-int64 interval; two blanks mean unrestricted."""

    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        minimum = None if self.minimum is None else _integer(
            self.minimum, "InclusiveInt64Range.minimum", _INT64_MIN, _INT64_MAX
        )
        maximum = None if self.maximum is None else _integer(
            self.maximum, "InclusiveInt64Range.maximum", _INT64_MIN, _INT64_MAX
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise _error("reversed-range", "InclusiveInt64Range", "minimum must not exceed maximum.")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    @property
    def unrestricted(self) -> bool:
        return self.minimum is None and self.maximum is None


@dataclass(frozen=True, slots=True)
class InclusiveFloat32Range:
    """An inclusive interval whose endpoints are explicitly canonical binary32."""

    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        minimum = None if self.minimum is None else _binary32(
            self.minimum, "InclusiveFloat32Range.minimum"
        )
        maximum = None if self.maximum is None else _binary32(
            self.maximum, "InclusiveFloat32Range.maximum"
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise _error("reversed-range", "InclusiveFloat32Range", "minimum must not exceed maximum.")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    @property
    def unrestricted(self) -> bool:
        return self.minimum is None and self.maximum is None


UNRESTRICTED_INT64_RANGE = InclusiveInt64Range()
UNRESTRICTED_FLOAT32_RANGE = InclusiveFloat32Range()
_DEFAULT_PRIMARY_RANGES = (UNRESTRICTED_INT64_RANGE,) * len(RESULT_PRIMARY_STAT_ORDER)
_DEFAULT_DERIVED_RANGES = (UNRESTRICTED_INT64_RANGE,) * len(RESULT_DERIVED_METRIC_ORDER)


@dataclass(frozen=True, slots=True)
class SlotItemFilter:
    """Restrict one canonical slot to a set of dense IDs; empty matches none."""

    slot_index: int
    allowed_dense_item_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        slot = _integer(self.slot_index, "SlotItemFilter.slot_index", 0, _SLOT_COUNT - 1)
        try:
            supplied = tuple(self.allowed_dense_item_ids)
        except TypeError:
            raise _error(
                "invalid-item-selection",
                "SlotItemFilter.allowed_dense_item_ids",
                "must be an iterable of dense item IDs.",
            ) from None
        allowed = tuple(
            sorted(
                {
                    _integer(
                        value,
                        f"SlotItemFilter.allowed_dense_item_ids[{index}]",
                        0,
                        RESULT_MAX_DENSE_ITEM_ID,
                    )
                    for index, value in enumerate(supplied)
                }
            )
        )
        object.__setattr__(self, "slot_index", slot)
        object.__setattr__(self, "allowed_dense_item_ids", allowed)

    @property
    def slot(self) -> GearSlot:
        return GEAR_SLOT_ORDER[self.slot_index]


@dataclass(frozen=True, slots=True)
class SetCountFilter:
    """Inclusive owned-piece and completed-activation ranges for one set."""

    set_index: int
    piece_count: InclusiveInt64Range = UNRESTRICTED_INT64_RANGE
    activation_count: InclusiveInt64Range = UNRESTRICTED_INT64_RANGE

    def __post_init__(self) -> None:
        set_index = _integer(self.set_index, "SetCountFilter.set_index", 0, _SET_COUNT - 1)
        if not isinstance(self.piece_count, InclusiveInt64Range):
            raise _error("invalid-range", "SetCountFilter.piece_count", "must be InclusiveInt64Range.")
        if not isinstance(self.activation_count, InclusiveInt64Range):
            raise _error("invalid-range", "SetCountFilter.activation_count", "must be InclusiveInt64Range.")
        for name, interval in (
            ("piece_count", self.piece_count),
            ("activation_count", self.activation_count),
        ):
            if interval.minimum is not None and interval.minimum < 0:
                raise _error("negative-count", f"SetCountFilter.{name}.minimum", "must be nonnegative.")
            if interval.maximum is not None and interval.maximum > 6:
                raise _error("count-out-of-range", f"SetCountFilter.{name}.maximum", "must not exceed six.")
        object.__setattr__(self, "set_index", set_index)


def _ranges(
    supplied: object,
    expected: int,
    path: str,
) -> tuple[InclusiveInt64Range, ...]:
    try:
        values = tuple(supplied)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-range-axis", path, f"must contain exactly {expected} ranges.") from None
    if len(values) != expected or not all(isinstance(item, InclusiveInt64Range) for item in values):
        raise _error(
            "invalid-range-axis",
            path,
            f"must contain exactly {expected} InclusiveInt64Range entries in canonical order.",
        )
    return values


def _dense_ids(supplied: object, path: str) -> tuple[int, ...]:
    try:
        values = tuple(supplied)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-item-selection", path, "must be an iterable of dense item IDs.") from None
    return tuple(
        sorted(
            {
                _integer(value, f"{path}[{index}]", 0, RESULT_MAX_DENSE_ITEM_ID)
                for index, value in enumerate(values)
            }
        )
    )


def _slot_index(value: object, path: str) -> int:
    if isinstance(value, GearSlot):
        return GEAR_SLOT_ORDER.index(value)
    if isinstance(value, str):
        try:
            return GEAR_SLOT_ORDER.index(GearSlot(value))
        except ValueError:
            raise _error("invalid-slot", path, "must name a canonical gear slot.") from None
    return _integer(value, path, 0, _SLOT_COUNT - 1)


@dataclass(frozen=True, slots=True)
class ResultFilterRequest:
    """Versioned, hash-stable AND-composition of result predicates.

    Empty category selection matches no rows. Empty global included/excluded
    item selections are unrestricted/no-op.
    An explicitly present per-slot item filter with no IDs matches no rows.
    """

    filter_id: str = RESULT_FILTER_ID
    version: int = RESULT_FILTER_VERSION
    categories: tuple[ResultCategory, ...] = _ALL_CATEGORIES
    primary_ranges: tuple[InclusiveInt64Range, ...] = _DEFAULT_PRIMARY_RANGES
    derived_ranges: tuple[InclusiveInt64Range, ...] = _DEFAULT_DERIVED_RANGES
    priority_score: InclusiveFloat32Range = UNRESTRICTED_FLOAT32_RANGE
    constraint_distance: InclusiveFloat32Range = UNRESTRICTED_FLOAT32_RANGE
    equipped_count: InclusiveInt64Range = UNRESTRICTED_INT64_RANGE
    replacement_distance: InclusiveInt64Range = UNRESTRICTED_INT64_RANGE
    included_dense_item_ids: tuple[int, ...] = ()
    excluded_dense_item_ids: tuple[int, ...] = ()
    slot_item_filters: tuple[SlotItemFilter, ...] = ()
    set_count_filters: tuple[SetCountFilter, ...] = ()

    def __post_init__(self) -> None:
        if self.filter_id != RESULT_FILTER_ID:
            raise _error("filter-id-mismatch", "ResultFilterRequest.filter_id", f"must be {RESULT_FILTER_ID!r}.")
        if self.version != RESULT_FILTER_VERSION:
            raise _error(
                "filter-version-mismatch",
                "ResultFilterRequest.version",
                f"must be {RESULT_FILTER_VERSION}.",
            )
        try:
            supplied_categories = tuple(self.categories)
        except TypeError:
            raise _error("invalid-categories", "ResultFilterRequest.categories", "must be iterable.") from None
        categories_by_code: dict[int, ResultCategory] = {}
        for index, value in enumerate(supplied_categories):
            try:
                category = value if isinstance(value, ResultCategory) else ResultCategory(value)
            except (TypeError, ValueError):
                raise _error(
                    "invalid-category",
                    f"ResultFilterRequest.categories[{index}]",
                    "must be exact, one-away, or two-away.",
                ) from None
            categories_by_code[encode_result_category(category)] = category
        categories = tuple(categories_by_code[index] for index in sorted(categories_by_code))
        primary = _ranges(self.primary_ranges, len(RESULT_PRIMARY_STAT_ORDER), "ResultFilterRequest.primary_ranges")
        derived = _ranges(self.derived_ranges, len(RESULT_DERIVED_METRIC_ORDER), "ResultFilterRequest.derived_ranges")
        if not isinstance(self.priority_score, InclusiveFloat32Range):
            raise _error("invalid-range", "ResultFilterRequest.priority_score", "must be InclusiveFloat32Range.")
        if not isinstance(self.constraint_distance, InclusiveFloat32Range):
            raise _error("invalid-range", "ResultFilterRequest.constraint_distance", "must be InclusiveFloat32Range.")
        if not isinstance(self.equipped_count, InclusiveInt64Range):
            raise _error("invalid-range", "ResultFilterRequest.equipped_count", "must be InclusiveInt64Range.")
        if not isinstance(self.replacement_distance, InclusiveInt64Range):
            raise _error("invalid-range", "ResultFilterRequest.replacement_distance", "must be InclusiveInt64Range.")
        for name, interval, maximum in (
            ("equipped_count", self.equipped_count, 6),
            ("replacement_distance", self.replacement_distance, 2),
        ):
            if interval.minimum is not None and interval.minimum < 0:
                raise _error("negative-count", f"ResultFilterRequest.{name}.minimum", "must be nonnegative.")
            if interval.maximum is not None and interval.maximum > maximum:
                raise _error(
                    "count-out-of-range",
                    f"ResultFilterRequest.{name}.maximum",
                    f"must not exceed {maximum}.",
                )
        included = _dense_ids(self.included_dense_item_ids, "ResultFilterRequest.included_dense_item_ids")
        excluded = _dense_ids(self.excluded_dense_item_ids, "ResultFilterRequest.excluded_dense_item_ids")

        slot_by_index: dict[int, SlotItemFilter] = {}
        try:
            supplied_slot_filters = tuple(self.slot_item_filters)
        except TypeError:
            raise _error("invalid-slot-filters", "ResultFilterRequest.slot_item_filters", "must be iterable.") from None
        for index, item in enumerate(supplied_slot_filters):
            if not isinstance(item, SlotItemFilter):
                raise _error("invalid-slot-filter", f"ResultFilterRequest.slot_item_filters[{index}]", "must be SlotItemFilter.")
            previous = slot_by_index.get(item.slot_index)
            if previous is not None and previous != item:
                raise _error("conflicting-slot-filter", f"ResultFilterRequest.slot_item_filters[{index}]", "duplicate slot filters must be equal.")
            slot_by_index[item.slot_index] = item

        set_by_index: dict[int, SetCountFilter] = {}
        try:
            supplied_set_filters = tuple(self.set_count_filters)
        except TypeError:
            raise _error("invalid-set-filters", "ResultFilterRequest.set_count_filters", "must be iterable.") from None
        for index, item in enumerate(supplied_set_filters):
            if not isinstance(item, SetCountFilter):
                raise _error("invalid-set-filter", f"ResultFilterRequest.set_count_filters[{index}]", "must be SetCountFilter.")
            previous = set_by_index.get(item.set_index)
            if previous is not None and previous != item:
                raise _error("conflicting-set-filter", f"ResultFilterRequest.set_count_filters[{index}]", "duplicate set filters must be equal.")
            if not (item.piece_count.unrestricted and item.activation_count.unrestricted):
                set_by_index[item.set_index] = item

        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "primary_ranges", primary)
        object.__setattr__(self, "derived_ranges", derived)
        object.__setattr__(self, "included_dense_item_ids", included)
        object.__setattr__(self, "excluded_dense_item_ids", excluded)
        object.__setattr__(self, "slot_item_filters", tuple(slot_by_index[index] for index in sorted(slot_by_index)))
        object.__setattr__(self, "set_count_filters", tuple(set_by_index[index] for index in sorted(set_by_index)))


@dataclass(frozen=True, slots=True)
class OriginalResultScope:
    """Active-session authority for predicates represented by a completed run."""

    filter_id: str
    filter_version: int
    baseline: ResultFilterRequest
    target_pattern: CompiledSetPattern

    def __post_init__(self) -> None:
        if self.filter_id != RESULT_FILTER_ID or self.filter_version != RESULT_FILTER_VERSION:
            raise _error("scope-version-mismatch", "OriginalResultScope", "must use the current result-filter ID/version.")
        if not isinstance(self.baseline, ResultFilterRequest):
            raise _error("invalid-baseline", "OriginalResultScope.baseline", "must be ResultFilterRequest.")
        if not isinstance(self.target_pattern, CompiledSetPattern):
            raise _error("invalid-target-pattern", "OriginalResultScope.target_pattern", "must be CompiledSetPattern.")

    @classmethod
    def create(cls, baseline: ResultFilterRequest, target_pattern: CompiledSetPattern) -> OriginalResultScope:
        return cls(RESULT_FILTER_ID, RESULT_FILTER_VERSION, baseline, target_pattern)


class FilterScopeDecision(StrEnum):
    EQUAL = "equal"
    TIGHTENING = "tightening"
    RERUN_REQUIRED = "rerun-required"


@dataclass(frozen=True, slots=True)
class FilterScopeAssessment:
    decision: FilterScopeDecision
    reasons: tuple[str, ...] = ()

    @property
    def rerun_required(self) -> bool:
        return self.decision is FilterScopeDecision.RERUN_REQUIRED


def _range_subset(requested: Any, baseline: Any) -> bool:
    minimum_ok = baseline.minimum is None or (
        requested.minimum is not None and requested.minimum >= baseline.minimum
    )
    maximum_ok = baseline.maximum is None or (
        requested.maximum is not None and requested.maximum <= baseline.maximum
    )
    return minimum_ok and maximum_ok


def _any_selection_subset(requested: tuple[int, ...], baseline: tuple[int, ...]) -> bool:
    if not baseline:
        return True
    return bool(requested) and set(requested).issubset(baseline)


def assess_filter_scope(
    original: OriginalResultScope,
    requested: ResultFilterRequest,
) -> FilterScopeAssessment:
    """Classify against the base run, never against a previously filtered view."""

    if not isinstance(original, OriginalResultScope):
        raise _error("invalid-original-scope", "original", "must be OriginalResultScope.")
    if not isinstance(requested, ResultFilterRequest):
        raise _error("invalid-filter-request", "requested", "must be ResultFilterRequest.")
    baseline = original.baseline
    reasons: list[str] = []

    if not set(requested.categories).issubset(baseline.categories):
        reasons.append("categories enable rows excluded by the original run")
    for index, (item, base) in enumerate(zip(requested.primary_ranges, baseline.primary_ranges, strict=True)):
        if not _range_subset(item, base):
            reasons.append(f"primary range {RESULT_PRIMARY_STAT_ORDER[index].value} is broader")
    for index, (item, base) in enumerate(zip(requested.derived_ranges, baseline.derived_ranges, strict=True)):
        if not _range_subset(item, base):
            reasons.append(f"derived range {RESULT_DERIVED_METRIC_ORDER[index]} is broader")
    for name in ("priority_score", "constraint_distance", "equipped_count", "replacement_distance"):
        if not _range_subset(getattr(requested, name), getattr(baseline, name)):
            reasons.append(f"{name} range is broader")
    if not _any_selection_subset(requested.included_dense_item_ids, baseline.included_dense_item_ids):
        reasons.append("included item selection is broader")
    if not set(requested.excluded_dense_item_ids).issuperset(baseline.excluded_dense_item_ids):
        reasons.append("excluded item selection is broader")

    requested_slots = {item.slot_index: item.allowed_dense_item_ids for item in requested.slot_item_filters}
    baseline_slots = {item.slot_index: item.allowed_dense_item_ids for item in baseline.slot_item_filters}
    for slot_index, base_allowed in baseline_slots.items():
        allowed = requested_slots.get(slot_index)
        if allowed is None or not set(allowed).issubset(base_allowed):
            reasons.append(f"slot item filter {GEAR_SLOT_ORDER[slot_index].value} is broader")

    requested_sets = {item.set_index: item for item in requested.set_count_filters}
    baseline_sets = {item.set_index: item for item in baseline.set_count_filters}
    for set_index, base_filter in baseline_sets.items():
        item = requested_sets.get(set_index)
        if item is None or not _range_subset(item.piece_count, base_filter.piece_count) or not _range_subset(
            item.activation_count, base_filter.activation_count
        ):
            reasons.append(f"set count filter {FRIBBELS_SET_ORDER[set_index].value} is broader")
    if reasons:
        return FilterScopeAssessment(FilterScopeDecision.RERUN_REQUIRED, tuple(reasons))
    decision = FilterScopeDecision.EQUAL if requested == baseline else FilterScopeDecision.TIGHTENING
    return FilterScopeAssessment(decision)


@dataclass(frozen=True, slots=True)
class ResultFilterExecutionStats:
    scanned_rows: int
    matched_rows: int
    chunk_rows: int
    peak_chunk_rows: int
    ordinal_capacity_bytes: int
    temporary_byte_upper_bound: int


@dataclass(frozen=True, slots=True)
class FilteredResultView:
    """Stable physical row ordinals in ascending base-run order."""

    row_ordinals: np.ndarray[Any, np.dtype[np.uint32]]
    stats: ResultFilterExecutionStats

    def __post_init__(self) -> None:
        values = self.row_ordinals
        if not isinstance(values, np.ndarray) or values.dtype.str != np.dtype("<u4").str or values.ndim != 1:
            raise _error("invalid-view-index", "FilteredResultView.row_ordinals", "must be a one-dimensional uint32 array.")
        if len(values) > 1 and np.any(values[1:] <= values[:-1]):
            raise _error("unstable-view-index", "FilteredResultView.row_ordinals", "must be strictly ascending.")
        values.flags.writeable = False


@dataclass(frozen=True, slots=True)
class ResultFilterOutcome:
    assessment: FilterScopeAssessment
    view: FilteredResultView | None

    @property
    def rerun_required(self) -> bool:
        return self.assessment.rerun_required


def _apply_range(mask: np.ndarray[Any, Any], values: np.ndarray[Any, Any], interval: Any) -> None:
    if interval.minimum is not None:
        mask &= values >= interval.minimum
    if interval.maximum is not None:
        mask &= values <= interval.maximum


def _open_required_columns(run: CompletedResultRun, request: ResultFilterRequest) -> dict[str, np.ndarray[Any, Any]]:
    names: set[str] = set()
    if request.categories != _ALL_CATEGORIES:
        names.add("category_codes")
    if not request.replacement_distance.unrestricted:
        names.add("replacement_distances")
    if any(not item.unrestricted for item in request.primary_ranges):
        names.add("effective_final_stats")
    if not request.primary_ranges[4].unrestricted:
        names.add("raw_critical_hit_chances")
    if any(not item.unrestricted for item in request.derived_ranges):
        names.add("derived_metrics")
    if not request.priority_score.unrestricted:
        names.add("priority_scores")
    if not request.constraint_distance.unrestricted:
        names.add("constraint_distances")
    if not request.equipped_count.unrestricted:
        names.add("equipped_item_counts")
    if request.included_dense_item_ids or request.excluded_dense_item_ids or request.slot_item_filters:
        names.add("dense_item_ids")
    if request.set_count_filters:
        names.add("owned_set_indices")
    return {name: run.open_column(name) for name in sorted(names)}


def _close_columns(columns: Iterable[np.ndarray[Any, Any]]) -> None:
    for column in columns:
        mapping = getattr(column, "_mmap", None)
        if mapping is not None:
            mapping.close()


def _set_counts(sets: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    counts = np.empty((sets.shape[0], _SET_COUNT), dtype="u1")
    for set_index in range(_SET_COUNT):
        counts[:, set_index] = np.count_nonzero(sets == set_index, axis=1)
    return counts


def _filter_chunk(
    columns: dict[str, np.ndarray[Any, Any]],
    request: ResultFilterRequest,
    required_piece_counts: np.ndarray[Any, Any],
    start: int,
    stop: int,
) -> np.ndarray[Any, Any]:
    size = stop - start
    mask = np.ones(size, dtype=np.bool_)
    if request.categories != _ALL_CATEGORIES:
        codes = np.asarray(tuple(encode_result_category(item) for item in request.categories), dtype="u1")
        mask &= np.isin(columns["category_codes"][start:stop], codes)
    if not request.replacement_distance.unrestricted:
        _apply_range(mask, columns["replacement_distances"][start:stop], request.replacement_distance)
    if "effective_final_stats" in columns:
        values = columns["effective_final_stats"][start:stop]
        for axis, interval in enumerate(request.primary_ranges):
            if not interval.unrestricted:
                source = (
                    columns["raw_critical_hit_chances"][start:stop]
                    if axis == 4
                    else values[:, axis]
                )
                _apply_range(mask, source, interval)
    if "derived_metrics" in columns:
        values = columns["derived_metrics"][start:stop]
        for axis, interval in enumerate(request.derived_ranges):
            if not interval.unrestricted:
                _apply_range(mask, values[:, axis], interval)
    if "priority_scores" in columns:
        _apply_range(mask, columns["priority_scores"][start:stop], request.priority_score)
    if "constraint_distances" in columns:
        _apply_range(mask, columns["constraint_distances"][start:stop], request.constraint_distance)
    if "equipped_item_counts" in columns:
        _apply_range(mask, columns["equipped_item_counts"][start:stop], request.equipped_count)
    if "dense_item_ids" in columns:
        dense = columns["dense_item_ids"][start:stop]
        if request.included_dense_item_ids:
            mask &= np.isin(dense, np.asarray(request.included_dense_item_ids, dtype="<i4")).any(axis=1)
        if request.excluded_dense_item_ids:
            mask &= ~np.isin(dense, np.asarray(request.excluded_dense_item_ids, dtype="<i4")).any(axis=1)
        for item in request.slot_item_filters:
            if not item.allowed_dense_item_ids:
                mask.fill(False)
            else:
                mask &= np.isin(
                    dense[:, item.slot_index],
                    np.asarray(item.allowed_dense_item_ids, dtype="<i4"),
                )
    if "owned_set_indices" in columns:
        sets = columns["owned_set_indices"][start:stop]
        counts = _set_counts(sets)
        for item in request.set_count_filters:
            pieces = counts[:, item.set_index]
            _apply_range(mask, pieces, item.piece_count)
            if not item.activation_count.unrestricted:
                activations = pieces // _SET_PIECES_REQUIRED[item.set_index]
                if not _SET_STACKABLE[item.set_index]:
                    activations = np.minimum(activations, 1)
                _apply_range(mask, activations, item.activation_count)
    return np.flatnonzero(mask)


def filter_completed_result_run(
    run: CompletedResultRun,
    requested: ResultFilterRequest,
    original: OriginalResultScope,
    *,
    chunk_rows: object = DEFAULT_FILTER_CHUNK_ROWS,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ResultFilterOutcome:
    """Return a compact stable view, or an explicit rerun-required outcome."""

    if not isinstance(run, CompletedResultRun):
        raise _error("invalid-completed-run", "run", "must be CompletedResultRun.")
    if not isinstance(requested, ResultFilterRequest):
        raise _error("invalid-filter-request", "requested", "must be ResultFilterRequest.")
    assessment = assess_filter_scope(original, requested)
    if assessment.rerun_required:
        return ResultFilterOutcome(assessment, None)
    chunk = _integer(chunk_rows, "chunk_rows", 1, MAX_FILTER_CHUNK_ROWS)
    required = np.asarray(original.target_pattern.required_piece_counts, dtype="u1")
    ordinals = np.empty(run.row_count, dtype="<u4")
    matched = 0
    peak = 0
    columns = _open_required_columns(run, requested)
    try:
        for start in range(0, run.row_count, chunk):
            if should_cancel is not None and should_cancel():
                raise ResultFilterCancelled()
            stop = min(start + chunk, run.row_count)
            peak = max(peak, stop - start)
            local = _filter_chunk(columns, requested, required, start, stop)
            count = int(local.size)
            if count:
                ordinals[matched : matched + count] = local.astype("<u4", copy=False) + start
                matched += count
            if on_progress is not None:
                on_progress(stop, run.row_count)
    finally:
        _close_columns(columns.values())
    ordinals.resize(matched, refcheck=False)
    ordinals.flags.writeable = False
    stats = ResultFilterExecutionStats(
        scanned_rows=run.row_count,
        matched_rows=matched,
        chunk_rows=chunk,
        peak_chunk_rows=peak,
        ordinal_capacity_bytes=run.row_count * np.dtype("<u4").itemsize,
        # Conservative declared ceiling for vector workspaces, membership
        # operations, flatnonzero, and set-count derivation. The immutable
        # request itself and the ordinal capacity are reported separately.
        temporary_byte_upper_bound=peak * 128,
    )
    return ResultFilterOutcome(assessment, FilteredResultView(ordinals, stats))


__all__ = [
    "DEFAULT_FILTER_CHUNK_ROWS",
    "MAX_FILTER_CHUNK_ROWS",
    "RESULT_FILTER_ID",
    "RESULT_FILTER_VERSION",
    "UNRESTRICTED_FLOAT32_RANGE",
    "UNRESTRICTED_INT64_RANGE",
    "FilterScopeAssessment",
    "FilterScopeDecision",
    "FilteredResultView",
    "InclusiveFloat32Range",
    "InclusiveInt64Range",
    "OriginalResultScope",
    "ResultFilterError",
    "ResultFilterCancelled",
    "ResultFilterExecutionStats",
    "ResultFilterOutcome",
    "ResultFilterRequest",
    "SetCountFilter",
    "SlotItemFilter",
    "assess_filter_scope",
    "filter_completed_result_run",
]
