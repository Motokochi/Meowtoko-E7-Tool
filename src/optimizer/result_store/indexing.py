"""Stable numeric sort indexes, bounded pages, and an optional disk cache."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from numbers import Integral
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

import numpy as np

from src.core.path_safety import lexical_absolute_path, path_traverses_linklike
from src.optimizer.domain import MAX_RESULT_CAP
from src.optimizer.result_store.filtering import (
    RESULT_FILTER_ID,
    RESULT_FILTER_VERSION,
    FilteredResultView,
)
from src.optimizer.result_store.schema import (
    RESULT_DERIVED_METRIC_ORDER,
    RESULT_PRIMARY_STAT_ORDER,
    RESULT_SCHEMA_ID,
    RESULT_SCHEMA_VERSION,
)
from src.optimizer.result_store.storage import (
    RESULT_RUN_FORMAT_ID,
    RESULT_RUN_FORMAT_VERSION,
    CompletedResultRun,
)


RESULT_INDEX_ID = "e7.optimizer.result-sort-index"
RESULT_INDEX_VERSION = 1
RESULT_PAGE_ID = "e7.optimizer.result-page"
RESULT_PAGE_VERSION = 1
RESULT_INDEX_FILENAME = "row-ordinals.u4"
RESULT_INDEX_MANIFEST_NAME = "manifest.json"

DEFAULT_MAXIMUM_BUILD_ARRAY_BYTES = 192 * (1 << 20)
DEFAULT_INDEX_CACHE_BYTES = 256 * (1 << 20)
DEFAULT_INDEX_CACHE_ENTRIES = 8
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1_000

_U4 = np.dtype("<u4")
_I8 = np.dtype("<i8")
_F4 = np.dtype("<f4")
_INTP = np.dtype(np.intp)
_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = frozenset(
    {
        "formatId",
        "formatVersion",
        "cacheKey",
        "runFingerprint",
        "viewFingerprint",
        "sortKey",
        "direction",
        "rowCount",
        "indexFile",
        "indexBytes",
        "indexSha256",
        "completedUtc",
    }
)


class ResultIndexError(ValueError):
    """Actionable sort-index, cache, or page failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> ResultIndexError:
    return ResultIndexError(code, path, message)


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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ResultSortDirection(StrEnum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


@dataclass(frozen=True, slots=True)
class ResultSortKey:
    key_id: str
    column_name: str
    axis_index: int | None
    dtype: np.dtype[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not self.key_id:
            raise _error("invalid-sort-key-id", "ResultSortKey.key_id", "must be nonempty text.")
        if not isinstance(self.column_name, str) or not self.column_name:
            raise _error("invalid-sort-column", "ResultSortKey.column_name", "must be nonempty text.")
        axis = self.axis_index
        if axis is not None:
            axis = _integer(axis, "ResultSortKey.axis_index", 0, len(RESULT_DERIVED_METRIC_ORDER) - 1)
        dtype = np.dtype(self.dtype)
        if dtype.str not in {_I8.str, _F4.str, np.dtype("u1").str}:
            raise _error("invalid-sort-dtype", "ResultSortKey.dtype", "must be signed int64, binary32, or uint8.")
        object.__setattr__(self, "axis_index", axis)
        object.__setattr__(self, "dtype", dtype)


RESULT_PRIMARY_SORT_KEYS = tuple(
    ResultSortKey(
        f"primary:{stat.value}",
        "raw_critical_hit_chances" if index == 4 else "effective_final_stats",
        None if index == 4 else index,
        _I8,
    )
    for index, stat in enumerate(RESULT_PRIMARY_STAT_ORDER)
)
RESULT_DERIVED_SORT_KEYS = tuple(
    ResultSortKey(f"derived:{metric_id}", "derived_metrics", index, _I8)
    for index, metric_id in enumerate(RESULT_DERIVED_METRIC_ORDER)
)
PRIORITY_SCORE_SORT_KEY = ResultSortKey("priority-score", "priority_scores", None, _F4)
CONSTRAINT_DISTANCE_SORT_KEY = ResultSortKey(
    "constraint-distance", "constraint_distances", None, _F4
)
REPLACEMENT_COUNT_SORT_KEY = ResultSortKey(
    "replacement-count", "replacement_distances", None, np.dtype("u1")
)
EQUIPPED_COUNT_SORT_KEY = ResultSortKey(
    "equipped-count", "equipped_item_counts", None, np.dtype("u1")
)
RESULT_SORT_KEYS = (
    *RESULT_PRIMARY_SORT_KEYS,
    *RESULT_DERIVED_SORT_KEYS,
    PRIORITY_SCORE_SORT_KEY,
    CONSTRAINT_DISTANCE_SORT_KEY,
    REPLACEMENT_COUNT_SORT_KEY,
    EQUIPPED_COUNT_SORT_KEY,
)
RESULT_SORT_KEYS_BY_ID: Mapping[str, ResultSortKey] = MappingProxyType(
    {item.key_id: item for item in RESULT_SORT_KEYS}
)


def _sort_key(value: object, path: str = "sort_key") -> ResultSortKey:
    key_id = value.key_id if isinstance(value, ResultSortKey) else value
    if not isinstance(key_id, str) or key_id not in RESULT_SORT_KEYS_BY_ID:
        raise _error(
            "unknown-sort-key",
            path,
            f"must be one of {tuple(RESULT_SORT_KEYS_BY_ID)!r}.",
        )
    return RESULT_SORT_KEYS_BY_ID[key_id]


@dataclass(frozen=True, slots=True)
class ResultSortRequest:
    index_id: str = RESULT_INDEX_ID
    version: int = RESULT_INDEX_VERSION
    sort_key: ResultSortKey = PRIORITY_SCORE_SORT_KEY
    direction: ResultSortDirection = ResultSortDirection.DESCENDING

    def __post_init__(self) -> None:
        if self.index_id != RESULT_INDEX_ID:
            raise _error("index-id-mismatch", "ResultSortRequest.index_id", f"must be {RESULT_INDEX_ID!r}.")
        if self.version != RESULT_INDEX_VERSION:
            raise _error(
                "index-version-mismatch",
                "ResultSortRequest.version",
                f"must be {RESULT_INDEX_VERSION}.",
            )
        key = _sort_key(self.sort_key, "ResultSortRequest.sort_key")
        try:
            direction = (
                self.direction
                if isinstance(self.direction, ResultSortDirection)
                else ResultSortDirection(self.direction)
            )
        except (TypeError, ValueError):
            raise _error(
                "invalid-sort-direction",
                "ResultSortRequest.direction",
                "must be ascending or descending.",
            ) from None
        object.__setattr__(self, "sort_key", key)
        object.__setattr__(self, "direction", direction)


@dataclass(frozen=True, slots=True)
class ResultPageRequest:
    page_id: str = RESULT_PAGE_ID
    version: int = RESULT_PAGE_VERSION
    page_index: int = 0
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if self.page_id != RESULT_PAGE_ID:
            raise _error("page-id-mismatch", "ResultPageRequest.page_id", f"must be {RESULT_PAGE_ID!r}.")
        if self.version != RESULT_PAGE_VERSION:
            raise _error(
                "page-version-mismatch",
                "ResultPageRequest.version",
                f"must be {RESULT_PAGE_VERSION}.",
            )
        page_index = _integer(self.page_index, "ResultPageRequest.page_index", 0, MAX_RESULT_CAP)
        page_size = _integer(self.page_size, "ResultPageRequest.page_size", 1, MAX_PAGE_SIZE)
        object.__setattr__(self, "page_index", page_index)
        object.__setattr__(self, "page_size", page_size)


@dataclass(frozen=True, slots=True)
class ResultSortIndexProjection:
    row_count: int
    key_item_bytes: int
    source_ordinal_bytes: int
    key_value_bytes: int
    sort_order_bytes: int
    sort_workspace_bytes: int
    output_index_bytes: int
    declared_peak_array_bytes: int
    cache_index_bytes: int


@dataclass(frozen=True, slots=True)
class ResultSortCacheEntryProjection:
    row_count: int
    filtered_view: bool
    index_bytes: int
    manifest_bytes: int
    total_bytes: int


def project_result_sort_index(
    row_count: object,
    sort_key: object = PRIORITY_SCORE_SORT_KEY,
) -> ResultSortIndexProjection:
    count = _integer(row_count, "row_count", 0, MAX_RESULT_CAP)
    key = _sort_key(sort_key)
    source = count * _U4.itemsize
    values = count * key.dtype.itemsize
    order = count * _INTP.itemsize
    workspace = order
    output = count * _U4.itemsize
    return ResultSortIndexProjection(
        row_count=count,
        key_item_bytes=key.dtype.itemsize,
        source_ordinal_bytes=source,
        key_value_bytes=values,
        sort_order_bytes=order,
        sort_workspace_bytes=workspace,
        output_index_bytes=output,
        declared_peak_array_bytes=source + values + order + workspace + output,
        cache_index_bytes=output,
    )


def project_result_sort_cache_entry(
    row_count: object,
    request: ResultSortRequest = ResultSortRequest(),
    *,
    filtered_view: object = False,
) -> ResultSortCacheEntryProjection:
    count = _integer(row_count, "row_count", 0, MAX_RESULT_CAP)
    if not isinstance(request, ResultSortRequest):
        raise _error("invalid-sort-request", "request", "must be ResultSortRequest.")
    if not isinstance(filtered_view, bool):
        raise _error("invalid-view-kind", "filtered_view", "must be boolean.")
    run_fingerprint = "0" * 64
    view_fingerprint = (
        f"filtered:{count}:{'0' * 64}" if filtered_view else f"base:{run_fingerprint}"
    )
    manifest: dict[str, object] = {
        "formatId": RESULT_INDEX_ID,
        "formatVersion": RESULT_INDEX_VERSION,
        "cacheKey": "0" * 64,
        "runFingerprint": run_fingerprint,
        "viewFingerprint": view_fingerprint,
        "sortKey": request.sort_key.key_id,
        "direction": request.direction.value,
        "rowCount": count,
        "indexFile": RESULT_INDEX_FILENAME,
        "indexBytes": count * _U4.itemsize,
        "indexSha256": "0" * 64,
        "completedUtc": "2000-01-01T00:00:00.000000Z",
    }
    manifest_bytes = len(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ) + 1
    index_bytes = count * _U4.itemsize
    return ResultSortCacheEntryProjection(
        count,
        filtered_view,
        index_bytes,
        manifest_bytes,
        index_bytes + manifest_bytes,
    )


@dataclass(frozen=True, slots=True)
class ResultSortIndexStats:
    cache_hit: bool
    cache_published: bool
    projection: ResultSortIndexProjection
    build_peak_array_bytes: int


@dataclass(frozen=True, slots=True)
class CompletedResultSortIndex:
    cache_key: str
    run_fingerprint: str
    view_fingerprint: str
    request: ResultSortRequest
    row_ordinals: np.ndarray[Any, np.dtype[np.uint32]]
    cache_path: Path | None
    stats: ResultSortIndexStats

    def __post_init__(self) -> None:
        if not _CACHE_KEY.fullmatch(self.cache_key):
            raise _error("invalid-cache-key", "CompletedResultSortIndex.cache_key", "must be lowercase SHA-256 hex.")
        for path, value in (
            ("run_fingerprint", self.run_fingerprint),
            ("view_fingerprint", self.view_fingerprint),
        ):
            if not isinstance(value, str) or not value:
                raise _error("invalid-fingerprint", f"CompletedResultSortIndex.{path}", "must be nonempty text.")
        values = self.row_ordinals
        if not isinstance(values, np.ndarray) or values.dtype.str != _U4.str or values.ndim != 1:
            raise _error(
                "invalid-sort-index",
                "CompletedResultSortIndex.row_ordinals",
                "must be a one-dimensional little-endian uint32 array.",
            )
        values.flags.writeable = False

    @property
    def row_count(self) -> int:
        return int(self.row_ordinals.size)


@dataclass(frozen=True, slots=True)
class ResultPage:
    request: ResultPageRequest
    total_rows: int
    page_count: int
    start_offset: int
    end_offset: int
    has_previous: bool
    has_next: bool
    out_of_range: bool
    row_ordinals: np.ndarray[Any, np.dtype[np.uint32]]

    def __post_init__(self) -> None:
        values = self.row_ordinals
        if not isinstance(values, np.ndarray) or values.dtype.str != _U4.str or values.ndim != 1:
            raise _error("invalid-page-index", "ResultPage.row_ordinals", "must be a one-dimensional uint32 array.")
        if values.size > self.request.page_size:
            raise _error("oversized-page", "ResultPage.row_ordinals", "must not exceed requested page size.")
        values.flags.writeable = False

    @property
    def returned_rows(self) -> int:
        return int(self.row_ordinals.size)


def page_result_sort_index(
    index: CompletedResultSortIndex,
    request: ResultPageRequest,
) -> ResultPage:
    if not isinstance(index, CompletedResultSortIndex):
        raise _error("invalid-completed-index", "index", "must be CompletedResultSortIndex.")
    if not isinstance(request, ResultPageRequest):
        raise _error("invalid-page-request", "request", "must be ResultPageRequest.")
    total = index.row_count
    page_count = math.ceil(total / request.page_size) if total else 0
    requested_start = request.page_index * request.page_size
    out_of_range = bool(total and request.page_index >= page_count) or (
        not total and request.page_index > 0
    )
    start = min(requested_start, total)
    end = min(start + request.page_size, total)
    ordinals = np.asarray(index.row_ordinals[start:end], dtype=_U4).copy()
    ordinals.flags.writeable = False
    return ResultPage(
        request=request,
        total_rows=total,
        page_count=page_count,
        start_offset=start,
        end_offset=end,
        has_previous=bool(total and request.page_index > 0),
        has_next=end < total,
        out_of_range=out_of_range,
        row_ordinals=ordinals,
    )


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_array(values: np.ndarray[Any, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(memoryview(values).cast("B"))
    return digest.hexdigest()


def _hash_file(path: Path, chunk_bytes: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _run_fingerprint(run: CompletedResultRun) -> str:
    payload: dict[str, object] = {
        "runFormatId": RESULT_RUN_FORMAT_ID,
        "runFormatVersion": RESULT_RUN_FORMAT_VERSION,
        "schemaId": RESULT_SCHEMA_ID,
        "schemaVersion": RESULT_SCHEMA_VERSION,
        "runId": run.run_id,
        "rowCount": run.row_count,
        "completedUtc": run.completed_utc,
        "columns": tuple((item.name, item.sha256) for item in run.columns),
    }
    return _hash_bytes(_canonical_json(payload))


def result_run_fingerprint(run: CompletedResultRun) -> str:
    """Return the canonical identity used by every index for one completed run."""

    if not isinstance(run, CompletedResultRun):
        raise _error("invalid-completed-run", "run", "must be CompletedResultRun.")
    return _run_fingerprint(run)


def _view_fingerprint(
    run_fingerprint: str,
    view: FilteredResultView | None,
) -> str:
    if view is None:
        return f"base:{run_fingerprint}"
    return f"filtered:{view.row_ordinals.size}:{_hash_array(view.row_ordinals)}"


def _cache_key(
    run_fingerprint: str,
    view_fingerprint: str,
    request: ResultSortRequest,
) -> str:
    payload: dict[str, object] = {
        "formatId": RESULT_INDEX_ID,
        "formatVersion": RESULT_INDEX_VERSION,
        "filterId": RESULT_FILTER_ID,
        "filterVersion": RESULT_FILTER_VERSION,
        "runFingerprint": run_fingerprint,
        "viewFingerprint": view_fingerprint,
        "sortKey": request.sort_key.key_id,
        "direction": request.direction.value,
    }
    return _hash_bytes(_canonical_json(payload))


def _safe_remove_directory(path: Path, root: Path) -> None:
    if path_traverses_linklike(root) or path_traverses_linklike(path):
        raise _error("unsafe-cache-path", str(path), "must not traverse a symlink or junction.")
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return
    if resolved.parent != resolved_root or path.is_symlink():
        raise _error("unsafe-cache-path", str(path), "must be a plain direct child of the cache root.")
    shutil.rmtree(resolved)


def _fsync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class ResultSortIndexCache:
    """Explicit-root disk cache; construction never creates or scans paths."""

    def __init__(
        self,
        root: str | Path,
        *,
        maximum_bytes: object = DEFAULT_INDEX_CACHE_BYTES,
        maximum_entries: object = DEFAULT_INDEX_CACHE_ENTRIES,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(root, (str, os.PathLike)):
            raise _error("invalid-cache-root", "root", "must be an explicit filesystem path.")
        self.root = lexical_absolute_path(root)
        self.maximum_bytes = _integer(maximum_bytes, "maximum_bytes", 1, 1 << 40)
        self.maximum_entries = _integer(maximum_entries, "maximum_entries", 1, 1_000)
        if checkpoint is not None and not callable(checkpoint):
            raise _error("invalid-checkpoint", "checkpoint", "must be callable or None.")
        self._checkpoint = checkpoint

    def _hit(self, name: str) -> None:
        if self._checkpoint is not None:
            self._checkpoint(name)

    def _entry_path(self, key: str) -> Path:
        if not _CACHE_KEY.fullmatch(key):
            raise _error("invalid-cache-key", "cache_key", "must be lowercase SHA-256 hex.")
        return self.root / key

    def _manifest(
        self,
        key: str,
        run_fingerprint: str,
        view_fingerprint: str,
        request: ResultSortRequest,
        row_count: int,
        index_digest: str,
    ) -> dict[str, object]:
        return {
            "formatId": RESULT_INDEX_ID,
            "formatVersion": RESULT_INDEX_VERSION,
            "cacheKey": key,
            "runFingerprint": run_fingerprint,
            "viewFingerprint": view_fingerprint,
            "sortKey": request.sort_key.key_id,
            "direction": request.direction.value,
            "rowCount": row_count,
            "indexFile": RESULT_INDEX_FILENAME,
            "indexBytes": row_count * _U4.itemsize,
            "indexSha256": index_digest,
            "completedUtc": _utc_now(),
        }

    def _open(
        self,
        key: str,
        run_fingerprint: str,
        view_fingerprint: str,
        request: ResultSortRequest,
        row_count: int,
    ) -> tuple[np.ndarray[Any, Any], Path] | None:
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise _error("unsafe-cache-root", str(self.root), "must be a plain directory.")
        entry = self._entry_path(key)
        if entry.is_symlink():
            raise _error("unsafe-cache-entry", str(entry), "must be a plain directory.")
        manifest_path = entry / RESULT_INDEX_MANIFEST_NAME
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise _error("invalid-cache-manifest", str(manifest_path), str(error)) from error
        if not isinstance(data, dict) or frozenset(data) != _MANIFEST_FIELDS:
            raise _error("cache-manifest-fields", str(manifest_path), "must contain exactly the v1 fields.")
        for field in ("formatVersion", "rowCount", "indexBytes"):
            if isinstance(data[field], bool) or not isinstance(data[field], int):
                raise _error("cache-manifest-integer", f"manifest.{field}", "must be an integer.")
        expected: dict[str, object] = {
            "formatId": RESULT_INDEX_ID,
            "formatVersion": RESULT_INDEX_VERSION,
            "cacheKey": key,
            "runFingerprint": run_fingerprint,
            "viewFingerprint": view_fingerprint,
            "sortKey": request.sort_key.key_id,
            "direction": request.direction.value,
            "rowCount": row_count,
            "indexFile": RESULT_INDEX_FILENAME,
            "indexBytes": row_count * _U4.itemsize,
        }
        for field, value in expected.items():
            if data[field] != value:
                raise _error("cache-manifest-mismatch", f"manifest.{field}", f"must equal {value!r}.")
        digest = data["indexSha256"]
        timestamp = data["completedUtc"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise _error("invalid-cache-digest", "manifest.indexSha256", "must be lowercase SHA-256 hex.")
        if not isinstance(timestamp, str) or not timestamp:
            raise _error("invalid-cache-timestamp", "manifest.completedUtc", "must be nonempty text.")
        index_path = entry / RESULT_INDEX_FILENAME
        if not index_path.is_file() or index_path.is_symlink():
            raise _error("cache-index-missing", str(index_path), "completed index file is missing or unsafe.")
        expected_bytes = row_count * _U4.itemsize
        if index_path.stat().st_size != expected_bytes:
            raise _error("cache-index-size", str(index_path), f"must contain exactly {expected_bytes} bytes.")
        if _hash_file(index_path) != digest:
            raise _error("cache-index-digest", str(index_path), "does not match the completed manifest.")
        try:
            os.utime(manifest_path, None)
        except OSError:
            pass
        if row_count:
            values = np.memmap(index_path, dtype=_U4, mode="r", shape=(row_count,), order="C")
        else:
            values = np.empty(0, dtype=_U4)
            values.flags.writeable = False
        return values, entry

    def _completed_entries(self) -> list[tuple[int, int, Path]]:
        if not self.root.is_dir() or self.root.is_symlink():
            return []
        entries: list[tuple[int, int, Path]] = []
        for path in self.root.iterdir():
            if not path.is_dir() or path.is_symlink() or not _CACHE_KEY.fullmatch(path.name):
                continue
            manifest = path / RESULT_INDEX_MANIFEST_NAME
            index = path / RESULT_INDEX_FILENAME
            if manifest.is_file() and not manifest.is_symlink() and index.is_file() and not index.is_symlink():
                size = manifest.stat().st_size + index.stat().st_size
                entries.append((manifest.stat().st_mtime_ns, size, path))
        return entries

    def _enforce_budget(self, protected: Path) -> bool:
        entries = self._completed_entries()
        total = sum(item[1] for item in entries)
        ordered = sorted(entries, key=lambda item: (item[0], item[2].name))
        while len(ordered) > self.maximum_entries or total > self.maximum_bytes:
            removed = False
            for victim_index, (_, size, victim) in enumerate(ordered):
                if victim == protected:
                    continue
                try:
                    _safe_remove_directory(victim, self.root)
                except OSError:
                    # A page/index held by another caller can keep a Windows
                    # memmap locked. It is not an eligible eviction victim.
                    continue
                ordered.pop(victim_index)
                total -= size
                removed = True
                break
            if removed:
                continue
            # The newly built index is not mapped yet, so drop it rather than
            # exceed the declared cache budget when all older entries are busy.
            protected_item = next((item for item in ordered if item[2] == protected), None)
            if protected_item is not None:
                _safe_remove_directory(protected, self.root)
                return False
            break
        return protected.exists()

    def _discard(self, key: str) -> None:
        entry = self._entry_path(key)
        if entry.exists():
            _safe_remove_directory(entry, self.root)

    def load(
        self,
        key: str,
        run_fingerprint: str,
        view_fingerprint: str,
        request: ResultSortRequest,
        row_count: int,
    ) -> tuple[np.ndarray[Any, Any], Path] | None:
        try:
            return self._open(key, run_fingerprint, view_fingerprint, request, row_count)
        except ResultIndexError as error:
            if error.code in {"unsafe-cache-root", "unsafe-cache-entry", "invalid-cache-key"}:
                raise
            self._discard(key)
            return None

    def publish(
        self,
        key: str,
        run_fingerprint: str,
        view_fingerprint: str,
        request: ResultSortRequest,
        values: np.ndarray[Any, Any],
    ) -> Path | None:
        digest = _hash_array(values)
        manifest = self._manifest(
            key,
            run_fingerprint,
            view_fingerprint,
            request,
            int(values.size),
            digest,
        )
        manifest_bytes = _canonical_json(manifest) + b"\n"
        if values.nbytes + len(manifest_bytes) > self.maximum_bytes:
            return None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise _error("unsafe-cache-root", str(self.root), "could not create a plain cache directory.") from error
        if self.root.is_symlink() or not self.root.is_dir():
            raise _error("unsafe-cache-root", str(self.root), "must be a plain directory.")
        final = self._entry_path(key)
        existing = self.load(
            key,
            run_fingerprint,
            view_fingerprint,
            request,
            int(values.size),
        )
        if existing is not None:
            return existing[1]
        temporary = self.root / f".{key}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir()
        try:
            index_path = temporary / RESULT_INDEX_FILENAME
            with index_path.open("xb") as file:
                payload = memoryview(values).cast("B")
                written = file.write(payload)
                if written != payload.nbytes:
                    raise OSError(f"short index write: {written}/{payload.nbytes}")
                file.flush()
                os.fsync(file.fileno())
            self._hit("after-index-fsync")
            pending = temporary / f"{RESULT_INDEX_MANIFEST_NAME}.pending"
            with pending.open("xb") as file:
                file.write(manifest_bytes)
                file.flush()
                os.fsync(file.fileno())
            self._hit("after-manifest-fsync")
            os.replace(pending, temporary / RESULT_INDEX_MANIFEST_NAME)
            _fsync_directory(temporary)
            self._hit("before-cache-publish")
            if final.exists():
                self._discard(key)
            os.replace(temporary, final)
            _fsync_directory(self.root)
            self._hit("after-cache-publish")
            return final if self._enforce_budget(final) else None
        except Exception:
            if temporary.exists():
                _safe_remove_directory(temporary, self.root)
            raise


def _source_ordinals(
    run: CompletedResultRun,
    view: FilteredResultView | None,
) -> np.ndarray[Any, Any]:
    if view is None:
        return np.arange(run.row_count, dtype=_U4)
    if not isinstance(view, FilteredResultView):
        raise _error("invalid-filtered-view", "view", "must be FilteredResultView or None.")
    values = view.row_ordinals
    if values.size and int(values[-1]) >= run.row_count:
        raise _error("view-ordinal-out-of-range", "view.row_ordinals", "contains an ordinal outside the completed run.")
    return values


def _gather_sort_values(
    run: CompletedResultRun,
    source: np.ndarray[Any, Any],
    key: ResultSortKey,
) -> np.ndarray[Any, Any]:
    column = run.open_column(key.column_name)
    try:
        selected = column if key.axis_index is None else column[:, key.axis_index]
        if selected.dtype.str != key.dtype.str:
            raise _error(
                "sort-key-dtype",
                key.key_id,
                f"must use exact stored dtype {key.dtype.str}; found {selected.dtype.str}.",
            )
        values = np.asarray(selected[source]).copy()
    finally:
        mapping = getattr(column, "_mmap", None)
        if mapping is not None:
            mapping.close()
    if values.dtype.str != key.dtype.str:
        raise _error("sort-key-narrowing", key.key_id, f"must preserve exact dtype {key.dtype.str}.")
    if key.dtype.str == _F4.str and not np.all(np.isfinite(values)):
        raise _error("nonfinite-sort-key", key.key_id, "NaN and infinity are not sortable result values.")
    return values


def build_result_sort_index(
    run: CompletedResultRun,
    request: ResultSortRequest,
    *,
    view: FilteredResultView | None = None,
    cache: ResultSortIndexCache | None = None,
    maximum_build_array_bytes: object = DEFAULT_MAXIMUM_BUILD_ARRAY_BYTES,
) -> CompletedResultSortIndex:
    """Build or reuse a stable numeric index over the exact supplied view."""

    if not isinstance(run, CompletedResultRun):
        raise _error("invalid-completed-run", "run", "must be CompletedResultRun.")
    if not isinstance(request, ResultSortRequest):
        raise _error("invalid-sort-request", "request", "must be ResultSortRequest.")
    if cache is not None and not isinstance(cache, ResultSortIndexCache):
        raise _error("invalid-index-cache", "cache", "must be ResultSortIndexCache or None.")
    if view is not None and not isinstance(view, FilteredResultView):
        raise _error("invalid-filtered-view", "view", "must be FilteredResultView or None.")
    maximum = _integer(
        maximum_build_array_bytes,
        "maximum_build_array_bytes",
        1,
        1 << 40,
    )
    run_fingerprint = _run_fingerprint(run)
    view_fingerprint = _view_fingerprint(run_fingerprint, view)
    key = _cache_key(run_fingerprint, view_fingerprint, request)
    count = run.row_count if view is None else int(view.row_ordinals.size)
    projection = project_result_sort_index(count, request.sort_key)

    if cache is not None:
        cached = cache.load(key, run_fingerprint, view_fingerprint, request, count)
        if cached is not None:
            values, path = cached
            return CompletedResultSortIndex(
                key,
                run_fingerprint,
                view_fingerprint,
                request,
                values,
                path,
                ResultSortIndexStats(True, False, projection, 0),
            )
    if projection.declared_peak_array_bytes > maximum:
        raise _error(
            "sort-memory-budget",
            "maximum_build_array_bytes",
            f"requires {projection.declared_peak_array_bytes} declared array bytes; budget is {maximum}.",
        )

    source = _source_ordinals(run, view)
    if count:
        values = _gather_sort_values(run, source, request.sort_key)
        if request.direction is ResultSortDirection.DESCENDING:
            if values.dtype.kind == "f":
                np.negative(values, out=values)
            else:
                np.bitwise_not(values, out=values)
        order = np.lexsort((source, values))
        sorted_ordinals = np.asarray(source[order], dtype=_U4)
    else:
        sorted_ordinals = np.empty(0, dtype=_U4)
    sorted_ordinals.flags.writeable = False

    cache_path: Path | None = None
    published = False
    if cache is not None:
        cache_path = cache.publish(
            key,
            run_fingerprint,
            view_fingerprint,
            request,
            sorted_ordinals,
        )
        published = cache_path is not None

    return CompletedResultSortIndex(
        key,
        run_fingerprint,
        view_fingerprint,
        request,
        sorted_ordinals,
        cache_path,
        ResultSortIndexStats(False, published, projection, projection.declared_peak_array_bytes),
    )


__all__ = [
    "CONSTRAINT_DISTANCE_SORT_KEY",
    "DEFAULT_INDEX_CACHE_BYTES",
    "DEFAULT_INDEX_CACHE_ENTRIES",
    "DEFAULT_MAXIMUM_BUILD_ARRAY_BYTES",
    "DEFAULT_PAGE_SIZE",
    "EQUIPPED_COUNT_SORT_KEY",
    "MAX_PAGE_SIZE",
    "PRIORITY_SCORE_SORT_KEY",
    "REPLACEMENT_COUNT_SORT_KEY",
    "RESULT_DERIVED_SORT_KEYS",
    "RESULT_INDEX_FILENAME",
    "RESULT_INDEX_ID",
    "RESULT_INDEX_MANIFEST_NAME",
    "RESULT_INDEX_VERSION",
    "RESULT_PAGE_ID",
    "RESULT_PAGE_VERSION",
    "RESULT_PRIMARY_SORT_KEYS",
    "RESULT_SORT_KEYS",
    "RESULT_SORT_KEYS_BY_ID",
    "CompletedResultSortIndex",
    "ResultIndexError",
    "ResultPage",
    "ResultPageRequest",
    "ResultSortDirection",
    "ResultSortIndexCache",
    "ResultSortIndexProjection",
    "ResultSortCacheEntryProjection",
    "ResultSortIndexStats",
    "ResultSortKey",
    "ResultSortRequest",
    "build_result_sort_index",
    "page_result_sort_index",
    "project_result_sort_index",
    "project_result_sort_cache_entry",
    "result_run_fingerprint",
]
