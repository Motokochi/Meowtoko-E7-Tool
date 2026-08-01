"""Bounded, deterministic CSV/JSON export for immutable result views."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import struct
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from numbers import Integral
from pathlib import Path
from typing import Any, Callable, TextIO

import numpy as np

from src.core.path_safety import lexical_absolute_path, path_traverses_linklike
from src.optimizer.domain import FRIBBELS_SET_ORDER, GEAR_SLOT_ORDER
from src.optimizer.result_store.filtering import FilteredResultView
from src.optimizer.result_store.indexing import CompletedResultSortIndex, result_run_fingerprint
from src.optimizer.result_store.lifecycle import (
    ResultReproducibilityRecord,
    validate_result_reproducibility_context,
)
from src.optimizer.result_store.resolution import ResultResolverContext
from src.optimizer.result_store.schema import (
    RESULT_COLUMN_NAMES,
    RESULT_DERIVED_METRIC_ORDER,
    RESULT_PRIMARY_STAT_ORDER,
    RESULT_ROW_BYTES,
    RESULT_SCHEMA,
    ResultSchemaError,
    decode_result_category,
    validate_result_columns,
)
from src.optimizer.result_store.storage import CompletedResultRun


RESULT_EXPORT_ID = "e7.optimizer.result-export"
RESULT_EXPORT_VERSION = 1
DEFAULT_EXPORT_CHUNK_ROWS = 8_192
MAX_EXPORT_CHUNK_ROWS = 131_072

_U4 = np.dtype("<u4")
_FILTERED_FINGERPRINT = re.compile(r"filtered:[0-9]+:[0-9a-f]{64}\Z")
_SORTED_FINGERPRINT = re.compile(r"sorted:[0-9a-f]{64}\Z")

RESULT_EXPORT_FIELD_NAMES: tuple[str, ...] = (
    "rowOrdinal",
    *(f"item:{slot.value}" for slot in GEAR_SLOT_ORDER),
    *(f"set:{slot.value}" for slot in GEAR_SLOT_ORDER),
    "category",
    "replacementCount",
    *(f"primary:{stat.value}" for stat in RESULT_PRIMARY_STAT_ORDER),
    *(f"derived:{metric_id}" for metric_id in RESULT_DERIVED_METRIC_ORDER),
    "priorityScore",
    "priorityScoreBits",
    "constraintDistance",
    "constraintDistanceBits",
    "equippedItemCount",
)


class ResultExportError(ValueError):
    """Actionable view, provenance, cancellation, or publication failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> ResultExportError:
    return ResultExportError(code, path, message)


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _error("invalid-text", path, "must be non-empty canonical text.")
    return value


def _integer(value: object, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _error("invalid-integer", path, "must be an integer; booleans are not accepted.")
    normalized = int(value)
    if normalized < minimum or normalized > maximum:
        raise _error("integer-out-of-range", path, f"must be between {minimum} and {maximum}.")
    return normalized


def _hash_array(values: np.ndarray[Any, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(memoryview(values).cast("B"))
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while payload := file.read(1 << 20):
            digest.update(payload)
    return digest.hexdigest()


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _binary32_bits(value: object) -> str:
    return f"{struct.unpack('<I', struct.pack('<f', float(value)))[0]:08x}"


class ResultExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"


class ResultExportViewKind(StrEnum):
    BASE = "base"
    FILTERED = "filtered"
    SORTED = "sorted"


@dataclass(frozen=True, slots=True)
class ResultExportView:
    kind: ResultExportViewKind
    run_fingerprint: str
    view_fingerprint: str
    source_view_fingerprint: str
    row_count: int
    row_ordinals: np.ndarray[Any, np.dtype[np.uint32]] | None

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, ResultExportViewKind) else ResultExportViewKind(self.kind)
        except (TypeError, ValueError):
            raise _error("invalid-export-view-kind", "ResultExportView.kind", "must be base, filtered, or sorted.") from None
        run_fingerprint = _text(self.run_fingerprint, "ResultExportView.run_fingerprint")
        view_fingerprint = _text(self.view_fingerprint, "ResultExportView.view_fingerprint")
        source_fingerprint = _text(self.source_view_fingerprint, "ResultExportView.source_view_fingerprint")
        row_count = _integer(self.row_count, "ResultExportView.row_count", 0, RESULT_SCHEMA.maximum_rows)
        values = self.row_ordinals
        if kind is ResultExportViewKind.BASE:
            if values is not None or view_fingerprint != f"base:{run_fingerprint}" or source_fingerprint != view_fingerprint:
                raise _error("invalid-base-export-view", "ResultExportView", "base views use their run fingerprint and no ordinal array.")
        else:
            if not isinstance(values, np.ndarray) or values.dtype.str != _U4.str or values.ndim != 1:
                raise _error("invalid-export-ordinals", "ResultExportView.row_ordinals", "must be a one-dimensional uint32 array.")
            if int(values.size) != row_count:
                raise _error("export-view-size", "ResultExportView.row_ordinals", "must match row_count.")
            values.flags.writeable = False
            if kind is ResultExportViewKind.FILTERED:
                if not _FILTERED_FINGERPRINT.fullmatch(view_fingerprint) or source_fingerprint != view_fingerprint:
                    raise _error("invalid-filtered-fingerprint", "ResultExportView.view_fingerprint", "must identify the filtered ordinal sequence.")
            elif not _SORTED_FINGERPRINT.fullmatch(view_fingerprint):
                raise _error("invalid-sorted-fingerprint", "ResultExportView.view_fingerprint", "must identify the completed sort index.")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "run_fingerprint", run_fingerprint)
        object.__setattr__(self, "view_fingerprint", view_fingerprint)
        object.__setattr__(self, "source_view_fingerprint", source_fingerprint)
        object.__setattr__(self, "row_count", row_count)


def create_base_export_view(run: CompletedResultRun) -> ResultExportView:
    if not isinstance(run, CompletedResultRun):
        raise _error("invalid-completed-run", "run", "must be CompletedResultRun.")
    fingerprint = result_run_fingerprint(run)
    return ResultExportView(ResultExportViewKind.BASE, fingerprint, f"base:{fingerprint}", f"base:{fingerprint}", run.row_count, None)


def create_filtered_export_view(run: CompletedResultRun, view: FilteredResultView) -> ResultExportView:
    if not isinstance(run, CompletedResultRun) or not isinstance(view, FilteredResultView):
        raise _error("invalid-filtered-view", "view", "requires a completed run and FilteredResultView.")
    values = view.row_ordinals
    if values.size and int(values[-1]) >= run.row_count:
        raise _error("filtered-ordinal-out-of-range", "view.row_ordinals", "references a row outside the completed run.")
    fingerprint = result_run_fingerprint(run)
    view_fingerprint = f"filtered:{values.size}:{_hash_array(values)}"
    return ResultExportView(ResultExportViewKind.FILTERED, fingerprint, view_fingerprint, view_fingerprint, int(values.size), values)


def create_sorted_export_view(run: CompletedResultRun, index: CompletedResultSortIndex) -> ResultExportView:
    if not isinstance(run, CompletedResultRun) or not isinstance(index, CompletedResultSortIndex):
        raise _error("invalid-sorted-view", "index", "requires a completed run and CompletedResultSortIndex.")
    fingerprint = result_run_fingerprint(run)
    if index.run_fingerprint != fingerprint:
        raise _error("stale-sort-index", "index.run_fingerprint", "does not identify the completed run.")
    if index.row_ordinals.size and int(np.max(index.row_ordinals)) >= run.row_count:
        raise _error("sorted-ordinal-out-of-range", "index.row_ordinals", "references a row outside the completed run.")
    if index.view_fingerprint != f"base:{fingerprint}" and not _FILTERED_FINGERPRINT.fullmatch(index.view_fingerprint):
        raise _error("invalid-sort-source-view", "index.view_fingerprint", "must identify a base or filtered source view.")
    return ResultExportView(
        ResultExportViewKind.SORTED,
        fingerprint,
        f"sorted:{index.cache_key}",
        index.view_fingerprint,
        index.row_count,
        index.row_ordinals,
    )


@dataclass(frozen=True, slots=True)
class ResultExportRequest:
    session_id: str
    run_id: str
    view_fingerprint: str
    destination: Path
    format: ResultExportFormat
    chunk_rows: int = DEFAULT_EXPORT_CHUNK_ROWS
    overwrite: bool = False
    operation_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    export_id: str = RESULT_EXPORT_ID
    version: int = RESULT_EXPORT_VERSION

    def __post_init__(self) -> None:
        if self.export_id != RESULT_EXPORT_ID or self.version != RESULT_EXPORT_VERSION:
            raise _error("export-version", "ResultExportRequest", "uses an unsupported identity or version.")
        session_id = _text(self.session_id, "ResultExportRequest.session_id")
        run_id = _text(self.run_id, "ResultExportRequest.run_id")
        view_fingerprint = _text(self.view_fingerprint, "ResultExportRequest.view_fingerprint")
        if not isinstance(self.destination, (str, os.PathLike)):
            raise _error("invalid-export-destination", "ResultExportRequest.destination", "must be an explicit filesystem path.")
        destination = lexical_absolute_path(self.destination)
        try:
            export_format = self.format if isinstance(self.format, ResultExportFormat) else ResultExportFormat(self.format)
        except (TypeError, ValueError):
            raise _error("invalid-export-format", "ResultExportRequest.format", "must be csv or json.") from None
        if destination.suffix.casefold() != f".{export_format.value}":
            raise _error("export-extension", "ResultExportRequest.destination", f"must end in .{export_format.value}.")
        chunk_rows = _integer(self.chunk_rows, "ResultExportRequest.chunk_rows", 1, MAX_EXPORT_CHUNK_ROWS)
        if not isinstance(self.overwrite, bool):
            raise _error("invalid-overwrite", "ResultExportRequest.overwrite", "must be boolean.")
        if not isinstance(self.operation_token, str) or not re.fullmatch(r"[0-9a-f]{32}", self.operation_token):
            raise _error("invalid-operation-token", "ResultExportRequest.operation_token", "must be 32 lowercase hexadecimal characters.")
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "view_fingerprint", view_fingerprint)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "format", export_format)
        object.__setattr__(self, "chunk_rows", chunk_rows)

    @property
    def temporary_path(self) -> Path:
        return self.destination.parent / f".{self.destination.name}.{self.operation_token}.e7-export.tmp"


@dataclass(frozen=True, slots=True)
class ResultExportProjection:
    row_count: int
    chunk_rows: int
    peak_chunk_rows: int
    stored_column_bytes_per_row: int
    ordinal_bytes_per_row: int
    peak_numeric_array_bytes: int
    peak_serialized_rows: int


def project_result_export(row_count: object, chunk_rows: object = DEFAULT_EXPORT_CHUNK_ROWS) -> ResultExportProjection:
    count = _integer(row_count, "row_count", 0, RESULT_SCHEMA.maximum_rows)
    chunk = _integer(chunk_rows, "chunk_rows", 1, MAX_EXPORT_CHUNK_ROWS)
    peak = min(count, chunk)
    return ResultExportProjection(count, chunk, peak, RESULT_ROW_BYTES, _U4.itemsize, peak * (RESULT_ROW_BYTES + _U4.itemsize), 1 if count else 0)


@dataclass(frozen=True, slots=True)
class ResultExportOutcome:
    destination: Path
    format: ResultExportFormat
    view_fingerprint: str
    row_count: int
    chunk_count: int
    file_bytes: int
    sha256: str
    projection: ResultExportProjection


def _validate_export_authority(
    run: CompletedResultRun,
    view: ResultExportView,
    request: ResultExportRequest,
    context: ResultResolverContext,
    reproducibility: ResultReproducibilityRecord,
) -> None:
    if not all(
        (
            isinstance(run, CompletedResultRun),
            isinstance(view, ResultExportView),
            isinstance(request, ResultExportRequest),
            isinstance(context, ResultResolverContext),
            isinstance(reproducibility, ResultReproducibilityRecord),
        )
    ):
        raise _error("invalid-export-context", "export", "requires completed run, view, request, resolver context, and reproducibility evidence.")
    fingerprint = result_run_fingerprint(run)
    expected = (
        ("request.session_id", request.session_id, context.session_id),
        ("request.run_id", request.run_id, run.run_id),
        ("context.run_id", context.run_id, run.run_id),
        ("view.run_fingerprint", view.run_fingerprint, fingerprint),
        ("request.view_fingerprint", request.view_fingerprint, view.view_fingerprint),
    )
    for path, actual, required in expected:
        if actual != required:
            raise _error("stale-export-authority", path, f"must equal active value {required!r}.")
    if view.kind is ResultExportViewKind.BASE and view.row_count != run.row_count:
        raise _error("base-view-row-count", "view.row_count", "must cover the completed run.")
    validate_result_reproducibility_context(reproducibility, run, context)


def _validate_destination(request: ResultExportRequest) -> None:
    destination = request.destination
    parent = destination.parent
    if not parent.exists() or not parent.is_dir() or _is_linklike(parent):
        raise _error("unsafe-export-parent", str(parent), "must already exist as a plain directory.")
    if path_traverses_linklike(parent):
        raise _error("unsafe-export-parent", str(parent), "must not traverse a symlink or junction.")
    if destination.exists() or _is_linklike(destination):
        if not request.overwrite:
            raise _error("export-already-exists", str(destination), "overwrite must be explicitly enabled.")
        if _is_linklike(destination) or not destination.is_file():
            raise _error("unsafe-export-destination", str(destination), "only a plain file can be explicitly replaced.")


def _close_columns(columns: dict[str, np.ndarray[Any, Any]]) -> None:
    for values in columns.values():
        mapping = getattr(values, "_mmap", None)
        if mapping is not None:
            mapping.close()


def _chunk_ordinals(view: ResultExportView, start: int, end: int) -> np.ndarray[Any, np.dtype[np.uint32]]:
    if view.kind is ResultExportViewKind.BASE:
        return np.arange(start, end, dtype=_U4)
    assert view.row_ordinals is not None
    return np.asarray(view.row_ordinals[start:end], dtype=_U4).copy(order="C")


def _row_values(
    ordinal: int,
    row_index: int,
    arrays: dict[str, np.ndarray[Any, Any]],
    context: ResultResolverContext,
) -> list[object]:
    dense_ids = arrays["dense_item_ids"][row_index]
    set_indices = arrays["owned_set_indices"][row_index]
    stable_ids: list[str] = []
    set_ids: list[str] = []
    equipped = 0
    for slot_index, (dense_value, set_value) in enumerate(zip(dense_ids, set_indices, strict=True)):
        dense_id = int(dense_value)
        set_index = int(set_value)
        if dense_id >= len(context.search_gear_by_dense_id):
            raise _error("export-dense-id-out-of-range", f"row[{ordinal}].dense_item_ids[{slot_index}]", "is absent from the active search snapshot.")
        gear = context.search_gear_by_dense_id[dense_id]
        if context.search_slot_index_by_dense_id[dense_id] != slot_index or gear.slot is not GEAR_SLOT_ORDER[slot_index]:
            raise _error("export-slot-drift", f"row[{ordinal}].dense_item_ids[{slot_index}]", "does not resolve to its stored slot.")
        if context.search_set_index_by_dense_id[dense_id] != set_index:
            raise _error("export-set-drift", f"row[{ordinal}].owned_set_indices[{slot_index}]", "does not match the active gear record.")
        stable_ids.append(gear.item_id)
        set_ids.append(FRIBBELS_SET_ORDER[set_index].value)
        equipped += gear.equipped_hero_id is not None
    stored_equipped = int(arrays["equipped_item_counts"][row_index])
    if equipped != stored_equipped:
        raise _error("export-equipped-count-drift", f"row[{ordinal}].equipped_item_counts", "does not match the active gear records.")
    category = decode_result_category(int(arrays["category_codes"][row_index])).value
    replacement = int(arrays["replacement_distances"][row_index])
    primary = [int(value) for value in arrays["effective_final_stats"][row_index]]
    primary[4] = int(arrays["raw_critical_hit_chances"][row_index])
    derived = [int(value) for value in arrays["derived_metrics"][row_index]]
    priority = float(arrays["priority_scores"][row_index])
    constraint = float(arrays["constraint_distances"][row_index])
    return [
        ordinal,
        *stable_ids,
        *set_ids,
        category,
        replacement,
        *primary,
        *derived,
        priority,
        _binary32_bits(priority),
        constraint,
        _binary32_bits(constraint),
        stored_equipped,
    ]


def _write_header(file: TextIO, export_format: ResultExportFormat) -> csv.writer | None:
    if export_format is ResultExportFormat.CSV:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(RESULT_EXPORT_FIELD_NAMES)
        return writer
    file.write("[")
    return None


def _write_row(
    file: TextIO,
    writer: csv.writer | None,
    export_format: ResultExportFormat,
    values: list[object],
    first_json_row: bool,
) -> bool:
    if export_format is ResultExportFormat.CSV:
        assert writer is not None
        writer.writerow(values)
        return first_json_row
    file.write("\n" if first_json_row else ",\n")
    file.write(
        json.dumps(
            dict(zip(RESULT_EXPORT_FIELD_NAMES, values, strict=True)),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )
    return False


def export_result_view(
    run: CompletedResultRun,
    view: ResultExportView,
    request: ResultExportRequest,
    context: ResultResolverContext,
    reproducibility: ResultReproducibilityRecord,
    *,
    cancelled: Callable[[], bool] | None = None,
    checkpoint: Callable[[str], None] | None = None,
) -> ResultExportOutcome:
    """Stream one compact view and atomically publish the complete export."""

    if cancelled is not None and not callable(cancelled):
        raise _error("invalid-cancellation", "cancelled", "must be callable or None.")
    if checkpoint is not None and not callable(checkpoint):
        raise _error("invalid-checkpoint", "checkpoint", "must be callable or None.")
    _validate_export_authority(run, view, request, context, reproducibility)
    _validate_destination(request)
    projection = project_result_export(view.row_count, request.chunk_rows)
    destination = request.destination
    temporary = request.temporary_path
    columns: dict[str, np.ndarray[Any, Any]] = {}
    chunk_count = 0
    first_json_row = True

    def hit(name: str) -> None:
        if checkpoint is not None:
            checkpoint(name)

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise _error("export-cancelled", "export", "was cancelled before atomic publication.")

    try:
        check_cancelled()
        columns = {name: run.open_column(name) for name in RESULT_COLUMN_NAMES}
        hit("after-export-open")
        with temporary.open("x", encoding="utf-8", newline="") as file:
            writer = _write_header(file, request.format)
            for start in range(0, view.row_count, request.chunk_rows):
                check_cancelled()
                end = min(start + request.chunk_rows, view.row_count)
                ordinals = _chunk_ordinals(view, start, end)
                if ordinals.size and int(np.max(ordinals)) >= run.row_count:
                    raise _error("export-ordinal-out-of-range", "view.row_ordinals", "references a row outside the completed run.")
                chunk = {
                    name: np.asarray(values[ordinals]).copy(order="C")
                    for name, values in columns.items()
                }
                try:
                    validate_result_columns(chunk, row_count=end - start)
                except ResultSchemaError as error:
                    raise _error(error.code, error.path, error.message) from error
                for offset, ordinal_value in enumerate(ordinals):
                    values = _row_values(int(ordinal_value), offset, chunk, context)
                    first_json_row = _write_row(file, writer, request.format, values, first_json_row)
                chunk_count += 1
                hit(f"after-export-chunk:{chunk_count}")
            if request.format is ResultExportFormat.JSON:
                file.write("\n]\n" if view.row_count else "]\n")
            check_cancelled()
            file.flush()
            os.fsync(file.fileno())
        hit("after-export-fsync")
        file_bytes = temporary.stat().st_size
        digest = _hash_file(temporary)
        check_cancelled()
        hit("before-export-publish")
        check_cancelled()
        if request.overwrite:
            if destination.exists() and (_is_linklike(destination) or not destination.is_file()):
                raise _error("unsafe-export-destination", str(destination), "changed into an unsafe publication target.")
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise _error("export-publication-conflict", str(destination), "appeared before atomic publication.") from None
            try:
                temporary.unlink()
            except OSError:
                # The destination is already a complete immutable hard link;
                # lifecycle cleanup can safely reclaim the recognized temp.
                pass
        _fsync_directory(destination.parent)
        return ResultExportOutcome(destination, request.format, view.view_fingerprint, view.row_count, chunk_count, file_bytes, digest, projection)
    except Exception:
        if temporary.exists() and not _is_linklike(temporary):
            temporary.unlink()
        raise
    finally:
        _close_columns(columns)


__all__ = [
    "DEFAULT_EXPORT_CHUNK_ROWS",
    "MAX_EXPORT_CHUNK_ROWS",
    "RESULT_EXPORT_FIELD_NAMES",
    "RESULT_EXPORT_ID",
    "RESULT_EXPORT_VERSION",
    "ResultExportError",
    "ResultExportFormat",
    "ResultExportOutcome",
    "ResultExportProjection",
    "ResultExportRequest",
    "ResultExportView",
    "ResultExportViewKind",
    "create_base_export_view",
    "create_filtered_export_view",
    "create_sorted_export_view",
    "export_result_view",
    "project_result_export",
]
