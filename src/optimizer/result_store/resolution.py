"""Bounded page-row resolution and cold selected-build explanations."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from numbers import Integral, Real
from typing import Any

import numpy as np

from src.optimizer.domain import (
    FRIBBELS_SET_ORDER,
    GEAR_SLOT_ORDER,
    RESULT_CATEGORY_ORDER,
    SET_CATALOG,
    GearItem,
    GearSlot,
    ResultCategory,
)
from src.optimizer.result_store.indexing import (
    MAX_PAGE_SIZE,
    CompletedResultSortIndex,
    ResultPage,
    page_result_sort_index,
    result_run_fingerprint,
)
from src.optimizer.result_store.schema import (
    RESULT_ROW_BYTES,
    RESULT_SCHEMA,
    ReplacementMetadataReference,
    ResultSchemaError,
    decode_result_category,
    replacement_metadata_reference,
    set_activation_counts,
    set_piece_counts,
    validate_result_columns,
    validate_row_ordinal,
)
from src.optimizer.result_store.storage import CompletedResultRun
from src.optimizer.search.exact_evaluation import (
    ExactBuildEvaluationContext,
    validate_exact_build_search_context,
)
from src.optimizer.search.set_patterns import CompiledSetPattern
from src.optimizer.search.slot_arrays import SearchReadySlotArrays


RESULT_RESOLUTION_ID = "e7.optimizer.result-resolution"
RESULT_RESOLUTION_VERSION = 1

_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")


class ResultResolutionError(ValueError):
    """Actionable active-session, page, snapshot, or detail failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> ResultResolutionError:
    return ResultResolutionError(code, path, message)


def _wrap(error: ValueError, path: str = "resolution") -> ResultResolutionError:
    return _error(
        getattr(error, "code", "resolution-composition-failed"),
        getattr(error, "path", path),
        getattr(error, "message", str(error)),
    )


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _error("invalid-stable-id", path, "must be a non-empty canonical stable ID.")
    return value


def _integer(value: object, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _error("invalid-integer", path, "must be an integer; booleans are not accepted.")
    normalized = int(value)
    if normalized < minimum or normalized > maximum:
        raise _error(
            "integer-out-of-range",
            path,
            f"must be between {minimum} and {maximum}; found {normalized}.",
        )
    return normalized


def _binary32(value: object, path: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error("invalid-binary32", path, "must be a finite binary32 value.")
    numeric = float(value)
    if not math.isfinite(numeric) or (nonnegative and numeric < 0):
        raise _error("invalid-binary32", path, "must be finite and inside the declared range.")
    canonical = float(np.float32(numeric))
    if numeric != canonical:
        raise _error("noncanonical-binary32", path, "must already equal its binary32 representation.")
    return 0.0 if canonical == 0 else canonical


def _version(identity: object, version: object, path: str) -> None:
    if identity != RESULT_RESOLUTION_ID:
        raise _error(
            "resolution-id-mismatch",
            f"{path}.resolution_id",
            f"must be {RESULT_RESOLUTION_ID!r}.",
        )
    if version != RESULT_RESOLUTION_VERSION:
        raise _error(
            "resolution-version-mismatch",
            f"{path}.version",
            f"must be {RESULT_RESOLUTION_VERSION}.",
        )


@dataclass(frozen=True, slots=True)
class ResultResolverContext:
    """One active run's exact in-memory inventory, search, and P05 authority."""

    session_id: str
    run_id: str
    selected_hero_id: str
    inventory_snapshot: object
    slot_arrays: SearchReadySlotArrays
    evaluation_context: ExactBuildEvaluationContext
    target_pattern: CompiledSetPattern
    resolution_id: str = RESULT_RESOLUTION_ID
    version: int = RESULT_RESOLUTION_VERSION
    search_gear_by_dense_id: tuple[GearItem, ...] = field(init=False, repr=False)
    search_slot_index_by_dense_id: tuple[int, ...] = field(init=False, repr=False)
    search_set_index_by_dense_id: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _version(self.resolution_id, self.version, "ResultResolverContext")
        for name in ("session_id", "run_id", "selected_hero_id"):
            object.__setattr__(
                self,
                name,
                _text(getattr(self, name), f"ResultResolverContext.{name}"),
            )
        if not isinstance(self.slot_arrays, SearchReadySlotArrays):
            raise _error(
                "invalid-search-arrays",
                "ResultResolverContext.slot_arrays",
                "must be SearchReadySlotArrays.",
            )
        if not isinstance(self.evaluation_context, ExactBuildEvaluationContext):
            raise _error(
                "invalid-evaluation-context",
                "ResultResolverContext.evaluation_context",
                "must be ExactBuildEvaluationContext.",
            )
        if not isinstance(self.target_pattern, CompiledSetPattern):
            raise _error(
                "invalid-target-pattern",
                "ResultResolverContext.target_pattern",
                "must be CompiledSetPattern.",
            )
        exact = self.evaluation_context
        if self.selected_hero_id != self.slot_arrays.hero_id or self.selected_hero_id != exact.hero_id:
            raise _error(
                "selected-hero-mismatch",
                "ResultResolverContext.selected_hero_id",
                "must match both the prepared search arrays and numeric evaluation context.",
            )
        if self.target_pattern.required_piece_counts != exact.required_piece_counts:
            raise _error(
                "target-context-mismatch",
                "ResultResolverContext.target_pattern",
                "must match the numeric evaluation context's requested set pieces.",
            )
        try:
            validate_exact_build_search_context(exact, self.slot_arrays)
        except ValueError as error:
            raise _wrap(error) from error

        snapshot = self.inventory_snapshot
        try:
            groups = tuple(snapshot.items_by_slot)  # type: ignore[attr-defined]
            reverse = tuple(snapshot.dense_id_to_stable_id)  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            raise _error(
                "invalid-inventory-snapshot",
                "ResultResolverContext.inventory_snapshot",
                "must expose immutable canonical items_by_slot and dense_id_to_stable_id sequences.",
            ) from None
        try:
            group_slots = tuple(item[0] for item in groups)
        except (TypeError, IndexError):
            raise _error(
                "invalid-snapshot-slot",
                "inventory_snapshot.items_by_slot",
                "must contain canonical slot/gear-sequence pairs.",
            ) from None
        if group_slots != GEAR_SLOT_ORDER:
            raise _error(
                "snapshot-slot-order",
                "inventory_snapshot.items_by_slot",
                "must use canonical six-slot order.",
            )
        flattened: list[GearItem] = []
        for slot_index, pair in enumerate(groups):
            try:
                slot, supplied = pair
                items = tuple(supplied)
            except (TypeError, ValueError):
                raise _error(
                    "invalid-snapshot-slot",
                    f"inventory_snapshot.items_by_slot[{slot_index}]",
                    "must contain one canonical slot and an immutable gear sequence.",
                ) from None
            if slot is not GEAR_SLOT_ORDER[slot_index] or not all(
                isinstance(item, GearItem) and item.slot is slot for item in items
            ):
                raise _error(
                    "snapshot-gear-slot-mismatch",
                    f"inventory_snapshot.items_by_slot[{slot_index}]",
                    "must contain only full GearItem records for its canonical slot.",
                )
            flattened.extend(items)
        expected_snapshot_ids = tuple(range(len(flattened)))
        try:
            reverse_dense = tuple(item[0] for item in reverse)
            reverse_stable = tuple(item[1] for item in reverse)
        except (TypeError, IndexError):
            raise _error(
                "invalid-snapshot-reverse-map",
                "inventory_snapshot.dense_id_to_stable_id",
                "must contain dense-ID/stable-ID pairs.",
            ) from None
        if reverse_dense != expected_snapshot_ids or tuple(item.dense_id for item in flattened) != expected_snapshot_ids:
            raise _error(
                "snapshot-dense-id-mismatch",
                "inventory_snapshot.dense_id_to_stable_id",
                "must be contiguous and agree with the full gear records.",
            )
        if tuple(item.item_id for item in flattened) != reverse_stable:
            raise _error(
                "snapshot-stable-id-mismatch",
                "inventory_snapshot.dense_id_to_stable_id",
                "must agree with the full gear records.",
            )
        if len(reverse_stable) != len(set(reverse_stable)):
            raise _error(
                "duplicate-snapshot-stable-id",
                "inventory_snapshot.dense_id_to_stable_id",
                "must map every owned item identity exactly once.",
            )
        inventory_by_stable = dict(zip(reverse_stable, flattened, strict=True))

        total = self.slot_arrays.total_items
        gear_by_dense: list[GearItem | None] = [None] * total
        slot_by_dense = [-1] * total
        set_by_dense = [-1] * total
        for slot_index, prepared in enumerate(self.slot_arrays.slots):
            for offset, (dense_id, set_index) in enumerate(
                zip(prepared.dense_ids, prepared.set_indices, strict=True)
            ):
                stable_id = self.slot_arrays.stable_item_id_for_dense_id(dense_id)
                gear = inventory_by_stable.get(stable_id)
                if gear is None:
                    raise _error(
                        "search-item-missing-from-snapshot",
                        f"slot_arrays.dense_id_to_stable_id[{dense_id}]",
                        f"owned item {stable_id!r} is absent from the active inventory snapshot.",
                    )
                if gear.slot is not prepared.slot:
                    raise _error(
                        "search-item-slot-mismatch",
                        f"slot_arrays.slots[{slot_index}].dense_ids[{offset}]",
                        "resolves to full gear in a different canonical slot.",
                    )
                actual_set_index = SET_CATALOG[gear.gear_set].fribbels_index
                if actual_set_index != set_index:
                    raise _error(
                        "search-item-set-mismatch",
                        f"slot_arrays.slots[{slot_index}].set_indices[{offset}]",
                        "does not match the active full gear record's set.",
                    )
                gear_by_dense[dense_id] = gear
                slot_by_dense[dense_id] = slot_index
                set_by_dense[dense_id] = set_index
        if any(item is None for item in gear_by_dense):
            raise _error(
                "incomplete-search-snapshot",
                "ResultResolverContext.slot_arrays",
                "every prepared dense ID must resolve to one full owned gear record.",
            )
        object.__setattr__(self, "search_gear_by_dense_id", tuple(gear_by_dense))
        object.__setattr__(self, "search_slot_index_by_dense_id", tuple(slot_by_dense))
        object.__setattr__(self, "search_set_index_by_dense_id", tuple(set_by_dense))


@dataclass(frozen=True, slots=True)
class ResultPageRowsRequest:
    session_id: str
    run_id: str
    index_cache_key: str
    page: ResultPage
    resolution_id: str = RESULT_RESOLUTION_ID
    version: int = RESULT_RESOLUTION_VERSION

    def __post_init__(self) -> None:
        _version(self.resolution_id, self.version, "ResultPageRowsRequest")
        for name in ("session_id", "run_id"):
            object.__setattr__(self, name, _text(getattr(self, name), f"ResultPageRowsRequest.{name}"))
        if not isinstance(self.index_cache_key, str) or not _CACHE_KEY.fullmatch(self.index_cache_key):
            raise _error(
                "invalid-index-cache-key",
                "ResultPageRowsRequest.index_cache_key",
                "must be the lowercase SHA-256 key of the checked sort index.",
            )
        if not isinstance(self.page, ResultPage):
            raise _error("invalid-result-page", "ResultPageRowsRequest.page", "must be a checked ResultPage.")


@dataclass(frozen=True, slots=True)
class ResultBuildDetailRequest:
    session_id: str
    run_id: str
    index_cache_key: str
    page: ResultPage
    row_ordinal: int
    resolution_id: str = RESULT_RESOLUTION_ID
    version: int = RESULT_RESOLUTION_VERSION

    def __post_init__(self) -> None:
        _version(self.resolution_id, self.version, "ResultBuildDetailRequest")
        for name in ("session_id", "run_id"):
            object.__setattr__(self, name, _text(getattr(self, name), f"ResultBuildDetailRequest.{name}"))
        if not isinstance(self.index_cache_key, str) or not _CACHE_KEY.fullmatch(self.index_cache_key):
            raise _error(
                "invalid-index-cache-key",
                "ResultBuildDetailRequest.index_cache_key",
                "must be the lowercase SHA-256 key of the checked sort index.",
            )
        if not isinstance(self.page, ResultPage):
            raise _error("invalid-result-page", "ResultBuildDetailRequest.page", "must be a checked ResultPage.")
        try:
            ordinal = validate_row_ordinal(self.row_ordinal)
        except ResultSchemaError as error:
            raise _wrap(error) from error
        object.__setattr__(self, "row_ordinal", ordinal)


@dataclass(frozen=True, slots=True)
class ResultResolutionProjection:
    row_count: int
    stored_column_count: int
    stored_column_copy_bytes: int
    owned_item_reference_count: int

    def __post_init__(self) -> None:
        count = _integer(self.row_count, "ResultResolutionProjection.row_count", 0, MAX_PAGE_SIZE)
        expected_columns = 0 if count == 0 else len(RESULT_SCHEMA.columns)
        expected_bytes = count * RESULT_ROW_BYTES
        expected_references = count * len(GEAR_SLOT_ORDER)
        if (
            self.stored_column_count != expected_columns
            or self.stored_column_copy_bytes != expected_bytes
            or self.owned_item_reference_count != expected_references
        ):
            raise _error(
                "resolution-projection-mismatch",
                "ResultResolutionProjection",
                "must equal the exact v1 bounded-page projection.",
            )
        object.__setattr__(self, "row_count", count)


def project_result_resolution(row_count: object) -> ResultResolutionProjection:
    count = _integer(row_count, "row_count", 0, MAX_PAGE_SIZE)
    return ResultResolutionProjection(
        row_count=count,
        stored_column_count=0 if count == 0 else len(RESULT_SCHEMA.columns),
        stored_column_copy_bytes=count * RESULT_ROW_BYTES,
        owned_item_reference_count=count * len(GEAR_SLOT_ORDER),
    )


@dataclass(frozen=True, slots=True)
class ResultResolutionStats:
    projection: ResultResolutionProjection
    unique_full_gear_records: int

    def __post_init__(self) -> None:
        if not isinstance(self.projection, ResultResolutionProjection):
            raise _error("invalid-projection", "ResultResolutionStats.projection", "must be ResultResolutionProjection.")
        object.__setattr__(
            self,
            "unique_full_gear_records",
            _integer(
                self.unique_full_gear_records,
                "ResultResolutionStats.unique_full_gear_records",
                0,
                MAX_PAGE_SIZE * 6,
            ),
        )


@dataclass(frozen=True, slots=True)
class ResolvedPageWindow:
    page_index: int
    page_size: int
    total_rows: int
    page_count: int
    start_offset: int
    end_offset: int
    has_previous: bool
    has_next: bool
    out_of_range: bool
    row_ordinals: tuple[int, ...]

    def __post_init__(self) -> None:
        page_index = _integer(self.page_index, "ResolvedPageWindow.page_index", 0, 5_000_000)
        page_size = _integer(self.page_size, "ResolvedPageWindow.page_size", 1, MAX_PAGE_SIZE)
        total = _integer(self.total_rows, "ResolvedPageWindow.total_rows", 0, 5_000_000)
        page_count = _integer(self.page_count, "ResolvedPageWindow.page_count", 0, 5_000_000)
        start = _integer(self.start_offset, "ResolvedPageWindow.start_offset", 0, total)
        end = _integer(self.end_offset, "ResolvedPageWindow.end_offset", start, total)
        ordinals = tuple(
            _integer(item, f"ResolvedPageWindow.row_ordinals[{index}]", 0, 4_999_999)
            for index, item in enumerate(self.row_ordinals)
        )
        if len(ordinals) > page_size or len(ordinals) != end - start:
            raise _error(
                "page-window-size-mismatch",
                "ResolvedPageWindow.row_ordinals",
                "must exactly cover the bounded source-page offsets.",
            )
        if len(ordinals) != len(set(ordinals)):
            raise _error("duplicate-page-ordinal", "ResolvedPageWindow.row_ordinals", "must be unique.")
        if page_count != (math.ceil(total / page_size) if total else 0):
            raise _error("page-count-mismatch", "ResolvedPageWindow.page_count", "must match total rows and page size.")
        if not all(isinstance(value, bool) for value in (self.has_previous, self.has_next, self.out_of_range)):
            raise _error("invalid-page-flag", "ResolvedPageWindow", "navigation flags must be boolean.")
        object.__setattr__(self, "page_index", page_index)
        object.__setattr__(self, "page_size", page_size)
        object.__setattr__(self, "total_rows", total)
        object.__setattr__(self, "page_count", page_count)
        object.__setattr__(self, "start_offset", start)
        object.__setattr__(self, "end_offset", end)
        object.__setattr__(self, "row_ordinals", ordinals)

    @classmethod
    def from_page(cls, page: ResultPage) -> ResolvedPageWindow:
        return cls(
            page.request.page_index,
            page.request.page_size,
            page.total_rows,
            page.page_count,
            page.start_offset,
            page.end_offset,
            page.has_previous,
            page.has_next,
            page.out_of_range,
            tuple(int(item) for item in page.row_ordinals),
        )


@dataclass(frozen=True, slots=True)
class ResolvedOwnedItem:
    search_dense_id: int
    stable_item_id: str
    slot: GearSlot
    set_index: int
    gear: GearItem

    def __post_init__(self) -> None:
        dense_id = _integer(self.search_dense_id, "ResolvedOwnedItem.search_dense_id", 0, (1 << 31) - 1)
        stable_id = _text(self.stable_item_id, "ResolvedOwnedItem.stable_item_id")
        try:
            slot = self.slot if isinstance(self.slot, GearSlot) else GearSlot(self.slot)
        except (TypeError, ValueError):
            raise _error("invalid-gear-slot", "ResolvedOwnedItem.slot", "must be a canonical gear slot.") from None
        set_index = _integer(self.set_index, "ResolvedOwnedItem.set_index", 0, len(FRIBBELS_SET_ORDER) - 1)
        if not isinstance(self.gear, GearItem):
            raise _error("invalid-full-gear", "ResolvedOwnedItem.gear", "must be a full GearItem record.")
        if self.gear.item_id != stable_id or self.gear.slot is not slot:
            raise _error(
                "full-gear-identity-mismatch",
                "ResolvedOwnedItem.gear",
                "must match the resolved stable item identity and canonical slot.",
            )
        if SET_CATALOG[self.gear.gear_set].fribbels_index != set_index:
            raise _error("full-gear-set-mismatch", "ResolvedOwnedItem.set_index", "must match the full gear set.")
        object.__setattr__(self, "search_dense_id", dense_id)
        object.__setattr__(self, "stable_item_id", stable_id)
        object.__setattr__(self, "slot", slot)
        object.__setattr__(self, "set_index", set_index)


@dataclass(frozen=True, slots=True)
class ResolvedResultRow:
    row_ordinal: int
    owned_items: tuple[ResolvedOwnedItem, ...]
    category: ResultCategory
    replacement_count: int
    owned_set_indices: tuple[int, ...]
    set_piece_counts: tuple[int, ...]
    set_activation_counts: tuple[int, ...]
    effective_final_stats: tuple[int, ...]
    raw_critical_hit_chance: int
    derived_metrics: tuple[int, ...]
    priority_score: float
    constraint_distance: float
    equipped_item_count: int
    replacement_reference: ReplacementMetadataReference | None

    def __post_init__(self) -> None:
        try:
            ordinal = validate_row_ordinal(self.row_ordinal)
        except ResultSchemaError as error:
            raise _wrap(error) from error
        items = tuple(self.owned_items)
        if len(items) != len(GEAR_SLOT_ORDER) or not all(isinstance(item, ResolvedOwnedItem) for item in items):
            raise _error("owned-item-count", "ResolvedResultRow.owned_items", "must contain six resolved owned items.")
        if tuple(item.slot for item in items) != GEAR_SLOT_ORDER:
            raise _error("owned-item-slot-order", "ResolvedResultRow.owned_items", "must use canonical six-slot order.")
        try:
            category = self.category if isinstance(self.category, ResultCategory) else ResultCategory(self.category)
        except (TypeError, ValueError):
            raise _error("invalid-category", "ResolvedResultRow.category", "must be exact, one-away, or two-away.") from None
        replacement_count = _integer(self.replacement_count, "ResolvedResultRow.replacement_count", 0, 2)
        expected_replacement = RESULT_CATEGORY_ORDER.index(category)
        if replacement_count != expected_replacement:
            raise _error("category-distance-mismatch", "ResolvedResultRow.replacement_count", "must equal the category code.")
        sets = tuple(int(item) for item in self.owned_set_indices)
        if sets != tuple(item.set_index for item in items):
            raise _error("owned-set-mismatch", "ResolvedResultRow.owned_set_indices", "must match all six resolved items.")
        pieces = set_piece_counts(sets)
        activations = set_activation_counts(sets)
        if self.set_piece_counts != pieces or self.set_activation_counts != activations:
            raise _error("set-display-evidence-mismatch", "ResolvedResultRow.set_piece_counts", "must be derived from the owned set signature.")
        stats = tuple(int(item) for item in self.effective_final_stats)
        metrics = tuple(int(item) for item in self.derived_metrics)
        if len(stats) != 8 or any(item < 0 for item in stats):
            raise _error("invalid-primary-stats", "ResolvedResultRow.effective_final_stats", "must contain eight nonnegative signed integers.")
        raw_crit = _integer(
            self.raw_critical_hit_chance,
            "ResolvedResultRow.raw_critical_hit_chance",
            0,
            (1 << 63) - 1,
        )
        if stats[4] != min(raw_crit, 100):
            raise _error(
                "critical-hit-chance-cap-mismatch",
                "ResolvedResultRow.raw_critical_hit_chance",
                "must produce the stored effective critical hit chance when capped at 100.",
            )
        if len(metrics) != 15:
            raise _error("invalid-derived-metrics", "ResolvedResultRow.derived_metrics", "must contain 15 signed integers.")
        priority = _binary32(self.priority_score, "ResolvedResultRow.priority_score")
        constraint = _binary32(self.constraint_distance, "ResolvedResultRow.constraint_distance", nonnegative=True)
        equipped = _integer(self.equipped_item_count, "ResolvedResultRow.equipped_item_count", 0, 6)
        expected_equipped = sum(item.gear.equipped_hero_id is not None for item in items)
        if equipped != expected_equipped:
            raise _error("equipped-count-mismatch", "ResolvedResultRow.equipped_item_count", "must match full gear ownership metadata.")
        expected_reference = replacement_metadata_reference(
            ordinal,
            category,
            tuple(item.search_dense_id for item in items),
        )
        if self.replacement_reference != expected_reference:
            raise _error("replacement-reference-mismatch", "ResolvedResultRow.replacement_reference", "must be the canonical lazy near-row reference.")
        object.__setattr__(self, "row_ordinal", ordinal)
        object.__setattr__(self, "owned_items", items)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "replacement_count", replacement_count)
        object.__setattr__(self, "owned_set_indices", sets)
        object.__setattr__(self, "set_piece_counts", pieces)
        object.__setattr__(self, "set_activation_counts", activations)
        object.__setattr__(self, "effective_final_stats", stats)
        object.__setattr__(self, "raw_critical_hit_chance", raw_crit)
        object.__setattr__(self, "derived_metrics", metrics)
        object.__setattr__(self, "priority_score", priority)
        object.__setattr__(self, "constraint_distance", constraint)
        object.__setattr__(self, "equipped_item_count", equipped)


@dataclass(frozen=True, slots=True)
class ResolvedResultPage:
    session_id: str
    run_id: str
    index_cache_key: str
    page: ResolvedPageWindow
    rows: tuple[ResolvedResultRow, ...]
    stats: ResultResolutionStats
    resolution_id: str = RESULT_RESOLUTION_ID
    version: int = RESULT_RESOLUTION_VERSION

    def __post_init__(self) -> None:
        _version(self.resolution_id, self.version, "ResolvedResultPage")
        for name in ("session_id", "run_id"):
            object.__setattr__(self, name, _text(getattr(self, name), f"ResolvedResultPage.{name}"))
        if not isinstance(self.index_cache_key, str) or not _CACHE_KEY.fullmatch(self.index_cache_key):
            raise _error("invalid-index-cache-key", "ResolvedResultPage.index_cache_key", "must be lowercase SHA-256 hex.")
        if not isinstance(self.page, ResolvedPageWindow):
            raise _error("invalid-page-window", "ResolvedResultPage.page", "must be ResolvedPageWindow.")
        rows = tuple(self.rows)
        if not all(isinstance(item, ResolvedResultRow) for item in rows):
            raise _error("invalid-resolved-rows", "ResolvedResultPage.rows", "must contain ResolvedResultRow values.")
        if tuple(item.row_ordinal for item in rows) != self.page.row_ordinals:
            raise _error("resolved-page-order-mismatch", "ResolvedResultPage.rows", "must exactly follow the checked page ordinals.")
        if not isinstance(self.stats, ResultResolutionStats) or self.stats.projection.row_count != len(rows):
            raise _error("resolution-stats-mismatch", "ResolvedResultPage.stats", "must describe every resolved page row.")
        object.__setattr__(self, "rows", rows)


@dataclass(frozen=True, slots=True)
class ResolvedBuildDetail:
    session_id: str
    run_id: str
    index_cache_key: str
    source_page: ResolvedPageWindow
    row: ResolvedResultRow
    stats: ResultResolutionStats
    resolution_id: str = RESULT_RESOLUTION_ID
    version: int = RESULT_RESOLUTION_VERSION

    def __post_init__(self) -> None:
        _version(self.resolution_id, self.version, "ResolvedBuildDetail")
        for name in ("session_id", "run_id"):
            object.__setattr__(self, name, _text(getattr(self, name), f"ResolvedBuildDetail.{name}"))
        if not isinstance(self.index_cache_key, str) or not _CACHE_KEY.fullmatch(self.index_cache_key):
            raise _error("invalid-index-cache-key", "ResolvedBuildDetail.index_cache_key", "must be lowercase SHA-256 hex.")
        if not isinstance(self.source_page, ResolvedPageWindow):
            raise _error("invalid-page-window", "ResolvedBuildDetail.source_page", "must be ResolvedPageWindow.")
        if not isinstance(self.row, ResolvedResultRow):
            raise _error("invalid-resolved-row", "ResolvedBuildDetail.row", "must be ResolvedResultRow.")
        if not isinstance(self.stats, ResultResolutionStats) or self.stats.projection.row_count != 1:
            raise _error("resolution-stats-mismatch", "ResolvedBuildDetail.stats", "must describe exactly one selected row.")
        if self.row.row_ordinal not in self.source_page.row_ordinals:
            raise _error("selected-row-outside-page", "ResolvedBuildDetail.row", "must belong to the checked source page.")


def _same_page(first: ResultPage, second: ResultPage) -> bool:
    return (
        first.request == second.request
        and first.total_rows == second.total_rows
        and first.page_count == second.page_count
        and first.start_offset == second.start_offset
        and first.end_offset == second.end_offset
        and first.has_previous == second.has_previous
        and first.has_next == second.has_next
        and first.out_of_range == second.out_of_range
        and np.array_equal(first.row_ordinals, second.row_ordinals)
    )


def _validate_source(
    run: CompletedResultRun,
    index: CompletedResultSortIndex,
    page: ResultPage,
    session_id: str,
    run_id: str,
    index_cache_key: str,
    context: ResultResolverContext,
) -> None:
    if not isinstance(run, CompletedResultRun):
        raise _error("invalid-completed-run", "run", "must be CompletedResultRun.")
    if not isinstance(index, CompletedResultSortIndex):
        raise _error("invalid-completed-index", "index", "must be CompletedResultSortIndex.")
    if not isinstance(context, ResultResolverContext):
        raise _error("invalid-resolver-context", "context", "must be ResultResolverContext.")
    identities = (
        ("session_id", session_id, context.session_id),
        ("request.run_id", run_id, context.run_id),
        ("completed_run.run_id", run.run_id, context.run_id),
        ("index_cache_key", index_cache_key, index.cache_key),
    )
    for path, actual, expected in identities:
        if actual != expected:
            raise _error("active-session-mismatch", path, f"must equal active value {expected!r}; found {actual!r}.")
    if index.run_fingerprint != result_run_fingerprint(run):
        raise _error(
            "index-run-mismatch",
            "index.run_fingerprint",
            "does not identify the supplied completed result run.",
        )
    expected_page = page_result_sort_index(index, page.request)
    if not _same_page(page, expected_page):
        raise _error(
            "unchecked-result-page",
            "request.page",
            "must exactly match the bounded page produced by the supplied checked index.",
        )
    ordinals = tuple(int(item) for item in page.row_ordinals)
    if len(ordinals) != len(set(ordinals)):
        raise _error("duplicate-page-ordinal", "request.page.row_ordinals", "must identify unique physical rows.")
    if ordinals and max(ordinals) >= run.row_count:
        raise _error("page-ordinal-out-of-range", "request.page.row_ordinals", "contains a row outside the completed run.")


def _read_visible_columns(
    run: CompletedResultRun,
    ordinals: tuple[int, ...],
) -> dict[str, np.ndarray[Any, Any]]:
    count = len(ordinals)
    if count == 0:
        return {
            spec.name: np.empty((0, *spec.shape), dtype=spec.dtype)
            for spec in RESULT_SCHEMA.columns
        }
    selected_ordinals = np.asarray(ordinals, dtype="<u4")
    arrays: dict[str, np.ndarray[Any, Any]] = {}
    for spec in RESULT_SCHEMA.columns:
        try:
            descriptor = run.column_spec(spec.name)
        except KeyError:
            raise _error("missing-result-column", spec.name, "is absent from the completed run.") from None
        if descriptor.dtype.str != spec.dtype.str or descriptor.shape != (run.row_count, *spec.shape):
            raise _error(
                "result-column-contract-mismatch",
                spec.name,
                "completed column metadata does not match the v1 schema.",
            )
        column: np.ndarray[Any, Any] | None = None
        try:
            column = run.open_column(spec.name)
            if column.dtype.str != spec.dtype.str or column.shape != descriptor.shape:
                raise _error(
                    "result-column-contract-mismatch",
                    spec.name,
                    "opened column does not match its completed descriptor.",
                )
            copied = np.asarray(column[selected_ordinals]).copy(order="C")
        except ResultResolutionError:
            raise
        except (OSError, ValueError, IndexError, MemoryError) as error:
            raise _error("result-column-read-failed", spec.name, str(error)) from error
        finally:
            mapping = None if column is None else getattr(column, "_mmap", None)
            if mapping is not None:
                mapping.close()
        if copied.dtype.str != spec.dtype.str or copied.shape != (count, *spec.shape):
            raise _error(
                "result-column-copy-mismatch",
                spec.name,
                "visible-row copy narrowed or reshaped the stored values.",
            )
        arrays[spec.name] = copied
    try:
        validate_result_columns(arrays, row_count=count)
    except ResultSchemaError as error:
        raise _wrap(error) from error
    return arrays


def _resolve_ordinals(
    run: CompletedResultRun,
    ordinals: tuple[int, ...],
    context: ResultResolverContext,
) -> tuple[tuple[ResolvedResultRow, ...], int]:
    if len(ordinals) > MAX_PAGE_SIZE:
        raise _error("oversized-resolution", "ordinals", f"must not exceed {MAX_PAGE_SIZE} rows.")
    arrays = _read_visible_columns(run, ordinals)
    item_cache: dict[int, ResolvedOwnedItem] = {}
    rows: list[ResolvedResultRow] = []
    for local_index, ordinal in enumerate(ordinals):
        dense_ids = tuple(int(item) for item in arrays["dense_item_ids"][local_index])
        set_indices = tuple(int(item) for item in arrays["owned_set_indices"][local_index])
        owned: list[ResolvedOwnedItem] = []
        for slot_index, (dense_id, set_index) in enumerate(zip(dense_ids, set_indices, strict=True)):
            if dense_id < 0 or dense_id >= len(context.search_gear_by_dense_id):
                raise _error(
                    "unknown-search-dense-id",
                    f"row[{ordinal}].dense_item_ids[{slot_index}]",
                    "is absent from the active prepared search snapshot.",
                )
            if context.search_slot_index_by_dense_id[dense_id] != slot_index:
                raise _error(
                    "dense-id-slot-mismatch",
                    f"row[{ordinal}].dense_item_ids[{slot_index}]",
                    "does not belong to this canonical gear slot.",
                )
            if context.search_set_index_by_dense_id[dense_id] != set_index:
                raise _error(
                    "dense-id-set-mismatch",
                    f"row[{ordinal}].owned_set_indices[{slot_index}]",
                    "does not match the active search/full-gear snapshot.",
                )
            resolved = item_cache.get(dense_id)
            if resolved is None:
                gear = context.search_gear_by_dense_id[dense_id]
                resolved = ResolvedOwnedItem(
                    search_dense_id=dense_id,
                    stable_item_id=context.slot_arrays.stable_item_id_for_dense_id(dense_id),
                    slot=GEAR_SLOT_ORDER[slot_index],
                    set_index=set_index,
                    gear=gear,
                )
                item_cache[dense_id] = resolved
            owned.append(resolved)
        category = decode_result_category(int(arrays["category_codes"][local_index]))
        replacement_count = int(arrays["replacement_distances"][local_index])
        pieces = set_piece_counts(set_indices)
        required = context.target_pattern.required_piece_counts
        if (
            category is not ResultCategory.EXACT
            or replacement_count != 0
            or any(actual < minimum for actual, minimum in zip(pieces, required, strict=True))
        ):
            raise _error(
                "stored-category-target-mismatch",
                f"row[{ordinal}].category",
                "must be an exact result matching the active target pattern.",
            )
        activations = set_activation_counts(set_indices)
        rows.append(
            ResolvedResultRow(
                row_ordinal=ordinal,
                owned_items=tuple(owned),
                category=category,
                replacement_count=replacement_count,
                owned_set_indices=set_indices,
                set_piece_counts=pieces,
                set_activation_counts=activations,
                effective_final_stats=tuple(
                    int(item) for item in arrays["effective_final_stats"][local_index]
                ),
                raw_critical_hit_chance=int(
                    arrays["raw_critical_hit_chances"][local_index]
                ),
                derived_metrics=tuple(int(item) for item in arrays["derived_metrics"][local_index]),
                priority_score=float(arrays["priority_scores"][local_index]),
                constraint_distance=float(arrays["constraint_distances"][local_index]),
                equipped_item_count=int(arrays["equipped_item_counts"][local_index]),
                replacement_reference=replacement_metadata_reference(ordinal, category, dense_ids),
            )
        )
    return tuple(rows), len(item_cache)


def resolve_result_page(
    run: CompletedResultRun,
    index: CompletedResultSortIndex,
    request: ResultPageRowsRequest,
    context: ResultResolverContext,
) -> ResolvedResultPage:
    """Resolve exactly one checked page, never its off-page neighbors."""

    if not isinstance(request, ResultPageRowsRequest):
        raise _error("invalid-page-resolution-request", "request", "must be ResultPageRowsRequest.")
    _validate_source(
        run,
        index,
        request.page,
        request.session_id,
        request.run_id,
        request.index_cache_key,
        context,
    )
    ordinals = tuple(int(item) for item in request.page.row_ordinals)
    rows, unique = _resolve_ordinals(run, ordinals, context)
    projection = project_result_resolution(len(rows))
    return ResolvedResultPage(
        session_id=context.session_id,
        run_id=context.run_id,
        index_cache_key=index.cache_key,
        page=ResolvedPageWindow.from_page(request.page),
        rows=rows,
        stats=ResultResolutionStats(projection, unique),
    )


def resolve_result_build_detail(
    run: CompletedResultRun,
    index: CompletedResultSortIndex,
    request: ResultBuildDetailRequest,
    context: ResultResolverContext,
) -> ResolvedBuildDetail:
    """Resolve one exact selected row from the checked visible page."""

    if not isinstance(request, ResultBuildDetailRequest):
        raise _error("invalid-detail-resolution-request", "request", "must be ResultBuildDetailRequest.")
    _validate_source(
        run,
        index,
        request.page,
        request.session_id,
        request.run_id,
        request.index_cache_key,
        context,
    )
    page_ordinals = tuple(int(item) for item in request.page.row_ordinals)
    if request.row_ordinal not in page_ordinals:
        raise _error(
            "selected-row-outside-page",
            "request.row_ordinal",
            "must be one of the physical ordinals in the checked source page.",
        )
    rows, unique = _resolve_ordinals(run, (request.row_ordinal,), context)
    row = rows[0]
    projection = project_result_resolution(1)
    return ResolvedBuildDetail(
        session_id=context.session_id,
        run_id=context.run_id,
        index_cache_key=index.cache_key,
        source_page=ResolvedPageWindow.from_page(request.page),
        row=row,
        stats=ResultResolutionStats(projection, unique),
    )


__all__ = [
    "RESULT_RESOLUTION_ID",
    "RESULT_RESOLUTION_VERSION",
    "ResolvedBuildDetail",
    "ResolvedOwnedItem",
    "ResolvedPageWindow",
    "ResolvedResultPage",
    "ResolvedResultRow",
    "ResultBuildDetailRequest",
    "ResultPageRowsRequest",
    "ResultResolutionError",
    "ResultResolutionProjection",
    "ResultResolutionStats",
    "ResultResolverContext",
    "project_result_resolution",
    "resolve_result_build_detail",
    "resolve_result_page",
]
