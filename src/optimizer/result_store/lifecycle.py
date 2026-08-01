"""Safe result artifact lifecycle and atomic reproducibility sidecars."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from src.core.path_safety import lexical_absolute_path, path_traverses_linklike
from src.optimizer.domain import (
    FRIBBELS_VOCABULARY_SOURCE_REVISION,
    OptimizationRequest,
)
from src.optimizer.engine import (
    FRIBBELS_DERIVED_METRIC_SOURCE_REVISION,
    FRIBBELS_PRIMARY_CAP_SOURCE_REVISION,
    FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_REVISION,
    FRIBBELS_SET_SOURCE_REVISION,
)
from src.optimizer.result_store.filtering import RESULT_FILTER_ID, RESULT_FILTER_VERSION
from src.optimizer.result_store.indexing import (
    RESULT_INDEX_FILENAME,
    RESULT_INDEX_ID,
    RESULT_INDEX_MANIFEST_NAME,
    RESULT_INDEX_VERSION,
    RESULT_SORT_KEYS_BY_ID,
    ResultSortDirection,
    result_run_fingerprint,
)
from src.optimizer.result_store.resolution import (
    RESULT_RESOLUTION_ID,
    RESULT_RESOLUTION_VERSION,
    ResultResolverContext,
)
from src.optimizer.result_store.schema import RESULT_SCHEMA_ID, RESULT_SCHEMA_VERSION
from src.optimizer.result_store.storage import (
    RESULT_RUN_COLUMNS_DIRECTORY,
    RESULT_RUN_FORMAT_ID,
    RESULT_RUN_FORMAT_VERSION,
    RESULT_RUN_MANIFEST_NAME,
    CompletedResultRun,
    ResultRunError,
    ResultRunStore,
)
from src.optimizer.search.set_patterns import compile_set_pattern


RESULT_LIFECYCLE_ID = "e7.optimizer.result-lifecycle"
RESULT_LIFECYCLE_VERSION = 1
RESULT_REPRODUCIBILITY_ID = "e7.optimizer.result-reproducibility"
RESULT_REPRODUCIBILITY_VERSION = 1
RESULT_REPRODUCIBILITY_FILENAME = "reproducibility-v1.json"

DEFAULT_STALE_AFTER_SECONDS = 24 * 60 * 60
DEFAULT_KEEP_NEWEST_COMPLETED_RUNS = 2

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CACHE_KEY = re.compile(r"[0-9a-f]{64}\Z")
_INCOMPLETE_NAME = re.compile(
    r"(?P<run>[A-Za-z0-9][A-Za-z0-9._-]{0,127})\.(?P<token>[0-9a-f]{32})\.tmp\Z"
)
_CACHE_TEMP_NAME = re.compile(r"\.(?P<key>[0-9a-f]{64})\.[0-9a-f]{32}\.tmp\Z")
_EXPORT_TEMP_NAME = re.compile(r"\..+\.[0-9a-f]{32}\.e7-export\.tmp\Z")
_DATA_COMPONENT = re.compile(r"[a-z][a-z0-9.-]{0,63}\Z")

REQUIRED_DATA_VERSION_CONTRACTS: Mapping[str, tuple[str, int]] = MappingProxyType(
    {
        "artifact-catalog": ("e7.optimizer.character-source-snapshot", 1),
        "character-catalog": ("e7.optimizer.character-catalog", 1),
        "skill-context-catalog": ("e7.optimizer.character-source-snapshot", 1),
    }
)
RESULT_CONTRACT_VERSIONS: tuple[tuple[str, int], ...] = (
    (RESULT_SCHEMA_ID, RESULT_SCHEMA_VERSION),
    (RESULT_RUN_FORMAT_ID, RESULT_RUN_FORMAT_VERSION),
    (RESULT_FILTER_ID, RESULT_FILTER_VERSION),
    (RESULT_INDEX_ID, RESULT_INDEX_VERSION),
    (RESULT_RESOLUTION_ID, RESULT_RESOLUTION_VERSION),
)
ENGINE_SOURCE_REVISIONS: tuple[tuple[str, str], ...] = (
    ("derived-metrics", FRIBBELS_DERIVED_METRIC_SOURCE_REVISION),
    ("primary-caps", FRIBBELS_PRIMARY_CAP_SOURCE_REVISION),
    ("priority-normalization", FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_REVISION),
    ("set-evaluation", FRIBBELS_SET_SOURCE_REVISION),
    ("vocabulary", FRIBBELS_VOCABULARY_SOURCE_REVISION),
)

_EXPECTED_COLUMN_FILES = frozenset(
    {
        "00-dense_item_ids.bin",
        "01-owned_set_indices.bin",
        "02-category_codes.bin",
        "03-replacement_distances.bin",
        "04-effective_final_stats.bin",
        "05-raw_critical_hit_chances.bin",
        "06-derived_metrics.bin",
        "07-priority_scores.bin",
        "08-constraint_distances.bin",
        "09-equipped_item_counts.bin",
    }
)
_EXPECTED_CACHE_MANIFEST_FIELDS = frozenset(
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


class ResultLifecycleError(ValueError):
    """Actionable lifecycle, sidecar, identity, or filesystem failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> ResultLifecycleError:
    return ResultLifecycleError(code, path, message)


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _error("invalid-text", path, "must be non-empty canonical text.")
    return value


def _run_id(value: object, path: str) -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value) or value in {".", ".."}:
        raise _error("invalid-run-id", path, "must be a canonical result-run ID.")
    return value


def _sha256(value: object, path: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise _error("invalid-sha256", path, "must be lowercase SHA-256 hex.")
    return value


def _integer(value: object, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error("invalid-integer", path, "must be an integer; booleans are not accepted.")
    if value < minimum or value > maximum:
        raise _error("integer-out-of-range", path, f"must be between {minimum} and {maximum}.")
    return value


def _utc(value: object, path: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise _error("invalid-utc-time", path, "must be a timezone-aware UTC datetime.")
    return value.astimezone(UTC)


def _parse_utc(value: object, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise _error("invalid-utc-timestamp", path, "must be an ISO-8601 UTC timestamp ending in Z.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise _error("invalid-utc-timestamp", path, "is not a valid ISO-8601 UTC timestamp.") from None
    return _utc(parsed, path)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _error("invalid-json-evidence", "reproducibility", str(error)) from error


def _hash_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


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


def _plain_root(path: Path, label: str) -> bool:
    if not path.exists():
        return False
    if _is_linklike(path) or not path.is_dir():
        raise _error("unsafe-lifecycle-root", label, "must be a plain directory.")
    if path_traverses_linklike(path):
        raise _error("unsafe-lifecycle-root", label, "must not traverse a symlink or junction.")
    return True


def _safe_delete(path: Path, parent: Path) -> None:
    if not _plain_root(parent, "cleanup-parent"):
        raise _error("missing-cleanup-parent", str(parent), "disappeared during cleanup.")
    if path.parent != parent or _is_linklike(path):
        raise _error("unsafe-cleanup-path", str(path), "must be a plain direct child of its configured root.")
    resolved_parent = parent.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved.parent != resolved_parent:
        raise _error("unsafe-cleanup-path", str(path), "resolved outside its configured root.")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()
    else:
        raise _error("unsafe-cleanup-kind", str(path), "must be a plain file or directory.")


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


def inventory_snapshot_fingerprint(snapshot: object) -> str:
    try:
        groups = tuple(snapshot.items_by_slot)  # type: ignore[attr-defined]
        reverse = tuple(snapshot.dense_id_to_stable_id)  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        raise _error("invalid-inventory-snapshot", "snapshot", "must expose the dense inventory contract.") from None
    payload = {
        "denseIdToStableId": [[dense_id, stable_id] for dense_id, stable_id in reverse],
        "itemsBySlot": [
            {
                "slot": slot.value,
                "items": [item.to_dict() for item in items],
            }
            for slot, items in groups
        ],
    }
    return _hash_json(payload)


def search_snapshot_fingerprint(context: ResultResolverContext) -> str:
    if not isinstance(context, ResultResolverContext):
        raise _error("invalid-resolver-context", "context", "must be ResultResolverContext.")
    arrays = context.slot_arrays
    payload = {
        "requestId": arrays.request_id,
        "heroId": arrays.hero_id,
        "baseProfileId": arrays.base_profile_id,
        "baseStats": list(arrays.base_stats),
        "projectionMode": arrays.diagnostics.projection_mode.value,
        "denseIdToStableId": [list(item) for item in arrays.dense_id_to_stable_id],
        "slots": [
            {
                "slot": slot.slot.value,
                "denseIds": list(slot.dense_ids),
                "setIndices": list(slot.set_indices),
                "finalStatContributions": [list(row) for row in slot.final_stat_contributions],
                "gearScores": list(slot.gear_scores),
            }
            for slot in arrays.slots
        ],
    }
    return _hash_json(payload)


@dataclass(frozen=True, slots=True)
class ResultDataVersionEvidence:
    component_id: str
    schema_id: str
    schema_version: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.component_id, str) or not _DATA_COMPONENT.fullmatch(self.component_id):
            raise _error("invalid-data-component", "ResultDataVersionEvidence.component_id", "must be a canonical component ID.")
        expected = REQUIRED_DATA_VERSION_CONTRACTS.get(self.component_id)
        if expected is None:
            raise _error("unknown-data-component", "ResultDataVersionEvidence.component_id", "is not a required v1 data component.")
        schema_id = _text(self.schema_id, "ResultDataVersionEvidence.schema_id")
        version = _integer(self.schema_version, "ResultDataVersionEvidence.schema_version", 1, 1_000_000)
        if (schema_id, version) != expected:
            raise _error(
                "data-version-drift",
                f"ResultDataVersionEvidence[{self.component_id}]",
                f"must use schema/version {expected!r}.",
            )
        object.__setattr__(self, "schema_id", schema_id)
        object.__setattr__(self, "schema_version", version)
        object.__setattr__(self, "sha256", _sha256(self.sha256, "ResultDataVersionEvidence.sha256"))

    def to_dict(self) -> dict[str, object]:
        return {
            "componentId": self.component_id,
            "schemaId": self.schema_id,
            "schemaVersion": self.schema_version,
            "sha256": self.sha256,
        }


class ResultExecutionBackend(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"


@dataclass(frozen=True, slots=True)
class ResultExecutionEvidence:
    backend: ResultExecutionBackend
    implementation_id: str
    device_name: str | None = None
    runtime_version: str | None = None

    def __post_init__(self) -> None:
        try:
            backend = self.backend if isinstance(self.backend, ResultExecutionBackend) else ResultExecutionBackend(self.backend)
        except (TypeError, ValueError):
            raise _error("invalid-execution-backend", "ResultExecutionEvidence.backend", "must be cpu or cuda.") from None
        implementation = _text(self.implementation_id, "ResultExecutionEvidence.implementation_id")
        device = None if self.device_name is None else _text(self.device_name, "ResultExecutionEvidence.device_name")
        runtime = None if self.runtime_version is None else _text(self.runtime_version, "ResultExecutionEvidence.runtime_version")
        if backend is ResultExecutionBackend.CUDA and (device is None or runtime is None):
            raise _error("incomplete-cuda-evidence", "ResultExecutionEvidence", "CUDA requires device and runtime version evidence.")
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "implementation_id", implementation)
        object.__setattr__(self, "device_name", device)
        object.__setattr__(self, "runtime_version", runtime)

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend.value,
            "implementationId": self.implementation_id,
            "deviceName": self.device_name,
            "runtimeVersion": self.runtime_version,
        }


@dataclass(frozen=True, slots=True)
class ResultReproducibilityRecord:
    run_id: str
    run_fingerprint: str
    row_count: int
    created_utc: str
    completed_utc: str
    request: OptimizationRequest
    inventory_snapshot_sha256: str
    search_snapshot_sha256: str
    data_versions: tuple[ResultDataVersionEvidence, ...]
    execution: ResultExecutionEvidence
    engine_source_revisions: tuple[tuple[str, str], ...] = ENGINE_SOURCE_REVISIONS
    result_contract_versions: tuple[tuple[str, int], ...] = RESULT_CONTRACT_VERSIONS

    def __post_init__(self) -> None:
        run_id = _run_id(self.run_id, "ResultReproducibilityRecord.run_id")
        run_fingerprint = _sha256(self.run_fingerprint, "ResultReproducibilityRecord.run_fingerprint")
        row_count = _integer(self.row_count, "ResultReproducibilityRecord.row_count", 0, 5_000_000)
        _parse_utc(self.created_utc, "ResultReproducibilityRecord.created_utc")
        created = self.created_utc
        completed_value = _parse_utc(self.completed_utc, "ResultReproducibilityRecord.completed_utc")
        if completed_value < _parse_utc(created, "ResultReproducibilityRecord.created_utc"):
            raise _error("reproducibility-time-order", "ResultReproducibilityRecord.completed_utc", "must not precede creation.")
        if not isinstance(self.request, OptimizationRequest):
            raise _error("invalid-optimization-request", "ResultReproducibilityRecord.request", "must be OptimizationRequest.")
        if self.request.request_id != self.request.request_id.strip():
            raise _error("invalid-request-id", "ResultReproducibilityRecord.request", "request identity is not canonical.")
        data_versions = tuple(self.data_versions)
        if not all(isinstance(item, ResultDataVersionEvidence) for item in data_versions):
            raise _error("invalid-data-versions", "ResultReproducibilityRecord.data_versions", "must contain version evidence.")
        if tuple(item.component_id for item in data_versions) != tuple(REQUIRED_DATA_VERSION_CONTRACTS):
            raise _error(
                "data-version-coverage",
                "ResultReproducibilityRecord.data_versions",
                f"must use canonical coverage {tuple(REQUIRED_DATA_VERSION_CONTRACTS)!r}.",
            )
        if not isinstance(self.execution, ResultExecutionEvidence):
            raise _error("invalid-execution-evidence", "ResultReproducibilityRecord.execution", "must be ResultExecutionEvidence.")
        revisions = tuple(tuple(item) for item in self.engine_source_revisions)
        contracts = tuple(tuple(item) for item in self.result_contract_versions)
        if revisions != ENGINE_SOURCE_REVISIONS:
            raise _error("engine-version-drift", "ResultReproducibilityRecord.engine_source_revisions", "must match current pinned engine revisions.")
        if contracts != RESULT_CONTRACT_VERSIONS:
            raise _error("result-contract-drift", "ResultReproducibilityRecord.result_contract_versions", "must match current result contracts.")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "run_fingerprint", run_fingerprint)
        object.__setattr__(self, "row_count", row_count)
        object.__setattr__(self, "data_versions", data_versions)
        object.__setattr__(self, "inventory_snapshot_sha256", _sha256(self.inventory_snapshot_sha256, "inventory_snapshot_sha256"))
        object.__setattr__(self, "search_snapshot_sha256", _sha256(self.search_snapshot_sha256, "search_snapshot_sha256"))
        object.__setattr__(self, "engine_source_revisions", revisions)
        object.__setattr__(self, "result_contract_versions", contracts)

    @property
    def request_sha256(self) -> str:
        return _hash_json(self.request.to_dict())

    def payload_dict(self) -> dict[str, object]:
        return {
            "formatId": RESULT_REPRODUCIBILITY_ID,
            "formatVersion": RESULT_REPRODUCIBILITY_VERSION,
            "runId": self.run_id,
            "runFingerprint": self.run_fingerprint,
            "rowCount": self.row_count,
            "createdUtc": self.created_utc,
            "completedUtc": self.completed_utc,
            "selectedHeroId": self.request.hero_id,
            "baseProfileId": self.request.base_profile_id,
            "request": self.request.to_dict(),
            "requestSha256": self.request_sha256,
            "inventorySnapshotSha256": self.inventory_snapshot_sha256,
            "searchSnapshotSha256": self.search_snapshot_sha256,
            "dataVersions": [item.to_dict() for item in self.data_versions],
            "engineSourceRevisions": [
                {"componentId": component, "revision": revision}
                for component, revision in self.engine_source_revisions
            ],
            "resultContracts": [
                {"formatId": format_id, "version": version}
                for format_id, version in self.result_contract_versions
            ],
            "execution": self.execution.to_dict(),
        }


def build_result_reproducibility_record(
    run: CompletedResultRun,
    request: OptimizationRequest,
    context: ResultResolverContext,
    data_versions: tuple[ResultDataVersionEvidence, ...],
    execution: ResultExecutionEvidence,
) -> ResultReproducibilityRecord:
    if not isinstance(run, CompletedResultRun):
        raise _error("invalid-completed-run", "run", "must be CompletedResultRun.")
    if not isinstance(request, OptimizationRequest):
        raise _error("invalid-optimization-request", "request", "must be OptimizationRequest.")
    if not isinstance(context, ResultResolverContext):
        raise _error("invalid-resolver-context", "context", "must be ResultResolverContext.")
    identities = (
        ("run.run_id", run.run_id, context.run_id),
        ("request.request_id", request.request_id, context.evaluation_context.request_id),
        ("request.hero_id", request.hero_id, context.selected_hero_id),
        ("request.base_profile_id", request.base_profile_id, context.slot_arrays.base_profile_id),
    )
    for path, actual, expected in identities:
        if actual != expected:
            raise _error("reproducibility-context-mismatch", path, f"must equal {expected!r}; found {actual!r}.")
    if request.item_projection_mode is None or request.item_projection_mode is not context.slot_arrays.diagnostics.projection_mode:
        raise _error("projection-context-mismatch", "request.item_projection_mode", "must match prepared search evidence.")
    if compile_set_pattern(request.set_pattern) != context.target_pattern:
        raise _error("target-context-mismatch", "request.set_pattern", "must match the compiled active target.")
    return ResultReproducibilityRecord(
        run_id=run.run_id,
        run_fingerprint=result_run_fingerprint(run),
        row_count=run.row_count,
        created_utc=run.created_utc,
        completed_utc=run.completed_utc,
        request=request,
        inventory_snapshot_sha256=inventory_snapshot_fingerprint(context.inventory_snapshot),
        search_snapshot_sha256=search_snapshot_fingerprint(context),
        data_versions=tuple(data_versions),
        execution=execution,
    )


def validate_result_reproducibility_context(
    record: ResultReproducibilityRecord,
    run: CompletedResultRun,
    context: ResultResolverContext,
) -> None:
    if not isinstance(record, ResultReproducibilityRecord):
        raise _error("invalid-reproducibility-record", "record", "must be ResultReproducibilityRecord.")
    if not isinstance(run, CompletedResultRun) or not isinstance(context, ResultResolverContext):
        raise _error("invalid-reproducibility-context", "context", "requires completed run and resolver context.")
    expected = (
        ("run_id", record.run_id, run.run_id),
        ("context.run_id", record.run_id, context.run_id),
        ("run_fingerprint", record.run_fingerprint, result_run_fingerprint(run)),
        ("row_count", record.row_count, run.row_count),
        ("request_id", record.request.request_id, context.evaluation_context.request_id),
        ("hero_id", record.request.hero_id, context.selected_hero_id),
        ("base_profile_id", record.request.base_profile_id, context.slot_arrays.base_profile_id),
        ("inventory_snapshot_sha256", record.inventory_snapshot_sha256, inventory_snapshot_fingerprint(context.inventory_snapshot)),
        ("search_snapshot_sha256", record.search_snapshot_sha256, search_snapshot_fingerprint(context)),
    )
    for path, actual, required in expected:
        if actual != required:
            raise _error("stale-reproducibility-evidence", path, f"must equal active value {required!r}.")
    if record.request.item_projection_mode is not context.slot_arrays.diagnostics.projection_mode:
        raise _error("stale-reproducibility-evidence", "request.item_projection_mode", "does not match the active search snapshot.")
    if compile_set_pattern(record.request.set_pattern) != context.target_pattern:
        raise _error("stale-reproducibility-evidence", "request.set_pattern", "does not match the active target.")


def _record_bytes(record: ResultReproducibilityRecord) -> bytes:
    payload = record.payload_dict()
    payload["recordSha256"] = _hash_json(payload)
    return _canonical_json(payload) + b"\n"


def persist_result_reproducibility(
    run: CompletedResultRun,
    record: ResultReproducibilityRecord,
    *,
    checkpoint: Callable[[str], None] | None = None,
) -> Path:
    if not isinstance(run, CompletedResultRun) or not isinstance(record, ResultReproducibilityRecord):
        raise _error("invalid-reproducibility-persistence", "record", "requires a completed run and record.")
    if checkpoint is not None and not callable(checkpoint):
        raise _error("invalid-checkpoint", "checkpoint", "must be callable or None.")
    if record.run_id != run.run_id or record.run_fingerprint != result_run_fingerprint(run) or record.row_count != run.row_count:
        raise _error("reproducibility-run-mismatch", "record", "does not identify the supplied completed run.")
    root = run.path
    if _is_linklike(root) or not root.is_dir() or path_traverses_linklike(root):
        raise _error("unsafe-run-path", str(root), "must be the plain completed-run directory.")
    final = root / RESULT_REPRODUCIBILITY_FILENAME
    expected = _record_bytes(record)
    if final.exists() or _is_linklike(final):
        if _is_linklike(final) or not final.is_file():
            raise _error("unsafe-reproducibility-path", str(final), "must be a plain file.")
        if final.read_bytes() == expected:
            return final
        raise _error("reproducibility-already-exists", str(final), "contains different immutable evidence.")
    temporary = root / f".{RESULT_REPRODUCIBILITY_FILENAME}.{uuid.uuid4().hex}.pending"
    try:
        with temporary.open("xb") as file:
            file.write(expected)
            file.flush()
            os.fsync(file.fileno())
        if checkpoint is not None:
            checkpoint("after-reproducibility-fsync")
        try:
            os.link(temporary, final)
        except FileExistsError:
            raise _error("reproducibility-publication-conflict", str(final), "appeared before atomic publication.") from None
        temporary.unlink()
        _fsync_directory(root)
        return final
    except Exception:
        if temporary.exists() and not _is_linklike(temporary):
            temporary.unlink()
        raise


def _strict_json_object(raw: bytes, path: Path) -> dict[str, object]:
    def reject_constant(value: str) -> None:
        raise _error("invalid-reproducibility-json", str(path), f"contains invalid number {value}.")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise _error("duplicate-reproducibility-field", str(path), f"contains duplicate field {key!r}.")
            result[key] = value
        return result

    try:
        value = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=pairs)
    except ResultLifecycleError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _error("invalid-reproducibility-json", str(path), str(error)) from error
    if not isinstance(value, dict):
        raise _error("invalid-reproducibility-json", str(path), "root must be an object.")
    return value


def load_result_reproducibility(
    run: CompletedResultRun,
) -> ResultReproducibilityRecord:
    if not isinstance(run, CompletedResultRun):
        raise _error("invalid-completed-run", "run", "must be CompletedResultRun.")
    path = run.path / RESULT_REPRODUCIBILITY_FILENAME
    if _is_linklike(path) or not path.is_file():
        raise _error("reproducibility-not-found", str(path), "immutable run evidence is missing or unsafe.")
    data = _strict_json_object(path.read_bytes(), path)
    expected_fields = frozenset(
        {
            "formatId", "formatVersion", "runId", "runFingerprint", "rowCount",
            "createdUtc", "completedUtc", "selectedHeroId", "baseProfileId",
            "request", "requestSha256", "inventorySnapshotSha256",
            "searchSnapshotSha256", "dataVersions", "engineSourceRevisions",
            "resultContracts", "execution", "recordSha256",
        }
    )
    if frozenset(data) != expected_fields:
        raise _error("reproducibility-fields", str(path), "must contain exactly the v1 fields.")
    if data["formatId"] != RESULT_REPRODUCIBILITY_ID or data["formatVersion"] != RESULT_REPRODUCIBILITY_VERSION:
        raise _error("reproducibility-version", str(path), "uses an unsupported format identity or version.")
    supplied_digest = _sha256(data["recordSha256"], "record.recordSha256")
    unsigned = dict(data)
    del unsigned["recordSha256"]
    if supplied_digest != _hash_json(unsigned):
        raise _error("reproducibility-digest", str(path), "does not match its canonical payload.")
    try:
        request = OptimizationRequest.from_dict(data["request"])
    except ValueError as error:
        raise _error("invalid-persisted-request", "record.request", str(error)) from error
    if _hash_json(request.to_dict()) != data["requestSha256"]:
        raise _error("request-digest", "record.requestSha256", "does not match the canonical request.")
    if request.hero_id != data["selectedHeroId"] or request.base_profile_id != data["baseProfileId"]:
        raise _error("selected-profile-drift", "record.request", "does not match selected hero/profile evidence.")
    try:
        versions = tuple(
            ResultDataVersionEvidence(
                item["componentId"], item["schemaId"], item["schemaVersion"], item["sha256"]
            )
            for item in data["dataVersions"]  # type: ignore[union-attr]
        )
        execution_data = data["execution"]
        execution = ResultExecutionEvidence(
            execution_data["backend"],  # type: ignore[index]
            execution_data["implementationId"],  # type: ignore[index]
            execution_data["deviceName"],  # type: ignore[index]
            execution_data["runtimeVersion"],  # type: ignore[index]
        )
        revisions = tuple(
            (item["componentId"], item["revision"])
            for item in data["engineSourceRevisions"]  # type: ignore[union-attr]
        )
        contracts = tuple(
            (item["formatId"], item["version"])
            for item in data["resultContracts"]  # type: ignore[union-attr]
        )
    except (KeyError, TypeError):
        raise _error("invalid-reproducibility-shape", str(path), "contains malformed nested evidence.") from None
    record = ResultReproducibilityRecord(
        run_id=data["runId"],  # type: ignore[arg-type]
        run_fingerprint=data["runFingerprint"],  # type: ignore[arg-type]
        row_count=data["rowCount"],  # type: ignore[arg-type]
        created_utc=data["createdUtc"],  # type: ignore[arg-type]
        completed_utc=data["completedUtc"],  # type: ignore[arg-type]
        request=request,
        inventory_snapshot_sha256=data["inventorySnapshotSha256"],  # type: ignore[arg-type]
        search_snapshot_sha256=data["searchSnapshotSha256"],  # type: ignore[arg-type]
        data_versions=versions,
        execution=execution,
        engine_source_revisions=revisions,
        result_contract_versions=contracts,
    )
    if record.run_id != run.run_id or record.run_fingerprint != result_run_fingerprint(run) or record.row_count != run.row_count:
        raise _error("reproducibility-run-mismatch", str(path), "does not match the completed run.")
    return record


class LifecycleArtifactKind(StrEnum):
    COMPLETED_RUN = "completed-run"
    INCOMPLETE_RUN = "incomplete-run"
    STAGED_RUN = "staged-run"
    WRITER_LOCK = "writer-lock"
    SORT_CACHE = "sort-cache"
    SORT_CACHE_TEMPORARY = "sort-cache-temporary"
    EXPORT_TEMPORARY = "export-temporary"


class LifecycleDisposition(StrEnum):
    REMOVED = "removed"
    ELIGIBLE_DRY_RUN = "eligible-dry-run"
    PROTECTED_ACTIVE = "protected-active"
    PROTECTED_RETENTION = "protected-retention"
    TOO_YOUNG = "too-young"


@dataclass(frozen=True, slots=True)
class ResultLifecyclePolicy:
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    keep_newest_completed_runs: int = DEFAULT_KEEP_NEWEST_COMPLETED_RUNS

    def __post_init__(self) -> None:
        object.__setattr__(self, "stale_after_seconds", _integer(self.stale_after_seconds, "ResultLifecyclePolicy.stale_after_seconds", 0, 10 * 365 * 24 * 60 * 60))
        object.__setattr__(self, "keep_newest_completed_runs", _integer(self.keep_newest_completed_runs, "ResultLifecyclePolicy.keep_newest_completed_runs", 0, 1_000))


@dataclass(frozen=True, slots=True)
class ResultLifecycleRequest:
    now_utc: datetime
    active_run_ids: tuple[str, ...] = ()
    active_index_cache_keys: tuple[str, ...] = ()
    active_export_temporary_names: tuple[str, ...] = ()
    policy: ResultLifecyclePolicy = ResultLifecyclePolicy()
    dry_run: bool = True
    lifecycle_id: str = RESULT_LIFECYCLE_ID
    version: int = RESULT_LIFECYCLE_VERSION

    def __post_init__(self) -> None:
        if self.lifecycle_id != RESULT_LIFECYCLE_ID or self.version != RESULT_LIFECYCLE_VERSION:
            raise _error("lifecycle-version", "ResultLifecycleRequest", "uses an unsupported identity or version.")
        object.__setattr__(self, "now_utc", _utc(self.now_utc, "ResultLifecycleRequest.now_utc"))
        runs = tuple(sorted({_run_id(item, "ResultLifecycleRequest.active_run_ids") for item in self.active_run_ids}))
        keys = tuple(sorted(set(self.active_index_cache_keys)))
        if any(not isinstance(item, str) or not _CACHE_KEY.fullmatch(item) for item in keys):
            raise _error("invalid-cache-key", "ResultLifecycleRequest.active_index_cache_keys", "must contain lowercase SHA-256 keys.")
        export_names = tuple(sorted(set(self.active_export_temporary_names)))
        if any(not isinstance(item, str) or not _EXPORT_TEMP_NAME.fullmatch(item) for item in export_names):
            raise _error("invalid-export-temporary-name", "ResultLifecycleRequest.active_export_temporary_names", "must contain owned export temporary basenames.")
        if not isinstance(self.policy, ResultLifecyclePolicy) or not isinstance(self.dry_run, bool):
            raise _error("invalid-lifecycle-request", "ResultLifecycleRequest", "requires policy and boolean dry_run.")
        object.__setattr__(self, "active_run_ids", runs)
        object.__setattr__(self, "active_index_cache_keys", keys)
        object.__setattr__(self, "active_export_temporary_names", export_names)
        object.__setattr__(self, "dry_run", self.dry_run)


@dataclass(frozen=True, slots=True)
class ResultLifecycleAction:
    kind: LifecycleArtifactKind
    path: Path
    age_seconds: int
    disposition: LifecycleDisposition


@dataclass(frozen=True, slots=True)
class ResultLifecycleReport:
    actions: tuple[ResultLifecycleAction, ...]
    preserved_unknown_artifacts: int

    @property
    def scanned_artifacts(self) -> int:
        return len(self.actions) + self.preserved_unknown_artifacts

    @property
    def removed_artifacts(self) -> int:
        return sum(item.disposition is LifecycleDisposition.REMOVED for item in self.actions)

    @property
    def eligible_artifacts(self) -> int:
        return sum(item.disposition in {LifecycleDisposition.REMOVED, LifecycleDisposition.ELIGIBLE_DRY_RUN} for item in self.actions)


class ResultLifecycleManager:
    """Explicit-root cleanup manager; construction performs no filesystem work."""

    def __init__(
        self,
        result_store_root: str | os.PathLike[str],
        sort_cache_root: str | os.PathLike[str],
        *,
        export_roots: tuple[str | os.PathLike[str], ...] = (),
    ) -> None:
        if not isinstance(result_store_root, (str, os.PathLike)) or not isinstance(sort_cache_root, (str, os.PathLike)):
            raise _error("invalid-lifecycle-root", "root", "must be explicit filesystem paths.")
        self.result_store_root = lexical_absolute_path(result_store_root)
        self.sort_cache_root = lexical_absolute_path(sort_cache_root)
        self.export_roots = tuple(lexical_absolute_path(item) for item in export_roots)

    @staticmethod
    def _age(path: Path, request: ResultLifecycleRequest, timestamp: datetime | None = None) -> int:
        moment = datetime.fromtimestamp(path.stat().st_mtime, UTC) if timestamp is None else timestamp
        return max(0, int((request.now_utc - moment).total_seconds()))

    @staticmethod
    def _owned_transaction(path: Path, *, staged: bool) -> bool:
        allowed_root = {RESULT_RUN_COLUMNS_DIRECTORY}
        if staged:
            allowed_root.add(f"{RESULT_RUN_MANIFEST_NAME}.pending")
        entries = tuple(path.iterdir())
        if any(item.name not in allowed_root or _is_linklike(item) for item in entries):
            return False
        columns = path / RESULT_RUN_COLUMNS_DIRECTORY
        pending = path / f"{RESULT_RUN_MANIFEST_NAME}.pending"
        if staged and pending.exists() and (not pending.is_file() or _is_linklike(pending)):
            return False
        if not columns.exists():
            return not entries
        if not columns.is_dir() or _is_linklike(columns):
            return False
        names = {item.name for item in columns.iterdir()}
        return names <= _EXPECTED_COLUMN_FILES and all(item.is_file() and not _is_linklike(item) for item in columns.iterdir())

    @staticmethod
    def _owned_cache_temp(path: Path) -> bool:
        allowed = {RESULT_INDEX_FILENAME, RESULT_INDEX_MANIFEST_NAME, f"{RESULT_INDEX_MANIFEST_NAME}.pending"}
        return all(item.name in allowed and item.is_file() and not _is_linklike(item) for item in path.iterdir())

    @staticmethod
    def _owned_completed_run(path: Path) -> bool:
        allowed = {
            RESULT_RUN_MANIFEST_NAME,
            RESULT_RUN_COLUMNS_DIRECTORY,
            RESULT_REPRODUCIBILITY_FILENAME,
        }
        entries = tuple(path.iterdir())
        if any(item.name not in allowed or _is_linklike(item) for item in entries):
            return False
        columns = path / RESULT_RUN_COLUMNS_DIRECTORY
        if not columns.is_dir() or _is_linklike(columns):
            return False
        column_entries = tuple(columns.iterdir())
        if {item.name for item in column_entries} != _EXPECTED_COLUMN_FILES:
            return False
        return all(item.is_file() and not _is_linklike(item) for item in column_entries)

    @staticmethod
    def _cache_completion(path: Path) -> datetime | None:
        manifest = path / RESULT_INDEX_MANIFEST_NAME
        index = path / RESULT_INDEX_FILENAME
        entries = tuple(path.iterdir())
        if {item.name for item in entries} != {RESULT_INDEX_MANIFEST_NAME, RESULT_INDEX_FILENAME}:
            return None
        if any(_is_linklike(item) or not item.is_file() for item in entries):
            return None
        if _is_linklike(manifest) or _is_linklike(index) or not manifest.is_file() or not index.is_file():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or frozenset(data) != _EXPECTED_CACHE_MANIFEST_FIELDS:
            return None
        try:
            row_count = data["rowCount"]
            index_bytes = data["indexBytes"]
            if (
                data["formatId"] != RESULT_INDEX_ID
                or data["formatVersion"] != RESULT_INDEX_VERSION
                or data["cacheKey"] != path.name
                or not isinstance(data["runFingerprint"], str)
                or not _SHA256.fullmatch(data["runFingerprint"])
                or not isinstance(data["viewFingerprint"], str)
                or not data["viewFingerprint"]
                or data["sortKey"] not in RESULT_SORT_KEYS_BY_ID
                or data["direction"] not in {item.value for item in ResultSortDirection}
                or isinstance(row_count, bool)
                or not isinstance(row_count, int)
                or row_count < 0
                or row_count > 5_000_000
                or data["indexFile"] != RESULT_INDEX_FILENAME
                or isinstance(index_bytes, bool)
                or not isinstance(index_bytes, int)
                or index_bytes != row_count * 4
                or index.stat().st_size != index_bytes
                or not isinstance(data["indexSha256"], str)
                or not _SHA256.fullmatch(data["indexSha256"])
                or _hash_file(index) != data["indexSha256"]
            ):
                return None
            return _parse_utc(data.get("completedUtc"), "cache.completedUtc")
        except (OSError, ResultLifecycleError):
            return None

    def clean(self, request: ResultLifecycleRequest) -> ResultLifecycleReport:
        if not isinstance(request, ResultLifecycleRequest):
            raise _error("invalid-lifecycle-request", "request", "must be ResultLifecycleRequest.")
        actions: list[ResultLifecycleAction] = []
        unknown = 0
        policy = request.policy

        def consider(kind: LifecycleArtifactKind, path: Path, parent: Path, age: int, active: bool, retained: bool = False) -> None:
            if active:
                disposition = LifecycleDisposition.PROTECTED_ACTIVE
            elif retained:
                disposition = LifecycleDisposition.PROTECTED_RETENTION
            elif age < policy.stale_after_seconds:
                disposition = LifecycleDisposition.TOO_YOUNG
            elif request.dry_run:
                disposition = LifecycleDisposition.ELIGIBLE_DRY_RUN
            else:
                _safe_delete(path, parent)
                disposition = LifecycleDisposition.REMOVED
            actions.append(ResultLifecycleAction(kind, path, age, disposition))

        store = ResultRunStore(self.result_store_root)
        completed: list[tuple[CompletedResultRun, Path, datetime]] = []
        if _plain_root(self.result_store_root, "result-store-root"):
            for namespace in (store.incomplete_path, store.locks_path, store.runs_path):
                if namespace.exists() and (not namespace.is_dir() or _is_linklike(namespace)):
                    raise _error("unsafe-lifecycle-namespace", str(namespace), "must be a plain directory.")

            if store.runs_path.is_dir():
                for path in sorted(store.runs_path.iterdir(), key=lambda item: item.name):
                    if _is_linklike(path):
                        raise _error("unsafe-lifecycle-artifact", str(path), "recognized namespaces must not contain links.")
                    if not path.is_dir() or not _RUN_ID.fullmatch(path.name):
                        unknown += 1
                        continue
                    manifest = path / RESULT_RUN_MANIFEST_NAME
                    if manifest.is_file() and not _is_linklike(manifest):
                        try:
                            if not self._owned_completed_run(path):
                                raise _error("unknown-run-artifact", str(path), "contains files outside the owned completed-run layout.")
                            run = store.open_run(path.name, verify_hashes=False)
                            completed.append((run, path, _parse_utc(run.completed_utc, "run.completed_utc")))
                        except (ResultRunError, ResultLifecycleError):
                            unknown += 1
                        continue
                    if not manifest.exists() and self._owned_transaction(path, staged=True):
                        age = self._age(path, request)
                        consider(LifecycleArtifactKind.STAGED_RUN, path, store.runs_path, age, path.name in request.active_run_ids)
                    else:
                        unknown += 1

            retained_ids = {
                item[0].run_id
                for item in sorted(completed, key=lambda item: (item[2], item[0].run_id), reverse=True)[
                    : policy.keep_newest_completed_runs
                ]
            }
            for run, path, completed_at in sorted(completed, key=lambda item: item[0].run_id):
                consider(
                    LifecycleArtifactKind.COMPLETED_RUN,
                    path,
                    store.runs_path,
                    self._age(path, request, completed_at),
                    run.run_id in request.active_run_ids,
                    run.run_id in retained_ids,
                )

            if store.incomplete_path.is_dir():
                for path in sorted(store.incomplete_path.iterdir(), key=lambda item: item.name):
                    match = _INCOMPLETE_NAME.fullmatch(path.name)
                    if _is_linklike(path):
                        raise _error("unsafe-lifecycle-artifact", str(path), "incomplete artifacts must not be links.")
                    if match and path.is_dir() and self._owned_transaction(path, staged=False):
                        consider(
                            LifecycleArtifactKind.INCOMPLETE_RUN,
                            path,
                            store.incomplete_path,
                            self._age(path, request),
                            match.group("run") in request.active_run_ids,
                        )
                    else:
                        unknown += 1

            if store.locks_path.is_dir():
                for path in sorted(store.locks_path.iterdir(), key=lambda item: item.name):
                    run_name = path.name[:-5] if path.name.endswith(".lock") else ""
                    if _is_linklike(path):
                        raise _error("unsafe-lifecycle-artifact", str(path), "writer locks must not be links.")
                    if path.is_dir() and _RUN_ID.fullmatch(run_name) and not any(path.iterdir()):
                        consider(
                            LifecycleArtifactKind.WRITER_LOCK,
                            path,
                            store.locks_path,
                            self._age(path, request),
                            run_name in request.active_run_ids,
                        )
                    else:
                        unknown += 1

        if _plain_root(self.sort_cache_root, "sort-cache-root"):
            for path in sorted(self.sort_cache_root.iterdir(), key=lambda item: item.name):
                if _is_linklike(path):
                    raise _error("unsafe-lifecycle-artifact", str(path), "sort-cache artifacts must not be links.")
                temp_match = _CACHE_TEMP_NAME.fullmatch(path.name)
                if temp_match and path.is_dir() and self._owned_cache_temp(path):
                    consider(
                        LifecycleArtifactKind.SORT_CACHE_TEMPORARY,
                        path,
                        self.sort_cache_root,
                        self._age(path, request),
                        temp_match.group("key") in request.active_index_cache_keys,
                    )
                    continue
                if path.is_dir() and _CACHE_KEY.fullmatch(path.name):
                    completed_at = self._cache_completion(path)
                    if completed_at is None:
                        unknown += 1
                    else:
                        consider(
                            LifecycleArtifactKind.SORT_CACHE,
                            path,
                            self.sort_cache_root,
                            self._age(path, request, completed_at),
                            path.name in request.active_index_cache_keys,
                        )
                    continue
                unknown += 1

        for root in self.export_roots:
            if not _plain_root(root, "export-root"):
                continue
            for path in sorted(root.iterdir(), key=lambda item: item.name):
                if _is_linklike(path) and _EXPORT_TEMP_NAME.fullmatch(path.name):
                    raise _error("unsafe-lifecycle-artifact", str(path), "export temporaries must not be links.")
                if path.is_file() and _EXPORT_TEMP_NAME.fullmatch(path.name):
                    consider(
                        LifecycleArtifactKind.EXPORT_TEMPORARY,
                        path,
                        root,
                        self._age(path, request),
                        path.name in request.active_export_temporary_names,
                    )

        return ResultLifecycleReport(tuple(actions), unknown)


__all__ = [
    "DEFAULT_KEEP_NEWEST_COMPLETED_RUNS",
    "DEFAULT_STALE_AFTER_SECONDS",
    "ENGINE_SOURCE_REVISIONS",
    "LifecycleArtifactKind",
    "LifecycleDisposition",
    "REQUIRED_DATA_VERSION_CONTRACTS",
    "RESULT_LIFECYCLE_ID",
    "RESULT_LIFECYCLE_VERSION",
    "RESULT_REPRODUCIBILITY_FILENAME",
    "RESULT_REPRODUCIBILITY_ID",
    "RESULT_REPRODUCIBILITY_VERSION",
    "RESULT_CONTRACT_VERSIONS",
    "ResultDataVersionEvidence",
    "ResultExecutionBackend",
    "ResultExecutionEvidence",
    "ResultLifecycleAction",
    "ResultLifecycleError",
    "ResultLifecycleManager",
    "ResultLifecyclePolicy",
    "ResultLifecycleReport",
    "ResultLifecycleRequest",
    "ResultReproducibilityRecord",
    "build_result_reproducibility_record",
    "inventory_snapshot_fingerprint",
    "load_result_reproducibility",
    "persist_result_reproducibility",
    "search_snapshot_fingerprint",
    "validate_result_reproducibility_context",
]
