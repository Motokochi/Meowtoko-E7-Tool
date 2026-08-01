"""Opt-in benchmark for five-million-row result-store interaction."""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import math
import os
import platform
import shutil
import statistics
import sys
import threading
import time
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.path_safety import lexical_absolute_path, path_traverses_linklike  # noqa: E402
from src.optimizer.data import (  # noqa: E402
    ArtifactSelection,
    BUNDLED_ARTIFACT_SOURCE_PATH,
    BUNDLED_CATALOG_FILENAME,
    BUNDLED_CHARACTER_DATA_DIRECTORY,
    BUNDLED_SOURCE_FILENAME,
    DenseInventorySnapshot,
    load_bundled_character_profile_selector,
    load_bundled_skill_context_repository,
    merge_fribbels_inventory,
    parse_fribbels_gear_bytes,
)
from src.optimizer.domain import (  # noqa: E402
    FINAL_STAT_ORDER,
    GEAR_SLOT_ORDER,
    MAX_RESULT_CAP,
    FinalStat,
    GearSet,
    GearSlot,
    HeroModifiers,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SetPattern,
    SkillContext,
    SkillSlot,
    gear_set_fribbels_name,
    gear_slot_fribbels_name,
    item_stat_fribbels_name,
)
from src.optimizer.result_store import (  # noqa: E402
    EQUIPPED_COUNT_SORT_KEY,
    PRIORITY_SCORE_SORT_KEY,
    REQUIRED_DATA_VERSION_CONTRACTS,
    RESULT_COLUMN_NAMES,
    RESULT_PRIMARY_SORT_KEYS,
    RESULT_PRIMARY_STAT_ORDER,
    RESULT_ROW_BYTES,
    DenseItemEquippedLookup,
    FilteredResultView,
    InclusiveInt64Range,
    OriginalResultScope,
    ResultBuildDetailRequest,
    ResultDataVersionEvidence,
    ResultExecutionBackend,
    ResultExecutionEvidence,
    ResultExportFormat,
    ResultExportRequest,
    ResultFilterExecutionStats,
    ResultFilterRequest,
    ResultLifecycleManager,
    ResultLifecycleRequest,
    ResultPageRequest,
    ResultPageRowsRequest,
    ResultResolverContext,
    ResultRunStore,
    ResultSortDirection,
    ResultSortIndexCache,
    ResultSortRequest,
    build_result_reproducibility_record,
    build_result_sort_index,
    create_filtered_export_view,
    export_result_view,
    filter_completed_result_run,
    page_result_sort_index,
    persist_result_reproducibility,
    project_result_export,
    project_result_run_storage,
    project_result_sort_index,
    resolve_result_build_detail,
    resolve_result_page,
    result_columns_from_cpu_rows,
    validate_result_columns,
)
from src.optimizer.search import (  # noqa: E402
    compile_exact_build_context,
    compile_set_pattern,
    create_cartesian_search_space,
    evaluate_exact_build_batch,
    iter_cartesian_batches,
    prepare_search_slot_arrays,
)


BENCHMARK_ID = "e7.optimizer.result-store-interaction"
BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_GATE = "E7_REQUIRE_RESULT_STORE_BENCHMARK"
CAP_ROW_COUNT = 5_000_000
DEFAULT_BATCH_ROWS = 131_072
DEFAULT_EXPORT_ROWS = 100_000
DEFAULT_REPETITIONS = 3
PAGE_SIZE = 1_000
MARKER_NAME = ".e7-result-store-benchmark-v1.json"
RUN_ID = "result-store-benchmark-cap"
SESSION_ID = "session.result-store-benchmark"
_SAFETY_DISK_BYTES = 512 << 20
_SAFETY_RAM_BYTES = 256 << 20
_EXPORT_DISK_BYTES_PER_ROW = 2_048
_T = TypeVar("_T")


class ResultStoreBenchmarkError(ValueError):
    """Actionable benchmark gate, workspace, fixture, or evidence failure."""


def _integer(value: object, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResultStoreBenchmarkError(f"{path} must be an integer.")
    if value < minimum or value > maximum:
        raise ResultStoreBenchmarkError(
            f"{path} must be between {minimum:,} and {maximum:,}; found {value:,}."
        )
    return value


def benchmark_gate_enabled(environment: Mapping[str, str]) -> bool:
    return environment.get(BENCHMARK_GATE, "").strip() == "1"


@dataclass(frozen=True, slots=True)
class ResultStoreBenchmarkConfig:
    workspace: Path
    row_count: int = CAP_ROW_COUNT
    batch_rows: int = DEFAULT_BATCH_ROWS
    export_rows: int = DEFAULT_EXPORT_ROWS
    repetitions: int = DEFAULT_REPETITIONS
    keep_workspace: bool = False
    full_export: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, (str, os.PathLike)):
            raise ResultStoreBenchmarkError("workspace must be an explicit path.")
        workspace = lexical_absolute_path(self.workspace)
        row_count = _integer(self.row_count, "row_count", 1, MAX_RESULT_CAP)
        batch_rows = _integer(self.batch_rows, "batch_rows", 1, 131_072)
        repetitions = _integer(self.repetitions, "repetitions", 1, 20)
        export_rows = _integer(self.export_rows, "export_rows", 1, row_count)
        if not isinstance(self.keep_workspace, bool) or not isinstance(self.full_export, bool):
            raise ResultStoreBenchmarkError("workspace/export switches must be boolean.")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "row_count", row_count)
        object.__setattr__(self, "batch_rows", batch_rows)
        object.__setattr__(self, "export_rows", row_count if self.full_export else export_rows)
        object.__setattr__(self, "repetitions", repetitions)


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _workspace_marker(workspace: Path) -> dict[str, object]:
    return {
        "benchmarkId": BENCHMARK_ID,
        "schemaVersion": BENCHMARK_SCHEMA_VERSION,
        "workspace": str(workspace),
    }


def create_owned_workspace(workspace: Path) -> Path:
    """Claim an explicit empty plain directory with an exact cleanup marker."""

    workspace = lexical_absolute_path(workspace)
    if workspace in {ROOT, ROOT.parent, workspace.anchor and Path(workspace.anchor)}:
        raise ResultStoreBenchmarkError("workspace is too broad for benchmark ownership.")
    parent = workspace.parent
    if not parent.is_dir() or _is_linklike(parent) or path_traverses_linklike(parent):
        raise ResultStoreBenchmarkError("workspace parent must already be a plain directory.")
    if workspace.exists():
        if _is_linklike(workspace) or not workspace.is_dir() or any(workspace.iterdir()):
            raise ResultStoreBenchmarkError("workspace must be absent or an empty plain directory.")
    else:
        workspace.mkdir()
    marker = workspace / MARKER_NAME
    marker.write_bytes(_canonical_json(_workspace_marker(workspace)) + b"\n")
    return workspace


def cleanup_owned_workspace(workspace: Path) -> None:
    """Remove only the exact plain directory claimed by this harness."""

    workspace = lexical_absolute_path(workspace)
    if not workspace.is_dir() or _is_linklike(workspace) or path_traverses_linklike(workspace):
        raise ResultStoreBenchmarkError("cleanup workspace must remain a plain exact directory.")
    marker = workspace / MARKER_NAME
    if _is_linklike(marker) or not marker.is_file():
        raise ResultStoreBenchmarkError("cleanup marker is missing or unsafe.")
    try:
        supplied = json.loads(marker.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ResultStoreBenchmarkError(f"cleanup marker is invalid: {error}") from error
    if supplied != _workspace_marker(workspace):
        raise ResultStoreBenchmarkError("cleanup marker does not identify this exact workspace.")
    shutil.rmtree(workspace)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PSAPI = ctypes.WinDLL("psapi", use_last_error=True)
    _KERNEL32.GetCurrentProcess.restype = ctypes.c_void_p
    _PSAPI.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    _PSAPI.GetProcessMemoryInfo.restype = ctypes.c_int


def system_memory_bytes() -> tuple[int, int]:
    if os.name == "nt":
        status = _MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_physical), int(status.available_physical)
    return 0, 0


def process_rss_bytes() -> int:
    if os.name == "nt":
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = _KERNEL32.GetCurrentProcess()
        if _PSAPI.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return int(counters.working_set_size)
        raise ResultStoreBenchmarkError(
            f"GetProcessMemoryInfo failed with Windows error {ctypes.get_last_error()}."
        )
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if sys.platform == "darwin" else value * 1024
    except (ImportError, ValueError):
        return 0


class _RssSampler:
    def __init__(self, interval_seconds: float = 0.005) -> None:
        self.interval_seconds = interval_seconds
        self.start_bytes = process_rss_bytes()
        self.peak_bytes = self.start_bytes
        self.end_bytes = self.start_bytes
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.peak_bytes = max(self.peak_bytes, process_rss_bytes())

    def __enter__(self) -> _RssSampler:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.end_bytes = process_rss_bytes()
        self.peak_bytes = max(self.peak_bytes, self.end_bytes)
        self._stop.set()
        self._thread.join()


def preflight(config: ResultStoreBenchmarkConfig) -> dict[str, int]:
    projection = project_result_run_storage(RUN_ID, config.row_count)
    worst_sort = max(
        project_result_sort_index(config.row_count, key).declared_peak_array_bytes
        for key in (RESULT_PRIMARY_SORT_KEYS[0], PRIORITY_SCORE_SORT_KEY, EQUIPPED_COUNT_SORT_KEY)
    )
    export_projection = project_result_export(config.export_rows, config.batch_rows)
    required_disk = (
        projection.transaction_peak_bytes
        + config.row_count * 4 * 4
        + max(
            export_projection.peak_numeric_array_bytes,
            config.export_rows * _EXPORT_DISK_BYTES_PER_ROW,
        )
        + _SAFETY_DISK_BYTES
    )
    required_ram = (
        worst_sort
        + config.row_count * 4
        + config.batch_rows * RESULT_ROW_BYTES
        + export_projection.peak_numeric_array_bytes
        + _SAFETY_RAM_BYTES
    )
    disk = shutil.disk_usage(config.workspace.parent)
    total_ram, available_ram = system_memory_bytes()
    if disk.free < required_disk:
        raise ResultStoreBenchmarkError(
            f"preflight needs {required_disk:,} free disk bytes; found {disk.free:,}."
        )
    if available_ram and available_ram < required_ram:
        raise ResultStoreBenchmarkError(
            f"preflight needs {required_ram:,} available RAM bytes; found {available_ram:,}."
        )
    return {
        "requiredDiskBytes": required_disk,
        "freeDiskBytes": disk.free,
        "totalDiskBytes": disk.total,
        "requiredRamBytes": required_ram,
        "availableRamBytes": available_ram,
        "totalRamBytes": total_ram,
    }


_MAIN_STATS = {
    GearSlot.WEAPON: (ItemStatType.FLAT_ATTACK, 500),
    GearSlot.HELMET: (ItemStatType.FLAT_HEALTH, 2500),
    GearSlot.ARMOR: (ItemStatType.FLAT_DEFENSE, 300),
    GearSlot.NECKLACE: (ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT, 65),
    GearSlot.RING: (ItemStatType.EFFECTIVENESS_PERCENT, 65),
    GearSlot.BOOTS: (ItemStatType.SPEED, 45),
}
_SETS = {
    GearSlot.WEAPON: GearSet.SPEED,
    GearSlot.HELMET: GearSet.SPEED,
    GearSlot.ARMOR: GearSet.SPEED,
    GearSlot.NECKLACE: GearSet.SPEED,
    GearSlot.RING: GearSet.HEALTH,
    GearSlot.BOOTS: GearSet.HEALTH,
}


@dataclass(frozen=True, slots=True)
class _Fixture:
    request: OptimizationRequest
    context: ResultResolverContext
    template: dict[str, np.ndarray[Any, Any]]
    attack_axis: int
    base_attack: int


def _synthetic_gear_row(slot: GearSlot) -> dict[str, object]:
    main_stat, main_value = _MAIN_STATS[slot]
    substat = ItemStatType.ATTACK_PERCENT if main_stat is ItemStatType.SPEED else ItemStatType.SPEED
    return {
        "ingameId": f"result-store-benchmark.{slot.name.lower()}",
        "gear": gear_slot_fribbels_name(slot),
        "rank": "Epic",
        "set": gear_set_fribbels_name(_SETS[slot]),
        "enhance": 15,
        "level": 85,
        "main": {
            "type": item_stat_fribbels_name(main_stat),
            "value": main_value,
            "reforgedValue": main_value,
        },
        "substats": [
            {
                "type": item_stat_fribbels_name(substat),
                "value": 8,
                "reforgedValue": 10,
            }
        ],
        "locked": False,
    }


def build_synthetic_fixture(run_id: str = RUN_ID) -> _Fixture:
    """Build one exact row through public import, preparation, and evaluation paths."""

    profile = load_bundled_character_profile_selector().create_default_selection(
        "hero.fribbels.ras"
    )
    request = OptimizationRequest(
        request_id="benchmark.result-store.cap",
        hero_id=profile.hero_id,
        base_profile_id=profile.profile_id,
        modifiers=HeroModifiers(),
        set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
        stat_priorities=((FinalStat.SPEED, 3), (FinalStat.ATTACK, 1)),
        target_defense=1500,
        skill_contexts=tuple(SkillContext(skill, 1500) for skill in SkillSlot),
        result_cap=MAX_RESULT_CAP,
        maximum_replacement_distance=0,
        near_set_tolerance=0,
        item_projection_mode=ItemProjectionMode.CURRENT,
    )
    raw = {"items": [_synthetic_gear_row(slot) for slot in GEAR_SLOT_ORDER]}
    parsed = parse_fribbels_gear_bytes(_canonical_json(raw))
    if parsed.rejections:
        raise ResultStoreBenchmarkError(f"synthetic fixture was rejected: {parsed.rejections!r}")
    inventory = merge_fribbels_inventory((), parsed).items
    arrays = prepare_search_slot_arrays(request, profile, inventory)
    if tuple(len(item.dense_ids) for item in arrays.slots) != (1,) * 6:
        raise ResultStoreBenchmarkError("synthetic fixture must prepare one item per slot.")
    pattern = compile_set_pattern(request.set_pattern)
    skills = load_bundled_skill_context_repository().select(
        request.hero_id, request.skill_contexts
    )
    exact = compile_exact_build_context(
        request, profile, ArtifactSelection(), skills, pattern
    )
    batch = next(iter_cartesian_batches(create_cartesian_search_space(arrays), 1))
    evaluated = evaluate_exact_build_batch(exact, arrays, batch)
    if evaluated.emitted_count != 1:
        raise ResultStoreBenchmarkError(
            f"synthetic fixture must emit one exact result row; found {evaluated.emitted_count}."
        )
    template = result_columns_from_cpu_rows(
        evaluated.rows,
        arrays,
        DenseItemEquippedLookup((False,) * arrays.total_items),
    )

    inventory_by_stable = {item.stable_item_id: item.gear_item for item in inventory}
    snapshot_groups = []
    reverse = []
    dense_id = 0
    for slot in GEAR_SLOT_ORDER:
        stable_id = arrays.stable_item_id_for_dense_id(arrays.slots[GEAR_SLOT_ORDER.index(slot)].dense_ids[0])
        gear = inventory_by_stable[stable_id]
        checked = replace(gear, dense_id=dense_id)
        snapshot_groups.append((slot, (checked,)))
        reverse.append((dense_id, stable_id))
        dense_id += 1
    snapshot = DenseInventorySnapshot(tuple(snapshot_groups), tuple(reverse))
    context = ResultResolverContext(
        session_id=SESSION_ID,
        run_id=run_id,
        selected_hero_id=request.hero_id,
        inventory_snapshot=snapshot,
        slot_arrays=arrays,
        evaluation_context=exact,
        target_pattern=pattern,
    )
    attack_axis = FINAL_STAT_ORDER.index(FinalStat.ATTACK)
    return _Fixture(
        request,
        context,
        template,
        attack_axis,
        int(template["effective_final_stats"][0, attack_axis]),
    )


def build_synthetic_result_batch(
    fixture: _Fixture,
    start_ordinal: int,
    stop_ordinal: int,
) -> dict[str, np.ndarray[Any, Any]]:
    """Vectorize valid deterministic rows without creating per-row objects."""

    if start_ordinal < 0 or stop_ordinal <= start_ordinal or stop_ordinal > MAX_RESULT_CAP:
        raise ResultStoreBenchmarkError("synthetic batch ordinals are invalid.")
    count = stop_ordinal - start_ordinal
    result: dict[str, np.ndarray[Any, Any]] = {}
    for name in RESULT_COLUMN_NAMES:
        source = fixture.template[name]
        values = np.empty((count, *source.shape[1:]), dtype=source.dtype)
        values[...] = source[0]
        result[name] = values
    ordinals = np.arange(start_ordinal, stop_ordinal, dtype="<i8")
    result["effective_final_stats"][:, fixture.attack_axis] = (
        fixture.base_attack + ordinals % 1_000
    )
    result["derived_metrics"][:, 0] = ordinals % 10_000
    result["priority_scores"][:] = ((ordinals % 1_024) / np.float32(1_024)).astype("<f4")
    validate_result_columns(result, row_count=count)
    return result


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_array(values: np.ndarray[Any, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(memoryview(values).cast("B"))
    return digest.hexdigest()


def _data_versions() -> tuple[ResultDataVersionEvidence, ...]:
    paths = {
        "artifact-catalog": BUNDLED_CHARACTER_DATA_DIRECTORY / BUNDLED_ARTIFACT_SOURCE_PATH,
        "character-catalog": BUNDLED_CHARACTER_DATA_DIRECTORY / BUNDLED_CATALOG_FILENAME,
        "skill-context-catalog": BUNDLED_CHARACTER_DATA_DIRECTORY / BUNDLED_SOURCE_FILENAME,
    }
    return tuple(
        ResultDataVersionEvidence(component, schema, version, _hash_file(paths[component]))
        for component, (schema, version) in REQUIRED_DATA_VERSION_CONTRACTS.items()
    )


def _measure(
    operation_id: str,
    condition: str,
    sample_index: int,
    operation: Callable[[], _T],
    *,
    track_python: bool = False,
) -> tuple[_T, dict[str, object]]:
    if track_python:
        tracemalloc.start()
    with _RssSampler() as memory:
        started = time.perf_counter()
        result = operation()
        elapsed = time.perf_counter() - started
    python_peak = 0
    if track_python:
        _current, python_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    sample = {
        "operationId": operation_id,
        "condition": condition,
        "sampleIndex": sample_index,
        "elapsedSeconds": elapsed,
        "startRssBytes": memory.start_bytes,
        "peakRssBytes": memory.peak_bytes,
        "endRssBytes": memory.end_bytes,
        "peakRssDeltaBytes": max(0, memory.peak_bytes - memory.start_bytes),
        "pythonAllocationPeakBytes": python_peak,
    }
    return result, sample


def summarize_samples(samples: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    by_operation: dict[str, list[Mapping[str, object]]] = {}
    for sample in samples:
        operation = str(sample["operationId"])
        by_operation.setdefault(operation, []).append(sample)
    summaries = []
    for operation, values in by_operation.items():
        elapsed = sorted(float(item["elapsedSeconds"]) for item in values)
        repeats = sorted(
            float(item["elapsedSeconds"])
            for item in values
            if item["condition"] == "repeat"
        )
        tail = repeats or elapsed
        p95_index = max(0, math.ceil(0.95 * len(tail)) - 1)
        summaries.append(
            {
                "operationId": operation,
                "sampleCount": len(values),
                "firstSeconds": float(values[0]["elapsedSeconds"]),
                "medianSeconds": statistics.median(elapsed),
                "repeatMedianSeconds": statistics.median(repeats) if repeats else None,
                "repeatP95Seconds": tail[p95_index],
                "maximumPeakRssDeltaBytes": max(
                    int(item["peakRssDeltaBytes"]) for item in values
                ),
                "maximumPythonAllocationPeakBytes": max(
                    int(item["pythonAllocationPeakBytes"]) for item in values
                ),
            }
        )
    return summaries


def _repeat(
    operation_id: str,
    repetitions: int,
    operation: Callable[[], _T],
    samples: list[dict[str, object]],
    *,
    track_python: bool = False,
) -> _T:
    latest: _T
    for index in range(repetitions):
        latest, sample = _measure(
            operation_id,
            "first" if index == 0 else "repeat",
            index,
            operation,
            track_python=track_python,
        )
        samples.append(sample)
    return latest


def _close_memmap(values: np.ndarray[Any, Any]) -> None:
    mapping = getattr(values, "_mmap", None)
    if mapping is not None:
        mapping.close()


def _count_modulo_at_most(row_count: int, maximum: int, modulus: int = 1_000) -> int:
    cycles, remainder = divmod(row_count, modulus)
    return cycles * (maximum + 1) + min(remainder, maximum + 1)


def _filter_request(fixture: _Fixture, maximum_offset: int, *, exact: bool = False) -> ResultFilterRequest:
    ranges = list(ResultFilterRequest().primary_ranges)
    minimum = fixture.base_attack if exact else None
    maximum = fixture.base_attack if exact else fixture.base_attack + maximum_offset
    ranges[fixture.attack_axis] = InclusiveInt64Range(minimum, maximum)
    return ResultFilterRequest(primary_ranges=tuple(ranges))


def _verify_filter(
    view: FilteredResultView,
    row_count: int,
    maximum_offset: int,
    *,
    exact: bool = False,
) -> dict[str, object]:
    expected = (
        _count_modulo_at_most(row_count, 0)
        if exact
        else _count_modulo_at_most(row_count, maximum_offset)
    )
    if view.stats.matched_rows != expected or view.row_ordinals.size != expected:
        raise ResultStoreBenchmarkError(
            f"filter expected {expected:,} ordinals; found {view.row_ordinals.size:,}."
        )
    if view.row_ordinals.size:
        if exact:
            expected_head = np.arange(0, min(row_count, 5_000), 1_000, dtype="<u4")
            if not np.array_equal(view.row_ordinals[: expected_head.size], expected_head):
                raise ResultStoreBenchmarkError("selective filter ordinal order drifted.")
        elif int(view.row_ordinals[0]) != 0:
            raise ResultStoreBenchmarkError("broad filter must start at physical ordinal zero.")
    return {
        "matchedRows": expected,
        "ordinalBytes": int(view.row_ordinals.nbytes),
        "declaredOrdinalCapacityBytes": view.stats.ordinal_capacity_bytes,
        "declaredTemporaryByteUpperBound": view.stats.temporary_byte_upper_bound,
        "ordinalSha256": _hash_array(view.row_ordinals),
        "head": [int(item) for item in view.row_ordinals[:5]],
        "tail": [int(item) for item in view.row_ordinals[-5:]],
    }


def _verify_sort(run: Any, index: Any) -> dict[str, object]:
    key = index.request.sort_key
    column = run.open_column(key.column_name)
    try:
        selected = column if key.axis_index is None else column[:, key.axis_index]
        previous_value: object | None = None
        previous_ordinal = -1
        for start in range(0, index.row_count, 131_072):
            ordinals = np.asarray(index.row_ordinals[start : start + 131_072], dtype="<u4")
            values = np.asarray(selected[ordinals])
            if not values.size:
                continue
            if previous_value is not None:
                first_value = values[0].item()
                first_ordinal = int(ordinals[0])
                if index.request.direction is ResultSortDirection.DESCENDING:
                    boundary_valid = previous_value > first_value or (
                        previous_value == first_value and previous_ordinal < first_ordinal
                    )
                else:
                    boundary_valid = previous_value < first_value or (
                        previous_value == first_value and previous_ordinal < first_ordinal
                    )
                if not boundary_valid:
                    raise ResultStoreBenchmarkError(
                        f"sort order drifted for {key.key_id} at offset {start}."
                    )
            left = values[:-1]
            right = values[1:]
            ties_out_of_order = (left == right) & (ordinals[:-1] >= ordinals[1:])
            if index.request.direction is ResultSortDirection.DESCENDING:
                invalid = (left < right) | ties_out_of_order
            else:
                invalid = (left > right) | ties_out_of_order
            if np.any(invalid):
                raise ResultStoreBenchmarkError(
                    f"sort order drifted for {key.key_id} at offset {start}."
                )
            previous_value = values[-1].item()
            previous_ordinal = int(ordinals[-1])
    finally:
        _close_memmap(column)
    return {
        "sortKey": key.key_id,
        "direction": index.request.direction.value,
        "rowCount": index.row_count,
        "ordinalSha256": _hash_array(index.row_ordinals),
        "head": [int(item) for item in index.row_ordinals[:5]],
        "tail": [int(item) for item in index.row_ordinals[-5:]],
    }


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _filesystem_name(path: Path) -> str:
    if os.name != "nt":
        return "unknown"
    volume = ctypes.create_unicode_buffer(261)
    filesystem = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_ulong()
    maximum = ctypes.c_ulong()
    flags = ctypes.c_ulong()
    root = f"{path.drive}\\"
    success = ctypes.windll.kernel32.GetVolumeInformationW(
        root,
        volume,
        len(volume),
        ctypes.byref(serial),
        ctypes.byref(maximum),
        ctypes.byref(flags),
        filesystem,
        len(filesystem),
    )
    return filesystem.value if success else "unknown"


def environment_evidence(workspace: Path, preflight_evidence: Mapping[str, int]) -> dict[str, object]:
    uname = platform.uname()
    return {
        "system": uname.system,
        "release": uname.release,
        "platform": platform.platform(),
        "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
        "logicalCpuCount": os.cpu_count(),
        "pythonImplementation": platform.python_implementation(),
        "pythonVersion": platform.python_version(),
        "numpyVersion": np.__version__,
        "filesystem": _filesystem_name(workspace),
        **preflight_evidence,
    }


def _write_completed_run(config: ResultStoreBenchmarkConfig, fixture: _Fixture, store: ResultRunStore):
    writer = store.begin_run(RUN_ID, maximum_rows=config.row_count)
    try:
        for start in range(0, config.row_count, config.batch_rows):
            stop = min(start + config.batch_rows, config.row_count)
            writer.append(start, build_synthetic_result_batch(fixture, start, stop))
        return writer.complete(config.row_count)
    except Exception:
        writer.abort("benchmark-failure")
        raise


def _sample_evidence(sample: dict[str, object], evidence: Mapping[str, object]) -> None:
    sample["evidence"] = dict(evidence)


def run_benchmark(config: ResultStoreBenchmarkConfig) -> dict[str, object]:
    """Run measured operations inside an already claimed explicit workspace."""

    if not (config.workspace / MARKER_NAME).is_file():
        raise ResultStoreBenchmarkError("workspace must be claimed before running the benchmark.")
    preflight_evidence = preflight(config)
    fixture = build_synthetic_fixture()
    store_root = config.workspace / "result-store"
    cache_root = config.workspace / "sort-cache"
    export_root = config.workspace / "exports"
    export_root.mkdir()
    store = ResultRunStore(store_root)
    samples: list[dict[str, object]] = []
    validations: dict[str, object] = {}

    run, generation_sample = _measure(
        "generate-transactional-run",
        "first",
        0,
        lambda: _write_completed_run(config, fixture, store),
    )
    _sample_evidence(
        generation_sample,
        {
            "rowCount": run.row_count,
            "payloadBytes": run.payload_bytes,
            "manifestBytes": run.manifest_bytes,
        },
    )
    samples.append(generation_sample)
    if run.row_count != config.row_count or run.payload_bytes != config.row_count * RESULT_ROW_BYTES:
        raise ResultStoreBenchmarkError("completed run size does not match the configured cap.")

    record = build_result_reproducibility_record(
        run,
        fixture.request,
        fixture.context,
        _data_versions(),
        ResultExecutionEvidence(ResultExecutionBackend.CPU, "python-numpy-reference-v1"),
    )
    persist_result_reproducibility(run, record)

    discovered = _repeat(
        "discover-completed-runs",
        config.repetitions,
        store.list_completed_runs,
        samples,
    )
    if tuple(item.run_id for item in discovered) != (RUN_ID,):
        raise ResultStoreBenchmarkError("completed-run discovery evidence drifted.")

    for verify_hashes in (False, True):
        operation_id = "open-run-with-hashes" if verify_hashes else "open-run-without-hashes"
        opened = _repeat(
            operation_id,
            config.repetitions,
            lambda verify_hashes=verify_hashes: store.open_run(RUN_ID, verify_hashes=verify_hashes),
            samples,
        )
        if opened.row_count != config.row_count:
            raise ResultStoreBenchmarkError("opened run row count drifted.")

    baseline_filter = ResultFilterRequest()
    original_scope = OriginalResultScope.create(baseline_filter, fixture.context.target_pattern)
    filter_specs = (
        ("filter-broad-80-percent", 799, False),
        ("filter-selective-one-per-thousand", 0, True),
        ("filter-sort-view-20-percent", 199, False),
    )
    retained_filter: FilteredResultView | None = None
    for operation_id, maximum_offset, exact in filter_specs:
        request = _filter_request(fixture, maximum_offset, exact=exact)
        outcome = _repeat(
            operation_id,
            config.repetitions,
            lambda request=request: filter_completed_result_run(
                run, request, original_scope, chunk_rows=config.batch_rows
            ),
            samples,
        )
        if outcome.view is None:
            raise ResultStoreBenchmarkError("tightening filter unexpectedly requires a rerun.")
        evidence = _verify_filter(
            outcome.view, config.row_count, maximum_offset, exact=exact
        )
        validations[operation_id] = evidence
        if operation_id == "filter-sort-view-20-percent":
            retained_filter = outcome.view
    assert retained_filter is not None
    gc.collect()

    cache = ResultSortIndexCache(
        cache_root,
        maximum_bytes=512 << 20,
        maximum_entries=8,
    )
    sort_specs = (
        ("sort-base-int64", ResultSortRequest(sort_key=RESULT_PRIMARY_SORT_KEYS[0]), None),
        ("sort-base-binary32", ResultSortRequest(sort_key=PRIORITY_SCORE_SORT_KEY), None),
        ("sort-base-byte", ResultSortRequest(sort_key=EQUIPPED_COUNT_SORT_KEY), None),
        (
            "sort-filtered-int64",
            ResultSortRequest(sort_key=RESULT_PRIMARY_SORT_KEYS[0]),
            retained_filter,
        ),
        (
            "sort-filtered-binary32",
            ResultSortRequest(sort_key=PRIORITY_SCORE_SORT_KEY),
            retained_filter,
        ),
        (
            "sort-filtered-byte",
            ResultSortRequest(sort_key=EQUIPPED_COUNT_SORT_KEY),
            retained_filter,
        ),
    )
    page_index = None
    cache_keys: list[str] = []
    for operation_id, request, view in sort_specs:
        latest = None
        for repetition in range(config.repetitions):
            if latest is not None and latest is not page_index:
                _close_memmap(latest.row_ordinals)
            latest, sample = _measure(
                operation_id,
                "first" if repetition == 0 else "repeat",
                repetition,
                lambda request=request, view=view: build_result_sort_index(
                    run,
                    request,
                    view=view,
                    cache=cache,
                    maximum_build_array_bytes=512 << 20,
                ),
            )
            _sample_evidence(
                sample,
                {
                    "cacheHit": latest.stats.cache_hit,
                    "cachePublished": latest.stats.cache_published,
                    "rowCount": latest.row_count,
                    "declaredPeakArrayBytes": latest.stats.projection.declared_peak_array_bytes,
                    "cacheKey": latest.cache_key,
                },
            )
            samples.append(sample)
        assert latest is not None
        validations[operation_id] = _verify_sort(run, latest)
        cache_keys.append(latest.cache_key)
        if operation_id == "sort-base-int64":
            page_index = latest
        else:
            _close_memmap(latest.row_ordinals)
    assert page_index is not None

    page_count = math.ceil(config.row_count / PAGE_SIZE)
    page_locations = {
        "first": 0,
        "middle": page_count // 2,
        "last": page_count - 1,
    }
    pages = {}
    for location, number in page_locations.items():
        page = _repeat(
            f"page-{location}-1000",
            config.repetitions,
            lambda number=number: page_result_sort_index(
                page_index, ResultPageRequest(page_index=number, page_size=PAGE_SIZE)
            ),
            samples,
        )
        expected_rows = min(PAGE_SIZE, config.row_count - number * PAGE_SIZE)
        if page.returned_rows != expected_rows:
            raise ResultStoreBenchmarkError(f"{location} page row count drifted.")
        pages[location] = page
        validations[f"page-{location}"] = {
            "pageIndex": number,
            "returnedRows": page.returned_rows,
            "head": [int(item) for item in page.row_ordinals[:5]],
            "tail": [int(item) for item in page.row_ordinals[-5:]],
        }

    for location, page in pages.items():
        request = ResultPageRowsRequest(
            SESSION_ID, RUN_ID, page_index.cache_key, page
        )
        resolved = _repeat(
            f"resolve-page-{location}-1000",
            config.repetitions,
            lambda request=request: resolve_result_page(
                run, page_index, request, fixture.context
            ),
            samples,
            track_python=True,
        )
        expected = tuple(int(item) for item in page.row_ordinals)
        if tuple(item.row_ordinal for item in resolved.rows) != expected:
            raise ResultStoreBenchmarkError(f"resolved {location} page order drifted.")

    detail_page = pages["middle"]
    selected_ordinal = int(detail_page.row_ordinals[len(detail_page.row_ordinals) // 2])
    detail_request = ResultBuildDetailRequest(
        SESSION_ID,
        RUN_ID,
        page_index.cache_key,
        detail_page,
        selected_ordinal,
    )
    detail = _repeat(
        "resolve-exact-detail",
        config.repetitions,
        lambda: resolve_result_build_detail(
            run, page_index, detail_request, fixture.context
        ),
        samples,
        track_python=True,
    )
    if detail.row.row_ordinal != selected_ordinal:
        raise ResultStoreBenchmarkError("exact detail evidence drifted.")
    validations["resolve-exact-detail"] = {
        "rowOrdinal": selected_ordinal,
        "category": detail.row.category.value,
        "replacementExplanation": False,
    }

    lifecycle = ResultLifecycleManager(
        store_root, cache_root, export_roots=(export_root,)
    )
    lifecycle_request = ResultLifecycleRequest(
        datetime.now(UTC),
        active_run_ids=(RUN_ID,),
        active_index_cache_keys=tuple(cache_keys),
    )
    lifecycle_report = _repeat(
        "lifecycle-dry-run-scan",
        config.repetitions,
        lambda: lifecycle.clean(lifecycle_request),
        samples,
    )
    validations["lifecycle-dry-run-scan"] = {
        "scannedArtifacts": lifecycle_report.scanned_artifacts,
        "eligibleArtifacts": lifecycle_report.eligible_artifacts,
        "removedArtifacts": lifecycle_report.removed_artifacts,
        "preservedUnknownArtifacts": lifecycle_report.preserved_unknown_artifacts,
    }
    if lifecycle_report.removed_artifacts:
        raise ResultStoreBenchmarkError("dry-run lifecycle scan removed an artifact.")

    if config.full_export:
        export_count = config.row_count
        export_ordinals = np.arange(export_count, dtype="<u4")
    else:
        export_count = min(config.export_rows, retained_filter.row_ordinals.size)
        export_ordinals = np.asarray(
            retained_filter.row_ordinals[:export_count], dtype="<u4"
        ).copy()
    export_ordinals.flags.writeable = False
    export_filter = FilteredResultView(
        export_ordinals,
        ResultFilterExecutionStats(
            config.row_count,
            export_count,
            config.batch_rows,
            min(config.batch_rows, config.row_count),
            config.row_count * 4,
            min(config.batch_rows, config.row_count) * 128,
        ),
    )
    export_view = create_filtered_export_view(run, export_filter)
    export_evidence: dict[str, object] = {}
    peak_benchmark_disk_bytes = _directory_bytes(config.workspace)
    for export_format in (ResultExportFormat.CSV, ResultExportFormat.JSON):
        operation_id = f"export-{export_format.value}-{export_count}-rows"
        outcomes = []
        for repetition in range(config.repetitions):
            destination = export_root / f"sample-{export_format.value}-{repetition}.{export_format.value}"
            request = ResultExportRequest(
                SESSION_ID,
                RUN_ID,
                export_view.view_fingerprint,
                destination,
                export_format,
                chunk_rows=config.batch_rows,
            )
            try:
                outcome, sample = _measure(
                    operation_id,
                    "first" if repetition == 0 else "repeat",
                    repetition,
                    lambda request=request: export_result_view(
                        run, export_view, request, fixture.context, record
                    ),
                )
                _sample_evidence(
                    sample,
                    {
                        "rowCount": outcome.row_count,
                        "fileBytes": outcome.file_bytes,
                        "sha256": outcome.sha256,
                        "chunkCount": outcome.chunk_count,
                        "rowsPerSecond": outcome.row_count / float(sample["elapsedSeconds"]),
                    },
                )
                samples.append(sample)
                outcomes.append(outcome)
                peak_benchmark_disk_bytes = max(
                    peak_benchmark_disk_bytes,
                    _directory_bytes(config.workspace),
                )
            finally:
                if destination.exists() and not _is_linklike(destination):
                    destination.unlink()
        if len({item.sha256 for item in outcomes}) != 1:
            raise ResultStoreBenchmarkError(f"{export_format.value} export digest drifted.")
        export_evidence[export_format.value] = {
            "measuredRows": export_count,
            "fileBytes": outcomes[-1].file_bytes,
            "sha256": outcomes[-1].sha256,
            "fullCapBytesProjected": round(outcomes[-1].file_bytes * config.row_count / export_count),
            "fullCapTimingIsProjected": export_count != config.row_count,
        }
    validations["exports"] = export_evidence

    summaries = summarize_samples(samples)
    summary_by_id = {item["operationId"]: item for item in summaries}
    for export_format in ("csv", "json"):
        operation_id = f"export-{export_format}-{export_count}-rows"
        measured = summary_by_id[operation_id]
        rate = export_count / float(measured["medianSeconds"])
        export_evidence[export_format]["medianRowsPerSecond"] = rate  # type: ignore[index]
        export_evidence[export_format]["fullCapSecondsProjected"] = config.row_count / rate  # type: ignore[index]

    result_store_bytes = _directory_bytes(store_root)
    cache_bytes = _directory_bytes(cache_root)
    peak_process_rss = max(int(item["peakRssBytes"]) for item in samples)
    maximum_operation_rss_delta = max(
        int(item["peakRssDeltaBytes"]) for item in samples
    )
    document = {
        "benchmarkId": BENCHMARK_ID,
        "schemaVersion": BENCHMARK_SCHEMA_VERSION,
        "recordType": "result-store-interaction",
        "measuredUtc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "environment": environment_evidence(config.workspace, preflight_evidence),
        "configuration": {
            "rowCount": config.row_count,
            "batchRows": config.batch_rows,
            "pageSize": PAGE_SIZE,
            "exportRows": export_count,
            "repetitions": config.repetitions,
            "fullExportMeasured": config.full_export,
            "workspaceRetained": config.keep_workspace,
        },
        "storage": {
            "resultStoreBytes": result_store_bytes,
            "payloadBytes": run.payload_bytes,
            "manifestBytes": run.manifest_bytes,
            "reproducibilityBytes": (run.path / "reproducibility-v1.json").stat().st_size,
            "sortCacheBytes": cache_bytes,
            "exportBytesRetained": 0,
            "totalBenchmarkBytesBeforeCleanup": _directory_bytes(config.workspace),
            "peakBenchmarkOwnedDiskBytes": peak_benchmark_disk_bytes,
        },
        "memory": {
            "peakProcessRssBytes": peak_process_rss,
            "maximumOperationRssDeltaBytes": maximum_operation_rss_delta,
            "rssIncludesMappedAndFileCachePages": True,
            "numpyBuffersAreNotPythonAllocations": True,
        },
        "fixture": {
            "source": "in-memory Fribbels-shaped six-item exact Speed/Health build",
            "rowPattern": {
                "attack": "baseAttack + rowOrdinal % 1000",
                "firstDerivedMetric": "rowOrdinal % 10000",
                "priorityScore": "float32((rowOrdinal % 1024) / 1024)",
            },
            "baseAttack": fixture.base_attack,
            "perRowPythonObjectsCreatedDuringGeneration": 0,
        },
        "samples": samples,
        "summaries": summaries,
        "validations": validations,
        "recommendations": {
            "filterDebounceMs": 300,
            "progressCadenceMs": 100,
            "pageSize": PAGE_SIZE,
            "exportChunkRows": config.batch_rows,
            "cancelSupersededFilters": True,
            "explicitBusyOperations": [
                "hash verification",
                "uncached filter",
                "uncached sort",
                "streaming export",
            ],
            "sortCacheHitsMayUseInlineProgress": True,
            "resolveOnlyVisiblePage": True,
        },
    }
    validate_benchmark_document(document, require_cap=config.row_count == CAP_ROW_COUNT)
    _close_memmap(page_index.row_ordinals)
    return document


def validate_benchmark_document(document: object, *, require_cap: bool = True) -> None:
    if not isinstance(document, dict):
        raise ResultStoreBenchmarkError("benchmark baseline must be a JSON object.")
    if document.get("benchmarkId") != BENCHMARK_ID or document.get("schemaVersion") != 1:
        raise ResultStoreBenchmarkError("benchmark baseline identity/version is invalid.")
    configuration = document.get("configuration")
    if not isinstance(configuration, dict):
        raise ResultStoreBenchmarkError("benchmark configuration evidence is missing.")
    row_count = configuration.get("rowCount")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 1:
        raise ResultStoreBenchmarkError("benchmark row count is invalid.")
    if require_cap and row_count != CAP_ROW_COUNT:
        raise ResultStoreBenchmarkError("checked acceptance baseline must measure 5,000,000 rows.")
    samples = document.get("samples")
    summaries = document.get("summaries")
    if not isinstance(samples, list) or not samples or not isinstance(summaries, list):
        raise ResultStoreBenchmarkError("benchmark samples/summaries are missing.")
    required = {
        "open-run-without-hashes",
        "open-run-with-hashes",
        "discover-completed-runs",
        "filter-broad-80-percent",
        "filter-selective-one-per-thousand",
        "sort-base-int64",
        "sort-base-binary32",
        "sort-base-byte",
        "sort-filtered-int64",
        "page-first-1000",
        "page-middle-1000",
        "page-last-1000",
        "resolve-page-first-1000",
        "resolve-exact-detail",
        "lifecycle-dry-run-scan",
    }
    operation_ids = {item.get("operationId") for item in summaries if isinstance(item, dict)}
    if not required <= operation_ids:
        raise ResultStoreBenchmarkError(
            f"benchmark is missing operations: {tuple(sorted(required - operation_ids))!r}."
        )
    storage = document.get("storage")
    valid_payload_bytes = {
        row_count * RESULT_ROW_BYTES,
        row_count * 225,  # Historical v1 result schema baseline.
    }
    if not isinstance(storage, dict) or storage.get("payloadBytes") not in valid_payload_bytes:
        raise ResultStoreBenchmarkError("benchmark payload evidence is invalid.")
    if (
        not isinstance(storage.get("peakBenchmarkOwnedDiskBytes"), int)
        or storage["peakBenchmarkOwnedDiskBytes"] < storage.get("totalBenchmarkBytesBeforeCleanup", 0)
    ):
        raise ResultStoreBenchmarkError("benchmark peak disk evidence is invalid.")
    memory = document.get("memory")
    if (
        not isinstance(memory, dict)
        or not isinstance(memory.get("peakProcessRssBytes"), int)
        or memory["peakProcessRssBytes"] <= 0
    ):
        raise ResultStoreBenchmarkError("benchmark peak process memory evidence is invalid.")
    fixture = document.get("fixture")
    if not isinstance(fixture, dict) or fixture.get("perRowPythonObjectsCreatedDuringGeneration") != 0:
        raise ResultStoreBenchmarkError("benchmark object-generation evidence is invalid.")


def render_markdown_report(document: Mapping[str, object]) -> str:
    validate_benchmark_document(dict(document), require_cap=document["configuration"]["rowCount"] == CAP_ROW_COUNT)  # type: ignore[index]
    environment = document["environment"]  # type: ignore[assignment]
    configuration = document["configuration"]  # type: ignore[assignment]
    storage = document["storage"]  # type: ignore[assignment]
    memory = document["memory"]  # type: ignore[assignment]
    summaries = document["summaries"]  # type: ignore[assignment]
    validations = document["validations"]  # type: ignore[assignment]
    recommendations = document["recommendations"]  # type: ignore[assignment]
    full_export = bool(configuration["fullExportMeasured"])
    workspace_name = "result-store-full-export" if full_export else "result-store-benchmark"
    output_name = (
        "result-store-5m-full-export-p09.json"
        if full_export
        else "result-store-5m-baseline.json"
    )
    report_name = (
        "RESULT_STORE_5M_FULL_EXPORT_P09.md"
        if full_export
        else "RESULT_STORE_5M_REPORT.md"
    )
    full_export_args = " --full-export --repetitions 1" if full_export else ""
    lines = [
        "# Five-million-row result-store benchmark",
        "",
        f"Measured `{document['measuredUtc']}` on {environment['system']} "
        f"{environment['release']} with {environment['processor']}, "
        f"{environment['totalRamBytes'] / (1 << 30):.1f} GiB RAM, "
        f"Python {environment['pythonVersion']}, NumPy {environment['numpyVersion']}, and "
        f"{environment['filesystem']}.",
        "",
        "The harness generated the completed run through the transactional v1 writer in bounded "
        "NumPy batches. It created no cap-scale Python row-object graph. First means the first "
        "invocation in the measured process; repeat measurements may benefit from OS file cache "
        "and the explicit sort-index cache. They are not claimed as powered-off cold-cache results.",
        "",
        "## Reproduce",
        "",
        "```powershell",
        "$env:E7_REQUIRE_RESULT_STORE_BENCHMARK='1'",
        f"python scripts/benchmark_result_store.py --workspace .build/benchmarks/{workspace_name}"
        f"{full_export_args} --output benchmarks/{output_name} "
        f"--report benchmarks/{report_name}",
        "```",
        "",
        "The workspace must be absent or empty and is removed after success unless "
        "`--keep-workspace` is passed. Ordinary imports and tests do not run the cap benchmark.",
        "",
        "## Target and storage",
        "",
        f"- Rows: **{configuration['rowCount']:,}**; page size: **{configuration['pageSize']:,}**; "
        f"bounded export view: **{configuration['exportRows']:,}** rows.",
        f"- Raw result payload: **{storage['payloadBytes'] / (1 << 30):.3f} GiB**; "
        f"result directory: **{storage['resultStoreBytes'] / (1 << 30):.3f} GiB**; "
        f"sort cache: **{storage['sortCacheBytes'] / (1 << 20):.1f} MiB**.",
        f"- Peak benchmark-owned disk (including bounded export): "
        f"**{storage['peakBenchmarkOwnedDiskBytes'] / (1 << 30):.3f} GiB**; "
        f"retained before cleanup: **{storage['totalBenchmarkBytesBeforeCleanup'] / (1 << 30):.3f} GiB**.",
        f"- Peak process working set: **{memory['peakProcessRssBytes'] / (1 << 20):.1f} MiB**; "
        f"largest operation-local rise: **{memory['maximumOperationRssDeltaBytes'] / (1 << 20):.1f} MiB**.",
        "",
        "## Latency and memory",
        "",
        "| Operation | First (s) | Repeat median (s) | p95 (s) | Peak RSS delta (MiB) | Python peak (MiB) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        repeat = item["repeatMedianSeconds"]
        lines.append(
            f"| `{item['operationId']}` | {item['firstSeconds']:.6f} | "
            f"{repeat if repeat is not None else item['medianSeconds']:.6f} | "
            f"{item['repeatP95Seconds']:.6f} | "
            f"{item['maximumPeakRssDeltaBytes'] / (1 << 20):.1f} | "
            f"{item['maximumPythonAllocationPeakBytes'] / (1 << 20):.1f} |"
        )
    exports = validations["exports"]
    lines.extend(
        [
            "",
            "RSS includes mapped/file-cache pages charged to this process; the separate "
            "`tracemalloc` column is shown for page/detail object allocation and does not count "
            "NumPy native buffers. Sort projections and filter ordinal capacities remain the "
            "authoritative declared array ceilings.",
            "",
            "## Export evidence",
            "",
        ]
    )
    for name in ("csv", "json"):
        item = exports[name]
        qualifier = "projected" if item["fullCapTimingIsProjected"] else "measured"
        lines.append(
            f"- {name.upper()}: {item['measuredRows']:,} measured rows, "
            f"{item['medianRowsPerSecond']:,.0f} rows/s, {item['fileBytes']:,} bytes. "
            f"A full-cap export is **{qualifier}** at {item['fullCapSecondsProjected']:.1f} s "
            f"and {item['fullCapBytesProjected']:,} bytes."
        )
    lines.extend(
        [
            "",
            "## Practical P08 requirements",
            "",
            f"- Debounce result-filter edits by **{recommendations['filterDebounceMs']} ms**, "
            "cancel superseded work, and never run a second full scan for each keystroke.",
            "- Run uncached filters/sorts, hash verification, and exports off the renderer thread "
            "with an explicit busy state. Cache-hit sorts may use a lighter inline progress state.",
            f"- Publish progress at most every **{recommendations['progressCadenceMs']} ms** and "
            "check cancellation at filter/export chunk boundaries. A future cancellable sort "
            "boundary should be added before exposing repeated sort changes.",
            f"- Keep result pages at **{recommendations['pageSize']:,} rows** and resolve only the "
            "visible page. Use a skeleton while its bounded row objects are created.",
            f"- Stream exports in **{recommendations['exportChunkRows']:,}-row** chunks, show rows "
            "written plus output bytes, and preserve atomic completion/no partial file semantics.",
            "- Surface the five-million match guard before result interaction; the benchmark does "
            "not justify retaining more than the locked cap.",
            "",
            "The benchmark measured an exact selected-row detail. A near-detail reconstruction was "
            "not valid for this intentionally exact synthetic fixture and is therefore not claimed.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--rows", type=int, default=CAP_ROW_COUNT)
    parser.add_argument("--batch-rows", type=int, default=DEFAULT_BATCH_ROWS)
    parser.add_argument("--export-rows", type=int, default=DEFAULT_EXPORT_ROWS)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--full-export", action="store_true")
    parser.add_argument("--keep-workspace", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    if not benchmark_gate_enabled(os.environ):
        print(
            f"Result-store benchmark refused: set {BENCHMARK_GATE}=1 explicitly.",
            file=sys.stderr,
        )
        return 2
    config = ResultStoreBenchmarkConfig(
        workspace=arguments.workspace,
        row_count=arguments.rows,
        batch_rows=arguments.batch_rows,
        export_rows=arguments.export_rows,
        repetitions=arguments.repetitions,
        keep_workspace=arguments.keep_workspace,
        full_export=arguments.full_export,
    )
    workspace_created = False
    try:
        create_owned_workspace(config.workspace)
        workspace_created = True
        document = run_benchmark(config)
        serialized = _canonical_json(document) + b"\n"
        if arguments.output is not None:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_bytes(serialized)
        else:
            sys.stdout.buffer.write(serialized)
        if arguments.report is not None:
            arguments.report.parent.mkdir(parents=True, exist_ok=True)
            arguments.report.write_text(render_markdown_report(document), encoding="utf-8")
        return 0
    except (OSError, ResultStoreBenchmarkError, ValueError) as error:
        print(f"Result-store benchmark error: {error}", file=sys.stderr)
        return 1
    finally:
        if workspace_created and not config.keep_workspace and config.workspace.exists():
            cleanup_owned_workspace(config.workspace)


if __name__ == "__main__":
    raise SystemExit(main())
