"""Trusted bounded result filtering, indexing, and page resolution for desktop."""

from __future__ import annotations

import math
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from src.desktop.optimizer_search_service import PreparedOptimizerSearch
from src.optimizer.data import (
    BUNDLED_ARTIFACT_SOURCE_PATH,
    BUNDLED_CATALOG_FILENAME,
    BUNDLED_CHARACTER_DATA_DIRECTORY,
    BUNDLED_SOURCE_FILENAME,
    CharacterRepository,
    DenseInventorySnapshot,
    InventoryRepository,
    load_bundled_character_repository,
)
from src.optimizer.domain import (
    FRIBBELS_SET_ORDER,
    GEAR_RANK_CATALOG,
    GEAR_SLOT_CATALOG,
    ITEM_STAT_CATALOG,
    ItemProjectionMode,
    RESULT_CATEGORY_ORDER,
    SET_CATALOG,
    ResultCategory,
)
from src.optimizer.engine import ProjectedGearItem
from src.optimizer.result_store import (
    CompletedResultSortIndex,
    MAX_PAGE_SIZE,
    RESULT_DERIVED_METRIC_ORDER,
    RESULT_PRIMARY_STAT_ORDER,
    RESULT_SORT_KEYS,
    RESULT_SORT_KEYS_BY_ID,
    InclusiveFloat32Range,
    InclusiveInt64Range,
    OriginalResultScope,
    ResultBuildDetailRequest,
    ResultExportError,
    ResultExportFormat,
    ResultExportRequest,
    REQUIRED_DATA_VERSION_CONTRACTS,
    ResultDataVersionEvidence,
    ResultExecutionBackend,
    ResultExecutionEvidence,
    ResultFilterCancelled,
    ResultFilterRequest,
    ResultPageRequest,
    ResultPageRowsRequest,
    ResultResolverContext,
    ResultSortDirection,
    ResultSortIndexCache,
    ResultSortRequest,
    build_result_sort_index,
    create_sorted_export_view,
    build_result_reproducibility_record,
    export_result_view,
    filter_completed_result_run,
    load_result_reproducibility,
    persist_result_reproducibility,
    page_result_sort_index,
    resolve_result_build_detail,
    resolve_result_page,
)


_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_REQUEST_FIELDS = frozenset({
    "runId", "category", "sortKey", "direction", "pageIndex", "pageSize",
    "primaryRanges", "derivedRanges", "priorityScore", "constraintDistance",
    "replacementCount", "equippedCount",
})
_RANGE_FIELDS = frozenset({"minimum", "maximum"})
_CATEGORY_BY_ID = {
    "exact": (ResultCategory.EXACT,),
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _result_data_versions() -> tuple[ResultDataVersionEvidence, ...]:
    paths = {
        "artifact-catalog": BUNDLED_CHARACTER_DATA_DIRECTORY / BUNDLED_ARTIFACT_SOURCE_PATH,
        "character-catalog": BUNDLED_CHARACTER_DATA_DIRECTORY / BUNDLED_CATALOG_FILENAME,
        "skill-context-catalog": BUNDLED_CHARACTER_DATA_DIRECTORY / BUNDLED_SOURCE_FILENAME,
    }
    return tuple(
        ResultDataVersionEvidence(component, schema, version, _sha256_file(paths[component]))
        for component, (schema, version) in REQUIRED_DATA_VERSION_CONTRACTS.items()
    )


def _result_execution_evidence(prepared: PreparedOptimizerSearch) -> ResultExecutionEvidence:
    if prepared.backend == "cuda":
        diagnostic = prepared.cuda_diagnostic
        return ResultExecutionEvidence(
            ResultExecutionBackend.CUDA,
            "e7-cupy-packed-exact-v2",
            device_name=diagnostic.device_name,
            runtime_version=str(diagnostic.runtime_version) if diagnostic.runtime_version is not None else None,
        )
    return ResultExecutionEvidence(ResultExecutionBackend.CPU, "python-numpy-reference-v1")
_PRIMARY_PUBLIC_IDS = (
    "attack", "health", "defense", "speed", "criticalHitChancePercent",
    "criticalHitDamagePercent", "effectivenessPercent", "effectResistancePercent",
)
_PRIMARY_LABELS = (
    "Attack", "Health", "Defense", "Speed", "Critical Hit Chance",
    "Critical Hit Damage", "Effectiveness", "Effect Resistance",
)
_DERIVED_LABELS = (
    "Build Score", "Combat Power", "Average Damage", "Damage × Defense",
    "Damage × Health", "Damage × Speed", "Effective Health", "EHP × Speed",
    "Gear Score", "Health × Speed", "Max Critical Damage", "MCD × Speed",
    "S1", "S2", "S3",
)

ProgressSink = Callable[[str, int, int], None]
CancellationPredicate = Callable[[], bool]
ExportProgressSink = Callable[[int, int], None]
class OptimizerResultServiceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OptimizerResultCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OptimizerResultPageContext:
    query_id: str
    run_id: str
    prepared: PreparedOptimizerSearch
    run: object
    index: CompletedResultSortIndex
    page: object
    resolver_context: object
    row_ordinals: Mapping[str, int]


class OptimizerResultExecution(dict[str, Any]):
    """Public page mapping with one backend-private checked-page capability."""

    def __init__(self, payload: Mapping[str, Any], detail_context: OptimizerResultPageContext | None = None) -> None:
        super().__init__(payload)
        self.detail_context = detail_context


def _label(identifier: str) -> str:
    return identifier.rsplit(".", 1)[-1].replace("-", " ").replace("_", " ").title()


def result_options() -> dict[str, Any]:
    primary = [
        {"fieldId": public_id, "label": label, "sortKey": f"primary:{item.value}"}
        for item, public_id, label in zip(
            RESULT_PRIMARY_STAT_ORDER, _PRIMARY_PUBLIC_IDS, _PRIMARY_LABELS, strict=True
        )
    ]
    derived = [
        {"fieldId": item, "label": label, "sortKey": f"derived:{item}"}
        for item, label in zip(RESULT_DERIVED_METRIC_ORDER, _DERIVED_LABELS, strict=True)
    ]
    extra_labels = {
        "priority-score": "Priority score",
        "equipped-count": "Equipped count",
    }
    return {
        "maxPageSize": MAX_PAGE_SIZE,
        "primaryFields": primary,
        "derivedFields": derived,
        "sortOptions": [
            {
                "sortKey": item.key_id,
                "label": next(
                    (field["label"] for field in (*primary, *derived) if field["sortKey"] == item.key_id),
                    extra_labels.get(item.key_id, _label(item.key_id)),
                ),
            }
            for item in RESULT_SORT_KEYS
            if item.key_id not in {"constraint-distance", "replacement-count"}
        ],
    }


def _object(value: object, fields: frozenset[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields or not all(isinstance(key, str) for key in value):
        raise OptimizerResultServiceError("invalid-query", f"{name} has unsupported fields.")
    return value


def _int_endpoint(value: object, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise OptimizerResultServiceError("invalid-range", f"{name} must be a decimal string or null.")
    try:
        parsed = int(value, 10)
    except ValueError:
        raise OptimizerResultServiceError("invalid-range", f"{name} must be a decimal string or null.") from None
    if str(parsed) != value or parsed < _INT64_MIN or parsed > _INT64_MAX:
        raise OptimizerResultServiceError("invalid-range", f"{name} is outside signed int64 or is not canonical.")
    return parsed


def _int_range(value: object, name: str) -> InclusiveInt64Range:
    data = _object(value, _RANGE_FIELDS, name)
    return InclusiveInt64Range(
        _int_endpoint(data["minimum"], f"{name}.minimum"),
        _int_endpoint(data["maximum"], f"{name}.maximum"),
    )


def _float_endpoint(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise OptimizerResultServiceError("invalid-range", f"{name} must be a finite number or null.")
    return float(np.float32(float(value)))


def _float_range(value: object, name: str) -> InclusiveFloat32Range:
    data = _object(value, _RANGE_FIELDS, name)
    return InclusiveFloat32Range(
        _float_endpoint(data["minimum"], f"{name}.minimum"),
        _float_endpoint(data["maximum"], f"{name}.maximum"),
    )


def _axis_ranges(
    value: object,
    names: tuple[Any, ...],
    label: str,
) -> tuple[InclusiveInt64Range, ...]:
    expected = tuple(item.value if hasattr(item, "value") else item for item in names)
    data = _object(value, frozenset(expected), label)
    return tuple(_int_range(data[name], f"{label}.{name}") for name in expected)


def _available_categories(prepared: PreparedOptimizerSearch) -> tuple[ResultCategory, ...]:
    return (ResultCategory.EXACT,)


def _parse_request(
    payload: Mapping[str, object],
    prepared: PreparedOptimizerSearch,
) -> tuple[ResultFilterRequest, ResultSortRequest, ResultPageRequest, str]:
    data = _object(payload, _REQUEST_FIELDS, "query")
    run_id = data["runId"]
    if not isinstance(run_id, str) or not run_id:
        raise OptimizerResultServiceError("invalid-query", "runId must be non-empty text.")
    category = data["category"]
    if category == "all":
        categories = _available_categories(prepared)
    elif isinstance(category, str) and category in _CATEGORY_BY_ID:
        categories = _CATEGORY_BY_ID[category]
    else:
        raise OptimizerResultServiceError("invalid-query", "category must be all or exact.")
    sort_key = data["sortKey"]
    if not isinstance(sort_key, str) or sort_key not in RESULT_SORT_KEYS_BY_ID:
        raise OptimizerResultServiceError("invalid-query", "sortKey is not supported.")
    direction = data["direction"]
    if direction not in {"ascending", "descending"}:
        raise OptimizerResultServiceError("invalid-query", "direction must be ascending or descending.")
    page_index = data["pageIndex"]
    page_size = data["pageSize"]
    if isinstance(page_index, bool) or not isinstance(page_index, int):
        raise OptimizerResultServiceError("invalid-query", "pageIndex must be an integer.")
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise OptimizerResultServiceError("invalid-query", "pageSize must be an integer.")
    requested = ResultFilterRequest(
        categories=categories,
        primary_ranges=_axis_ranges(data["primaryRanges"], _PRIMARY_PUBLIC_IDS, "primaryRanges"),
        derived_ranges=_axis_ranges(data["derivedRanges"], RESULT_DERIVED_METRIC_ORDER, "derivedRanges"),
        priority_score=_float_range(data["priorityScore"], "priorityScore"),
        constraint_distance=_float_range(data["constraintDistance"], "constraintDistance"),
        replacement_distance=_int_range(data["replacementCount"], "replacementCount"),
        equipped_count=_int_range(data["equippedCount"], "equippedCount"),
    )
    return (
        requested,
        ResultSortRequest(sort_key=RESULT_SORT_KEYS_BY_ID[sort_key], direction=ResultSortDirection(direction)),
        ResultPageRequest(page_index=page_index, page_size=page_size),
        run_id,
    )


def _close_index(index: object) -> None:
    values = getattr(index, "row_ordinals", None)
    mapping = getattr(values, "_mmap", None)
    if mapping is not None:
        mapping.close()


def _owned_index(index: CompletedResultSortIndex) -> CompletedResultSortIndex:
    """Detach a bounded active index from any cache-backed memmap."""

    values = np.asarray(index.row_ordinals, dtype="<u4").copy()
    values.flags.writeable = False
    return replace(index, row_ordinals=values, cache_path=None)


def _public_category(category: ResultCategory) -> str:
    if category is not ResultCategory.EXACT:
        raise OptimizerResultServiceError("invalid-result", "Only exact-set results are supported.")
    return "exact"


def _display_primary_stats(row: object) -> tuple[int, ...]:
    values = list(row.effective_final_stats)
    values[4] = row.raw_critical_hit_chance
    return tuple(values)


def _range_status(actual: int, interval: object | None) -> str:
    if interval is None:
        return "unrestricted"
    return "satisfied" if interval.contains(actual) else "outside-target"


def _public_constraints(prepared: PreparedOptimizerSearch, row: object) -> dict[str, Any]:
    primary_ranges = dict(prepared.request.stat_ranges)
    derived_ranges = dict(prepared.request.derived_metric_ranges)
    primary = []
    for stat, public_id, label, actual in zip(
        RESULT_PRIMARY_STAT_ORDER,
        _PRIMARY_PUBLIC_IDS,
        _PRIMARY_LABELS,
        _display_primary_stats(row),
        strict=True,
    ):
        interval = primary_ranges.get(stat)
        primary.append({
            "fieldId": public_id,
            "label": label,
            "actual": str(actual),
            "minimum": None if interval is None else interval.minimum,
            "maximum": None if interval is None else interval.maximum,
            "status": _range_status(actual, interval),
        })
    derived = []
    for metric, label, actual in zip(
        RESULT_DERIVED_METRIC_ORDER,
        _DERIVED_LABELS,
        row.derived_metrics,
        strict=True,
    ):
        interval = derived_ranges.get(metric)
        derived.append({
            "fieldId": metric,
            "label": label,
            "actual": str(actual),
            "minimum": None if interval is None else interval.minimum,
            "maximum": None if interval is None else interval.maximum,
            "status": _range_status(actual, interval),
        })
    return {
        "status": "satisfied" if row.constraint_distance == 0 else "outside-target",
        "normalizedDistance": row.constraint_distance,
        "primary": primary,
        "derived": derived,
    }


def _public_sets(prepared: PreparedOptimizerSearch, row: object) -> list[dict[str, Any]]:
    required = prepared.target_pattern.required_piece_counts
    result = []
    for set_index, gear_set in enumerate(FRIBBELS_SET_ORDER):
        pieces = row.set_piece_counts[set_index]
        target = required[set_index]
        if not pieces and not target:
            continue
        status = (
            "target-complete" if target and pieces >= target
            else "target-incomplete" if target
            else "off-target"
        )
        result.append({
            "setId": gear_set.value,
            "label": SET_CATALOG[gear_set].display_name,
            "pieces": pieces,
            "activations": row.set_activation_counts[set_index],
            "requiredPieces": target,
            "status": status,
        })
    return result


def _slot_gear_score(prepared: PreparedOptimizerSearch, slot_index: int, dense_id: int) -> int:
    slot = prepared.slot_arrays.slots[slot_index]
    try:
        offset = slot.dense_ids.index(dense_id)
    except ValueError:
        raise OptimizerResultServiceError(
            "detail-context-mismatch",
            "The selected gear no longer matches the completed search context.",
        ) from None
    return slot.gear_scores[offset]


def _public_gear(
    prepared: PreparedOptimizerSearch,
    row: object,
    stored_by_id: Mapping[str, object],
    owners_by_id: Mapping[str, object],
    characters: CharacterRepository,
) -> list[dict[str, Any]]:
    selected_hero = characters.get(prepared.request.hero_id)
    result = []
    for slot_index, resolved in enumerate(row.owned_items):
        stored = stored_by_id.get(resolved.stable_item_id)
        if (
            stored is None
            or replace(stored.gear_item, equipped_hero_id=None)
            != replace(resolved.gear, dense_id=None, equipped_hero_id=None)
        ):
            raise OptimizerResultServiceError(
                "inventory-changed",
                "Owned gear changed. Run the optimizer search again.",
            )
        gear = stored.gear_item
        gear_key = f"gear-{slot_index + 1}"
        owner = owners_by_id.get(gear.equipped_hero_id) if gear.equipped_hero_id is not None else None
        stored_owner_name = getattr(owner, "name", None)
        imported_owner_name = (
            stored_owner_name
            if isinstance(stored_owner_name, str) and stored_owner_name.strip()
            else stored.equipped_by_name
        )
        canonical_owner = characters.find_exact(imported_owner_name)
        selected_owner = (
            gear.equipped_hero_id is not None
            and (
                gear.equipped_hero_id == prepared.request.hero_id
                or (
                    canonical_owner is not None
                    and canonical_owner.hero_id == prepared.request.hero_id
                )
            )
        )
        equipped = (
            "unequipped" if gear.equipped_hero_id is None
            else "selected-hero" if selected_owner
            else "other-hero"
        )
        equipped_hero_name = (
            None
            if gear.equipped_hero_id is None
            else selected_hero.name
            if selected_owner
            else canonical_owner.name
            if canonical_owner is not None
            else None
        )
        projected = ProjectedGearItem.from_fribbels_inventory_item(stored)
        reforged_substats = dict(projected.totals_for(ItemProjectionMode.REFORGED))
        assert projected.main_stat is not None
        reforged_substats[projected.main_stat] -= projected.main_value_for(
            ItemProjectionMode.REFORGED
        )
        result.append({
            "gearKey": gear_key,
            "slotId": gear.slot.value,
            "slotLabel": GEAR_SLOT_CATALOG[gear.slot].display_name,
            "setId": gear.gear_set.value,
            "setLabel": SET_CATALOG[gear.gear_set].display_name,
            "rankId": stored.rank.value,
            "rankLabel": GEAR_RANK_CATALOG[stored.rank].display_name,
            "itemLevel": gear.item_level,
            "enhance": gear.enhance,
            "gearScore": _slot_gear_score(prepared, slot_index, resolved.search_dense_id),
            "locked": gear.locked,
            "equippedStatus": equipped,
            "equippedHeroName": equipped_hero_name,
            "mainStat": {
                "statId": gear.main_stat.value,
                "label": ITEM_STAT_CATALOG[gear.main_stat].display_name,
                "value": gear.main_stat_value,
            },
            "substats": [
                {
                    "statId": stat.value,
                    "label": ITEM_STAT_CATALOG[stat].display_name,
                    "value": value,
                    "reforgedValue": reforged_substats[stat],
                }
                for stat, value, _raw in stored.source_substat_rows()
            ],
        })
    return result


def _public_guidance() -> dict[str, Any]:
    return {
        "kind": "set-complete",
        "message": "The selected set pattern is complete.",
    }


class OptimizerResultService:
    def __init__(
        self,
        user_data_dir: str | Path,
        result_store: object,
        character_repository: CharacterRepository | None = None,
    ) -> None:
        root = Path(user_data_dir)
        self.database_path = root / "optimizer.db"
        self.result_store = result_store
        self.characters = character_repository or load_bundled_character_repository()
        self.sort_cache = ResultSortIndexCache(root / "optimizer_result_sort_cache")

    def inventory_is_current(self, prepared: PreparedOptimizerSearch) -> bool:
        try:
            repository = InventoryRepository(self.database_path)
            repository.initialize()
            current = repository.dense_snapshot()
            original = prepared.inventory_snapshot
            if not isinstance(original, DenseInventorySnapshot):
                return current == original
            if current.dense_id_to_stable_id != original.dense_id_to_stable_id:
                return False
            current_items = tuple(
                replace(item, equipped_hero_id=None)
                for _, slot_items in current.items_by_slot
                for item in slot_items
            )
            original_items = tuple(
                replace(item, equipped_hero_id=None)
                for _, slot_items in original.items_by_slot
                for item in slot_items
            )
            return current_items == original_items
        except Exception:
            return False

    def execute(
        self,
        prepared: PreparedOptimizerSearch,
        active_run_id: str,
        query_id: str,
        payload: Mapping[str, object],
        should_cancel: CancellationPredicate,
        on_progress: ProgressSink,
    ) -> OptimizerResultExecution:
        requested, sort_request, page_request, supplied_run_id = _parse_request(payload, prepared)
        if supplied_run_id != active_run_id:
            raise OptimizerResultServiceError("stale-run", "The requested result run is no longer active.")
        if not self.inventory_is_current(prepared):
            raise OptimizerResultServiceError("inventory-changed", "Owned gear changed. Run the optimizer search again.")
        if should_cancel():
            raise OptimizerResultCancelled()
        run = self.result_store.open_run(active_run_id)
        baseline = ResultFilterRequest(categories=_available_categories(prepared))
        scope = OriginalResultScope.create(baseline, prepared.target_pattern)
        view = None
        if requested != baseline:
            try:
                outcome = filter_completed_result_run(
                    run,
                    requested,
                    scope,
                    should_cancel=should_cancel,
                    on_progress=lambda done, total: on_progress("filtering", done, total),
                )
            except ResultFilterCancelled:
                raise OptimizerResultCancelled() from None
            if outcome.rerun_required:
                return OptimizerResultExecution({
                    "kind": "rerun-required",
                    "reasons": list(outcome.assessment.reasons),
                })
            view = outcome.view
        if should_cancel():
            raise OptimizerResultCancelled()
        matched = run.row_count if view is None else int(view.row_ordinals.size)
        on_progress("sorting", 0, matched)
        index = build_result_sort_index(run, sort_request, view=view, cache=self.sort_cache)
        try:
            if should_cancel():
                raise OptimizerResultCancelled()
            page = page_result_sort_index(index, page_request)
            context = ResultResolverContext(
                session_id=f"result-session-{active_run_id}",
                run_id=active_run_id,
                selected_hero_id=prepared.request.hero_id,
                inventory_snapshot=prepared.inventory_snapshot,
                slot_arrays=prepared.slot_arrays,
                evaluation_context=prepared.evaluation_context,
                target_pattern=prepared.target_pattern,
            )
            on_progress("resolving", 0, page.returned_rows)
            resolved = resolve_result_page(
                run,
                index,
                ResultPageRowsRequest(context.session_id, active_run_id, index.cache_key, page),
                context,
            )
            rows = []
            row_ordinals: dict[str, int] = {}
            for offset, row in enumerate(resolved.rows):
                row_key = f"{query_id}.{page.start_offset + offset}"
                row_ordinals[row_key] = row.row_ordinal
                sets = [
                    {
                        "setId": FRIBBELS_SET_ORDER[set_index].value,
                        "label": _label(FRIBBELS_SET_ORDER[set_index].value),
                        "pieces": row.set_piece_counts[set_index],
                        "activations": row.set_activation_counts[set_index],
                    }
                    for set_index in range(len(FRIBBELS_SET_ORDER))
                    if row.set_piece_counts[set_index]
                ]
                rows.append({
                    "rowKey": row_key,
                    "category": _public_category(row.category),
                    "replacementCount": row.replacement_count,
                    "equippedCount": row.equipped_item_count,
                    "priorityScore": row.priority_score,
                    "constraintDistance": row.constraint_distance,
                    "primaryStats": {
                        public_id: str(value)
                        for public_id, value in zip(_PRIMARY_PUBLIC_IDS, _display_primary_stats(row), strict=True)
                    },
                    "derivedMetrics": {
                        metric: str(value)
                        for metric, value in zip(RESULT_DERIVED_METRIC_ORDER, row.derived_metrics, strict=True)
                    },
                    "sets": sets,
                })
            if not self.inventory_is_current(prepared):
                raise OptimizerResultServiceError("inventory-changed", "Owned gear changed. Run the optimizer search again.")
            owned_index = _owned_index(index)
            detail_context = OptimizerResultPageContext(
                query_id=query_id,
                run_id=active_run_id,
                prepared=prepared,
                run=run,
                index=owned_index,
                page=page,
                resolver_context=context,
                row_ordinals=row_ordinals,
            )
            return OptimizerResultExecution({
                "kind": "page",
                "filteredRows": str(page.total_rows),
                "pageIndex": page.request.page_index,
                "pageSize": page.request.page_size,
                "pageCount": page.page_count,
                "startOffset": str(page.start_offset),
                "endOffset": str(page.end_offset),
                "hasPrevious": page.has_previous,
                "hasNext": page.has_next,
                "outOfRange": page.out_of_range,
                "rows": rows,
            }, detail_context)
        finally:
            _close_index(index)

    def resolve_detail(
        self,
        page_context: OptimizerResultPageContext,
        run_id: str,
        query_id: str,
        row_key: str,
    ) -> dict[str, Any]:
        prepared, detail = self._resolve_detail_row(
            page_context,
            run_id,
            query_id,
            row_key,
        )
        try:
            repository = InventoryRepository(self.database_path)
            stored = repository.load_inventory()
            stored_by_id = {item.stable_item_id: item for item in stored}
            owners_by_id = {hero.hero_id: hero for hero in repository.load_heroes()}
            gear = _public_gear(
                prepared,
                detail.row,
                stored_by_id,
                owners_by_id,
                self.characters,
            )
            guidance = _public_guidance()
        except OptimizerResultServiceError:
            raise
        except Exception:
            raise OptimizerResultServiceError(
                "detail-projection-failed",
                "The selected build detail could not be prepared safely.",
            ) from None
        if not self.inventory_is_current(prepared):
            raise OptimizerResultServiceError("inventory-changed", "Owned gear changed. Run the optimizer search again.")
        return {
            "category": _public_category(detail.row.category),
            "replacementCount": detail.row.replacement_count,
            "equippedCount": sum(
                item["equippedStatus"] != "unequipped"
                for item in gear
            ),
            "priorityScore": detail.row.priority_score,
            "constraintDistance": detail.row.constraint_distance,
            "primaryStats": {
                public_id: str(value)
                for public_id, value in zip(_PRIMARY_PUBLIC_IDS, _display_primary_stats(detail.row), strict=True)
            },
            "derivedMetrics": {
                metric: str(value)
                for metric, value in zip(RESULT_DERIVED_METRIC_ORDER, detail.row.derived_metrics, strict=True)
            },
            "constraints": _public_constraints(prepared, detail.row),
            "sets": _public_sets(prepared, detail.row),
            "gear": gear,
            "guidance": guidance,
        }

    def _resolve_detail_row(
        self,
        page_context: OptimizerResultPageContext,
        run_id: str,
        query_id: str,
        row_key: str,
    ) -> tuple[PreparedOptimizerSearch, object]:
        if not isinstance(page_context, OptimizerResultPageContext):
            raise OptimizerResultServiceError("detail-unavailable", "The selected result page is no longer active.")
        if run_id != page_context.run_id or query_id != page_context.query_id:
            raise OptimizerResultServiceError("stale-detail", "The selected result page is no longer active.")
        row_ordinal = page_context.row_ordinals.get(row_key)
        if row_ordinal is None:
            raise OptimizerResultServiceError("unknown-row", "Choose a build from the currently visible result page.")
        prepared = page_context.prepared
        if not self.inventory_is_current(prepared):
            raise OptimizerResultServiceError("inventory-changed", "Owned gear changed. Run the optimizer search again.")
        try:
            detail = resolve_result_build_detail(
                page_context.run,
                page_context.index,
                ResultBuildDetailRequest(
                    page_context.resolver_context.session_id,
                    run_id,
                    page_context.index.cache_key,
                    page_context.page,
                    row_ordinal,
                ),
                page_context.resolver_context,
            )
        except OptimizerResultServiceError:
            raise
        except Exception:
            raise OptimizerResultServiceError(
                "detail-resolution-failed",
                "The selected build could not be verified against the completed result page.",
            ) from None
        return prepared, detail

    def equip_build(
        self,
        page_context: OptimizerResultPageContext,
        run_id: str,
        query_id: str,
        row_key: str,
    ) -> dict[str, Any]:
        """Apply one visible exact build to local imported ownership state."""

        prepared, detail = self._resolve_detail_row(
            page_context,
            run_id,
            query_id,
            row_key,
        )
        try:
            repository = InventoryRepository(self.database_path)
            stored = repository.load_inventory()
            stored_by_id = {item.stable_item_id: item for item in stored}
            imported_heroes = repository.load_heroes()
            owners_by_id = {hero.hero_id: hero for hero in imported_heroes}
            _public_gear(
                prepared,
                detail.row,
                stored_by_id,
                owners_by_id,
                self.characters,
            )
            selected_hero = self.characters.get(prepared.request.hero_id)
            matching_owners = [
                hero
                for hero in imported_heroes
                if isinstance(hero.name, str)
                and (canonical := self.characters.find_exact(hero.name)) is not None
                and canonical.hero_id == selected_hero.hero_id
            ]
            if len(matching_owners) != 1:
                raise OptimizerResultServiceError(
                    "equip-hero-unavailable",
                    "The selected character is not uniquely present in the imported gear.txt. Import current game data before equipping this build locally.",
                )
            assignment = repository.assign_equipment_build(
                matching_owners[0].hero_id,
                selected_hero.name,
                tuple(item.stable_item_id for item in detail.row.owned_items),
            )
        except OptimizerResultServiceError:
            raise
        except Exception:
            raise OptimizerResultServiceError(
                "equip-write-failed",
                "The selected build could not be equipped locally; no ownership changes were applied.",
            ) from None
        return {
            "state": "equipped",
            "heroName": selected_hero.name,
            "equippedCount": assignment.assigned_items,
            "alreadyEquipped": assignment.already_on_target,
            "movedFromOtherHeroes": assignment.moved_from_other_heroes,
            "newlyEquipped": assignment.newly_equipped_items,
            "unequippedFromHero": assignment.unequipped_from_target,
            "inventoryEquippedItems": assignment.total_equipped_items,
        }

    def export_active_view(
        self,
        page_context: OptimizerResultPageContext,
        run_id: str,
        query_id: str,
        destination: str | Path,
        export_format: str,
        should_cancel: CancellationPredicate,
        on_progress: ExportProgressSink,
    ) -> dict[str, Any]:
        """Stream the complete active sorted view to one trusted destination."""

        if not isinstance(page_context, OptimizerResultPageContext):
            raise OptimizerResultServiceError(
                "export-unavailable",
                "Export requires the active completed result view.",
            )
        if run_id != page_context.run_id or query_id != page_context.query_id:
            raise OptimizerResultServiceError(
                "stale-export",
                "The result view changed before export started.",
            )
        if not self.inventory_is_current(page_context.prepared):
            raise OptimizerResultServiceError(
                "inventory-changed",
                "Owned gear changed. Run the optimizer search again.",
            )
        try:
            format_value = ResultExportFormat(export_format)
        except (TypeError, ValueError):
            raise OptimizerResultServiceError(
                "invalid-export-format",
                "Export format must be CSV or JSON.",
            ) from None

        view = create_sorted_export_view(page_context.run, page_context.index)
        total_rows = view.row_count
        on_progress(0, total_rows)
        request = ResultExportRequest(
            session_id=page_context.resolver_context.session_id,
            run_id=run_id,
            view_fingerprint=view.view_fingerprint,
            destination=Path(destination),
            format=format_value,
            overwrite=True,
        )
        completed_rows = 0

        def checkpoint(name: str) -> None:
            nonlocal completed_rows
            prefix = "after-export-chunk:"
            if not name.startswith(prefix):
                return
            try:
                chunk_count = int(name[len(prefix):])
            except ValueError:
                return
            completed_rows = min(total_rows, chunk_count * request.chunk_rows)
            on_progress(completed_rows, total_rows)

        try:
            try:
                record = load_result_reproducibility(page_context.run)
            except Exception:
                record = build_result_reproducibility_record(
                    page_context.run,
                    page_context.prepared.request,
                    page_context.resolver_context,
                    _result_data_versions(),
                    _result_execution_evidence(page_context.prepared),
                )
                persist_result_reproducibility(page_context.run, record)
            outcome = export_result_view(
                page_context.run,
                view,
                request,
                page_context.resolver_context,
                record,
                cancelled=should_cancel,
                checkpoint=checkpoint,
            )
        except ResultExportError as error:
            if error.code == "export-cancelled":
                raise OptimizerResultCancelled() from None
            raise OptimizerResultServiceError(error.code, error.message) from error
        except OptimizerResultCancelled:
            raise
        except Exception:
            raise OptimizerResultServiceError(
                "result-export-failed",
                "The active result view could not be exported safely.",
            ) from None

        on_progress(total_rows, total_rows)
        return {
            "format": outcome.format.value,
            "rowCount": str(outcome.row_count),
            "fileBytes": str(outcome.file_bytes),
            "sha256": outcome.sha256,
        }


__all__ = [
    "MAX_DETAIL_ALTERNATIVES",
    "OptimizerResultCancelled",
    "OptimizerResultExecution",
    "OptimizerResultPageContext",
    "OptimizerResultService",
    "OptimizerResultServiceError",
    "result_options",
]
