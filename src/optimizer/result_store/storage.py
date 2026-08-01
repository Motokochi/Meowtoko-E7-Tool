"""Transactional raw-column result runs with an atomic directory publish."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping
from uuid import uuid4

import numpy as np

from src.core.path_safety import lexical_absolute_path, path_traverses_linklike
from src.optimizer.domain import MAX_RESULT_CAP
from src.optimizer.result_store.schema import (
    RESULT_COLUMN_NAMES,
    RESULT_ROW_BYTES,
    RESULT_SCHEMA,
    RESULT_SCHEMA_ID,
    RESULT_SCHEMA_VERSION,
    ResultColumnSpec,
    ResultSchemaError,
    validate_result_columns,
)


RESULT_RUN_FORMAT_ID = "e7.optimizer.result-run"
RESULT_RUN_FORMAT_VERSION = 1
RESULT_RUN_MANIFEST_NAME = "manifest.json"
RESULT_RUN_COLUMNS_DIRECTORY = "columns"

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_KEYS = frozenset({
    "formatId",
    "formatVersion",
    "state",
    "runId",
    "schemaId",
    "schemaVersion",
    "rowCount",
    "bytesPerRow",
    "payloadBytes",
    "createdUtc",
    "completedUtc",
    "columns",
})
_COLUMN_KEYS = frozenset({
    "name",
    "file",
    "dtype",
    "rowShape",
    "shape",
    "bytesPerRow",
    "fileBytes",
    "sha256",
})


class ResultRunError(ValueError):
    """Actionable run path, transaction, manifest, or integrity failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> ResultRunError:
    return ResultRunError(code, path, message)


def _integer(value: object, path: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error("invalid-integer", path, "must be an integer.")
    if value < minimum or value > maximum:
        raise _error("integer-out-of-range", path, f"must be between {minimum} and {maximum}; found {value}.")
    return value


def _checked_run_id(value: object) -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value) or value in {".", ".."}:
        raise _error(
            "invalid-run-id",
            "run_id",
            "must be 1..128 ASCII letters, digits, dot, underscore, or hyphen and start alphanumeric.",
        )
    return value


def _column_filename(index: int, spec: ResultColumnSpec) -> str:
    return f"{index:02d}-{spec.name}.bin"


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def _require_plain_directory(path: Path, label: str) -> None:
    if _is_linklike(path) or path_traverses_linklike(path):
        raise _error("unsafe-storage-link", label, "must not be a symlink or junction.")
    if path.exists() and not path.is_dir():
        raise _error("storage-path-not-directory", label, "must be a directory.")


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


def _safe_remove_directory(path: Path, expected_parent: Path) -> None:
    _require_plain_directory(expected_parent, "cleanup-parent")
    if path.parent != expected_parent:
        raise _error("unsafe-cleanup-path", str(path), "is outside the transaction parent.")
    if not path.exists() and not _is_linklike(path):
        return
    if _is_linklike(path):
        path.unlink()
        return
    shutil.rmtree(path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _build_manifest(
    run_id: str,
    row_count: int,
    created_utc: str,
    completed_utc: str,
    digests: tuple[str, ...],
) -> dict[str, object]:
    descriptors = []
    for index, (spec, digest) in enumerate(zip(RESULT_SCHEMA.columns, digests, strict=True)):
        filename = _column_filename(index, spec)
        descriptors.append({
            "name": spec.name,
            "file": f"{RESULT_RUN_COLUMNS_DIRECTORY}/{filename}",
            "dtype": spec.dtype.str,
            "rowShape": list(spec.shape),
            "shape": [row_count, *spec.shape],
            "bytesPerRow": spec.bytes_per_row,
            "fileBytes": row_count * spec.bytes_per_row,
            "sha256": digest,
        })
    return {
        "formatId": RESULT_RUN_FORMAT_ID,
        "formatVersion": RESULT_RUN_FORMAT_VERSION,
        "state": "completed",
        "runId": run_id,
        "schemaId": RESULT_SCHEMA_ID,
        "schemaVersion": RESULT_SCHEMA_VERSION,
        "rowCount": row_count,
        "bytesPerRow": RESULT_ROW_BYTES,
        "payloadBytes": row_count * RESULT_ROW_BYTES,
        "createdUtc": created_utc,
        "completedUtc": completed_utc,
        "columns": descriptors,
    }


def _serialize_manifest(manifest: Mapping[str, object]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


class ResultRunWriterState(StrEnum):
    OPEN = "open"
    FAILED = "failed"
    ABORTED = "aborted"
    PUBLISHED = "published"


@dataclass(frozen=True, slots=True)
class ResultRunColumn:
    name: str
    path: Path
    dtype: np.dtype[Any]
    row_shape: tuple[int, ...]
    shape: tuple[int, ...]
    bytes_per_row: int
    file_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CompletedResultRun:
    run_id: str
    path: Path
    row_count: int
    bytes_per_row: int
    payload_bytes: int
    manifest_bytes: int
    created_utc: str
    completed_utc: str
    columns: tuple[ResultRunColumn, ...]

    @property
    def storage_bytes(self) -> int:
        return self.payload_bytes + self.manifest_bytes

    def column_spec(self, name: str) -> ResultRunColumn:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(name)

    def open_column(self, name: str) -> np.ndarray[Any, Any]:
        column = self.column_spec(name)
        if self.row_count == 0:
            values = np.empty(column.shape, dtype=column.dtype)
            values.flags.writeable = False
            return values
        return np.memmap(column.path, dtype=column.dtype, mode="r", shape=column.shape, order="C")


@dataclass(frozen=True, slots=True)
class ResultRunStorageProjection:
    run_id: str
    row_count: int
    payload_bytes: int
    column_header_bytes: int
    manifest_bytes: int
    published_bytes: int
    transaction_peak_bytes: int


def project_result_run_storage(run_id: object, row_count: object) -> ResultRunStorageProjection:
    identifier = _checked_run_id(run_id)
    count = _integer(row_count, "row_count", minimum=0, maximum=MAX_RESULT_CAP)
    timestamp = "2000-01-01T00:00:00.000000Z"
    manifest = _build_manifest(
        identifier,
        count,
        timestamp,
        timestamp,
        ("0" * 64,) * len(RESULT_SCHEMA.columns),
    )
    manifest_bytes = len(_serialize_manifest(manifest))
    payload_bytes = count * RESULT_ROW_BYTES
    return ResultRunStorageProjection(
        identifier,
        count,
        payload_bytes,
        0,
        manifest_bytes,
        payload_bytes + manifest_bytes,
        payload_bytes + manifest_bytes,
    )


class ResultRunWriter:
    """One append-only transaction. A run is visible only after ``complete``."""

    def __init__(
        self,
        *,
        store: ResultRunStore,
        run_id: str,
        maximum_rows: int,
        temporary_path: Path,
        published_path: Path,
        lock_path: Path,
        created_utc: str,
        files: tuple[BinaryIO, ...],
        checkpoint: Callable[[str], None] | None,
    ) -> None:
        self._store = store
        self.run_id = run_id
        self.maximum_rows = maximum_rows
        self.temporary_path = temporary_path
        self.published_path = published_path
        self.lock_path = lock_path
        self.created_utc = created_utc
        self._files = list(files)
        self._checkpoint = checkpoint
        self._hashes = [hashlib.sha256() for _ in RESULT_SCHEMA.columns]
        self._file_bytes = [0] * len(RESULT_SCHEMA.columns)
        self._row_count = 0
        self._state = ResultRunWriterState.OPEN
        self._directory_staged = False

    @property
    def state(self) -> ResultRunWriterState:
        return self._state

    @property
    def row_count(self) -> int:
        return self._row_count

    def _hit(self, name: str) -> None:
        if self._checkpoint is not None:
            self._checkpoint(name)

    def _ensure_open(self) -> None:
        if self._state is not ResultRunWriterState.OPEN:
            raise _error("writer-not-open", "ResultRunWriter.state", f"is {self._state.value}, not open.")

    def _close_files(self) -> None:
        for file in self._files:
            if not file.closed:
                file.close()

    def append(self, start_ordinal: object, arrays: Mapping[str, np.ndarray[Any, Any]]) -> int:
        self._ensure_open()
        try:
            start = _integer(start_ordinal, "start_ordinal", minimum=0, maximum=self.maximum_rows)
            if start != self._row_count:
                raise _error(
                    "noncontiguous-append",
                    "start_ordinal",
                    f"must equal the next physical row ordinal {self._row_count}; found {start}.",
                )
            try:
                count = validate_result_columns(arrays)
            except ResultSchemaError as error:
                raise _error(error.code, error.path, error.message) from error
            if count == 0:
                return self._row_count
            if self._row_count + count > self.maximum_rows:
                raise _error(
                    "result-cap-overflow",
                    "arrays",
                    f"would exceed transaction maximum {self.maximum_rows} rows.",
                )
            self._hit("before-batch-write")
            for index, (spec, file) in enumerate(zip(RESULT_SCHEMA.columns, self._files, strict=True)):
                payload = memoryview(arrays[spec.name]).cast("B")
                written = file.write(payload)
                if written != payload.nbytes:
                    raise OSError(f"short write for {spec.name}: {written}/{payload.nbytes}")
                self._hashes[index].update(payload)
                self._file_bytes[index] += written
                self._hit(f"after-column-write:{spec.name}")
            self._row_count += count
            self._hit("after-batch-write")
            return self._row_count
        except Exception:
            self._state = ResultRunWriterState.FAILED
            self._close_files()
            raise

    def complete(self, expected_row_count: object | None = None) -> CompletedResultRun:
        self._ensure_open()
        try:
            expected = self._row_count if expected_row_count is None else _integer(
                expected_row_count,
                "expected_row_count",
                minimum=0,
                maximum=self.maximum_rows,
            )
            if expected != self._row_count:
                raise _error(
                    "terminal-row-count-mismatch",
                    "expected_row_count",
                    f"must equal appended row count {self._row_count}; found {expected}.",
                )
            for file in self._files:
                file.flush()
                os.fsync(file.fileno())
                file.close()
            self._hit("after-column-fsync")
            columns_path = self.temporary_path / RESULT_RUN_COLUMNS_DIRECTORY
            for index, spec in enumerate(RESULT_SCHEMA.columns):
                filename = _column_filename(index, spec)
                path = columns_path / filename
                expected_bytes = self._row_count * spec.bytes_per_row
                actual_bytes = path.stat().st_size
                if actual_bytes != expected_bytes or self._file_bytes[index] != expected_bytes:
                    raise _error(
                        "column-size-mismatch",
                        spec.name,
                        f"expected {expected_bytes} bytes; found {actual_bytes}.",
                    )
            self._hit("after-column-verification")
            manifest = _build_manifest(
                self.run_id,
                self._row_count,
                self.created_utc,
                _utc_now(),
                tuple(digest.hexdigest() for digest in self._hashes),
            )
            manifest_bytes = _serialize_manifest(manifest)
            pending = self.temporary_path / f"{RESULT_RUN_MANIFEST_NAME}.pending"
            with pending.open("xb") as file:
                file.write(manifest_bytes)
                file.flush()
                os.fsync(file.fileno())
            self._hit("after-manifest-fsync")
            _fsync_directory(self.temporary_path)
            self._hit("before-directory-stage")
            _require_plain_directory(self._store.incomplete_path, "incomplete")
            _require_plain_directory(self._store.runs_path, "runs")
            os.replace(self.temporary_path, self.published_path)
            self._directory_staged = True
            _fsync_directory(self._store.runs_path)
            self._hit("before-manifest-publish")
            os.replace(
                self.published_path / f"{RESULT_RUN_MANIFEST_NAME}.pending",
                self.published_path / RESULT_RUN_MANIFEST_NAME,
            )
            _fsync_directory(self.published_path)
            _fsync_directory(self._store.runs_path)
            self._state = ResultRunWriterState.PUBLISHED
            self._release_lock()
            return self._store.open_run(self.run_id, verify_hashes=False)
        except Exception:
            if self._state is not ResultRunWriterState.PUBLISHED:
                self._state = ResultRunWriterState.FAILED
                self._close_files()
            raise

    def _release_lock(self) -> None:
        try:
            self.lock_path.rmdir()
        except FileNotFoundError:
            pass

    def abort(self, reason: str = "aborted") -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise _error("invalid-abort-reason", "reason", "must be nonempty text.")
        if self._state is ResultRunWriterState.PUBLISHED:
            raise _error("published-run-abort", "ResultRunWriter.state", "a published run cannot be aborted.")
        if self._state is ResultRunWriterState.ABORTED:
            return
        self._close_files()
        if self._directory_staged:
            _safe_remove_directory(self.published_path, self._store.runs_path)
        else:
            _safe_remove_directory(self.temporary_path, self._store.incomplete_path)
        self._release_lock()
        self._state = ResultRunWriterState.ABORTED

    def __enter__(self) -> ResultRunWriter:
        return self

    def __exit__(self, exception_type, exception, traceback) -> bool:
        if self._state is not ResultRunWriterState.PUBLISHED:
            self.abort("context-exit")
        return False


class ResultRunStore:
    """Explicit-root manager for transactional and completed result runs."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if not isinstance(root, (str, os.PathLike)):
            raise _error("invalid-storage-root", "root", "must be a filesystem path.")
        self.root = lexical_absolute_path(root)
        self.incomplete_path = self.root / ".incomplete"
        self.locks_path = self.root / ".locks"
        self.runs_path = self.root / "runs"

    def _prepare_roots(self) -> None:
        _require_plain_directory(self.root, "root")
        self.root.mkdir(parents=True, exist_ok=True)
        for label, path in (
            ("incomplete", self.incomplete_path),
            ("locks", self.locks_path),
            ("runs", self.runs_path),
        ):
            _require_plain_directory(path, label)
            path.mkdir(exist_ok=True)

    def begin_run(
        self,
        run_id: object,
        *,
        maximum_rows: object = MAX_RESULT_CAP,
        checkpoint: Callable[[str], None] | None = None,
    ) -> ResultRunWriter:
        identifier = _checked_run_id(run_id)
        maximum = _integer(maximum_rows, "maximum_rows", minimum=1, maximum=MAX_RESULT_CAP)
        if checkpoint is not None and not callable(checkpoint):
            raise _error("invalid-checkpoint", "checkpoint", "must be callable.")
        self._prepare_roots()
        published = self.runs_path / identifier
        if published.exists() or _is_linklike(published):
            raise _error("run-already-exists", "run_id", f"published path already exists for {identifier}.")
        lock = self.locks_path / f"{identifier}.lock"
        try:
            lock.mkdir()
        except FileExistsError:
            raise _error("run-writer-active", "run_id", f"a writer lock already exists for {identifier}.") from None
        token = uuid4().hex
        temporary = self.incomplete_path / f"{identifier}.{token}.tmp"
        files: list[BinaryIO] = []
        try:
            temporary.mkdir()
            columns = temporary / RESULT_RUN_COLUMNS_DIRECTORY
            columns.mkdir()
            for index, spec in enumerate(RESULT_SCHEMA.columns):
                files.append((columns / _column_filename(index, spec)).open("xb", buffering=0))
            return ResultRunWriter(
                store=self,
                run_id=identifier,
                maximum_rows=maximum,
                temporary_path=temporary,
                published_path=published,
                lock_path=lock,
                created_utc=_utc_now(),
                files=tuple(files),
                checkpoint=checkpoint,
            )
        except Exception:
            for file in files:
                file.close()
            _safe_remove_directory(temporary, self.incomplete_path)
            try:
                lock.rmdir()
            except FileNotFoundError:
                pass
            raise

    def open_run(self, run_id: object, *, verify_hashes: bool = False) -> CompletedResultRun:
        identifier = _checked_run_id(run_id)
        if not isinstance(verify_hashes, bool):
            raise _error("invalid-verify-hashes", "verify_hashes", "must be boolean.")
        run_path = self.runs_path / identifier
        if _is_linklike(run_path) or not run_path.is_dir():
            raise _error("completed-run-not-found", "run_id", f"no plain published directory exists for {identifier}.")
        manifest_path = run_path / RESULT_RUN_MANIFEST_NAME
        if _is_linklike(manifest_path) or not manifest_path.is_file():
            raise _error("manifest-not-found", str(manifest_path), "completed manifest is missing or unsafe.")
        try:
            raw = manifest_path.read_bytes()
            data = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _error("invalid-manifest-json", str(manifest_path), str(error)) from error
        if not isinstance(data, dict) or frozenset(data) != _MANIFEST_KEYS:
            raise _error("manifest-fields", str(manifest_path), "must contain exactly the v1 manifest fields.")
        expected_identity = {
            "formatId": RESULT_RUN_FORMAT_ID,
            "formatVersion": RESULT_RUN_FORMAT_VERSION,
            "state": "completed",
            "runId": identifier,
            "schemaId": RESULT_SCHEMA_ID,
            "schemaVersion": RESULT_SCHEMA_VERSION,
            "bytesPerRow": RESULT_ROW_BYTES,
        }
        for key, expected in expected_identity.items():
            if data[key] != expected or isinstance(data[key], bool):
                raise _error("manifest-identity", key, f"must equal {expected!r}; found {data[key]!r}.")
        row_count = _integer(data["rowCount"], "manifest.rowCount", minimum=0, maximum=MAX_RESULT_CAP)
        payload_bytes = _integer(
            data["payloadBytes"],
            "manifest.payloadBytes",
            minimum=0,
            maximum=MAX_RESULT_CAP * RESULT_ROW_BYTES,
        )
        if payload_bytes != row_count * RESULT_ROW_BYTES:
            raise _error("manifest-payload-size", "manifest.payloadBytes", "must equal rowCount * bytesPerRow.")
        if not isinstance(data["createdUtc"], str) or not isinstance(data["completedUtc"], str):
            raise _error("manifest-timestamp", "manifest", "timestamps must be text.")
        raw_columns = data["columns"]
        if not isinstance(raw_columns, list) or len(raw_columns) != len(RESULT_SCHEMA.columns):
            raise _error("manifest-columns", "manifest.columns", "must cover every schema column in order.")
        columns_path = run_path / RESULT_RUN_COLUMNS_DIRECTORY
        if _is_linklike(columns_path) or not columns_path.is_dir():
            raise _error("columns-directory", str(columns_path), "must be a plain directory.")
        columns: list[ResultRunColumn] = []
        for index, (spec, descriptor) in enumerate(zip(RESULT_SCHEMA.columns, raw_columns, strict=True)):
            if not isinstance(descriptor, dict) or frozenset(descriptor) != _COLUMN_KEYS:
                raise _error("column-manifest-fields", f"manifest.columns[{index}]", "has invalid fields.")
            filename = _column_filename(index, spec)
            relative = f"{RESULT_RUN_COLUMNS_DIRECTORY}/{filename}"
            expected = {
                "name": spec.name,
                "file": relative,
                "dtype": spec.dtype.str,
                "rowShape": list(spec.shape),
                "shape": [row_count, *spec.shape],
                "bytesPerRow": spec.bytes_per_row,
                "fileBytes": row_count * spec.bytes_per_row,
            }
            for key, value in expected.items():
                if descriptor[key] != value or isinstance(descriptor[key], bool):
                    raise _error("column-manifest-mismatch", f"manifest.columns[{index}].{key}", f"must equal {value!r}.")
            digest = descriptor["sha256"]
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise _error("column-digest", f"manifest.columns[{index}].sha256", "must be lowercase SHA-256 hex.")
            path = columns_path / filename
            if _is_linklike(path) or not path.is_file():
                raise _error("column-file-missing", str(path), "must be a plain file.")
            actual_bytes = path.stat().st_size
            if actual_bytes != descriptor["fileBytes"]:
                raise _error("column-file-size", str(path), f"expected {descriptor['fileBytes']} bytes; found {actual_bytes}.")
            if verify_hashes:
                actual_digest = _hash_file(path)
                if actual_digest != digest:
                    raise _error("column-file-digest", str(path), "does not match the completed manifest.")
            columns.append(ResultRunColumn(
                spec.name,
                path,
                spec.dtype,
                spec.shape,
                (row_count, *spec.shape),
                spec.bytes_per_row,
                actual_bytes,
                digest,
            ))
        return CompletedResultRun(
            identifier,
            run_path,
            row_count,
            RESULT_ROW_BYTES,
            payload_bytes,
            len(raw),
            data["createdUtc"],
            data["completedUtc"],
            tuple(columns),
        )

    def list_completed_runs(self) -> tuple[CompletedResultRun, ...]:
        if not self.runs_path.is_dir() or _is_linklike(self.runs_path):
            return ()
        completed = []
        for path in sorted(self.runs_path.iterdir(), key=lambda item: item.name):
            if not path.is_dir() or _is_linklike(path):
                continue
            try:
                completed.append(self.open_run(path.name, verify_hashes=False))
            except ResultRunError:
                continue
        return tuple(completed)


__all__ = [
    "RESULT_RUN_COLUMNS_DIRECTORY",
    "RESULT_RUN_FORMAT_ID",
    "RESULT_RUN_FORMAT_VERSION",
    "RESULT_RUN_MANIFEST_NAME",
    "CompletedResultRun",
    "ResultRunColumn",
    "ResultRunError",
    "ResultRunStore",
    "ResultRunStorageProjection",
    "ResultRunWriter",
    "ResultRunWriterState",
    "project_result_run_storage",
]
