"""Pinned, offline Fribbels character-source snapshot generation and loading.

The Phase 00 character catalog intentionally contains only calculation inputs.
This module keeps the richer Fribbels records in a separately versioned,
recursively immutable sidecar and emits a record-by-record normalization report.
No network access or implicit clock is used here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from src.optimizer.data.schema_common import (
    FrozenJsonObject,
    SchemaValidationError,
    SourceMetadata,
    deterministic_json,
    document_object,
    freeze_json_object,
    load_versioned_document,
    migration_registry,
    parse_json_document,
    required_text,
    sha256_checksum,
    thaw_json,
    unique_values,
    utc_timestamp,
)
from src.optimizer.data.schemas import CharacterCatalogDocument, load_character_catalog_json
from src.optimizer.domain import ArtifactDefinition, FinalStat, HeroBaseProfile, HeroDefinition


FRIBBELS_CHARACTER_REPOSITORY = "RexQian/Fribbels-Epic-7-Optimizer"
FRIBBELS_CHARACTER_REPOSITORY_URL = (
    "https://github.com/RexQian/Fribbels-Epic-7-Optimizer"
)
FRIBBELS_CHARACTER_SOURCE_REVISION = "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb"
FRIBBELS_CHARACTER_SOURCE_REVISION_URL = (
    f"{FRIBBELS_CHARACTER_REPOSITORY_URL}/commit/{FRIBBELS_CHARACTER_SOURCE_REVISION}"
)
FRIBBELS_CHARACTER_SOURCE_BRANCH = "feat/offline"
FRIBBELS_CHARACTER_SOURCE_VERSION = "patch-20260716"
FRIBBELS_OFFLINE_APP_VERSION = "1.11.0-offline.20260108"
FRIBBELS_LAST_CACHE_UPDATE_REVISION = "dab0509584b1405aa13f5e1ddbfea9d919269fe8"
FRIBBELS_LAST_CACHE_UPDATE_AT = "2026-07-16T15:29:30Z"

FRIBBELS_HERO_SOURCE_PATH = "data/cache/herodata.json"
FRIBBELS_ARTIFACT_SOURCE_PATH = "data/cache/artifactdata.json"
FRIBBELS_HERO_GIT_BLOB_SHA1 = "2364040f3d9424653877745fc2883ecdd632afb5"
FRIBBELS_ARTIFACT_GIT_BLOB_SHA1 = "3cf3f766b1117caac64e5d7da9f4fc8b42781054"
FRIBBELS_HERO_SHA256 = "a5ed0b641e578a2b290b75d6f75a866a93b91e40c1064a4f1a264630a745c349"
FRIBBELS_ARTIFACT_SHA256 = "ed1bb666ae7465560fbc1a163000966821174b0a48be826b28da16021f463ac0"

CHARACTER_SNAPSHOT_GENERATOR_ID = "e7.optimizer.character-snapshot-generator"
CHARACTER_SNAPSHOT_GENERATOR_VERSION = "1.0.0"
CHARACTER_SOURCE_SCHEMA_ID = "e7.optimizer.character-source-snapshot"
CHARACTER_SOURCE_CURRENT_VERSION = 1
CHARACTER_VALIDATION_SCHEMA_ID = "e7.optimizer.character-normalization-report"
CHARACTER_VALIDATION_CURRENT_VERSION = 1
CHARACTER_MANIFEST_SCHEMA_ID = "e7.optimizer.character-snapshot-manifest"
CHARACTER_MANIFEST_CURRENT_VERSION = 1

CHARACTER_SOURCE_MIGRATIONS = migration_registry({})
CHARACTER_VALIDATION_MIGRATIONS = migration_registry({})
CHARACTER_MANIFEST_MIGRATIONS = migration_registry({})

BUNDLED_CHARACTER_DATA_DIRECTORY = Path(__file__).with_name("character_data")
BUNDLED_CATALOG_FILENAME = "character-catalog-v1.json"
BUNDLED_SOURCE_FILENAME = "character-source-v1.json"
BUNDLED_VALIDATION_FILENAME = "character-validation-v1.json"
BUNDLED_MANIFEST_FILENAME = "manifest-v1.json"
BUNDLED_HERO_SOURCE_PATH = "source/herodata.json"
BUNDLED_ARTIFACT_SOURCE_PATH = "source/artifactdata.json"

RecordKind = Literal["hero", "artifact"]
OutcomeStatus = Literal["normalized", "rejected"]

_GIT_SHA1_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
_HERO_SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PROFILE_SPECS = (
    ("lv50FiveStarFullyAwakened", 50, 5, "Level 50 / 5 star / fully awakened"),
    ("lv60SixStarFullyAwakened", 60, 6, "Level 60 / 6 star / fully awakened"),
)
_FINAL_STAT_SOURCE_FIELDS = (
    (FinalStat.ATTACK, "atk", False),
    (FinalStat.HEALTH, "hp", False),
    (FinalStat.DEFENSE, "def", False),
    (FinalStat.SPEED, "spd", False),
    (FinalStat.CRITICAL_HIT_CHANCE, "chc", True),
    (FinalStat.CRITICAL_HIT_DAMAGE, "chd", True),
    (FinalStat.EFFECTIVENESS, "eff", True),
    (FinalStat.EFFECT_RESISTANCE, "efr", True),
)


def _relative_path(value: object, field: str) -> str:
    text = required_text(value, field)
    if "\\" in text:
        raise SchemaValidationError(f"{field} must use forward slashes.")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SchemaValidationError(f"{field} must be a normalized relative path.")
    return path.as_posix()


def _nonnegative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaValidationError(f"{field} must be a non-negative integer.")
    return value


def _git_sha1(value: object, field: str) -> str:
    text = required_text(value, field)
    if not _GIT_SHA1_PATTERN.fullmatch(text):
        raise SchemaValidationError(f"{field} must be a 40-character hexadecimal Git SHA-1.")
    return text.lower()


@dataclass(frozen=True, slots=True)
class CharacterSourceInput:
    record_kind: RecordKind
    source_path: str
    bundled_path: str
    sha256: str
    git_blob_sha1: str
    byte_length: int
    record_count: int

    def __post_init__(self) -> None:
        if self.record_kind not in {"hero", "artifact"}:
            raise SchemaValidationError("Character source input recordKind must be hero or artifact.")
        object.__setattr__(self, "source_path", _relative_path(self.source_path, "Character source input sourcePath"))
        object.__setattr__(self, "bundled_path", _relative_path(self.bundled_path, "Character source input bundledPath"))
        object.__setattr__(self, "sha256", sha256_checksum(self.sha256, "Character source input sha256"))
        object.__setattr__(self, "git_blob_sha1", _git_sha1(self.git_blob_sha1, "Character source input gitBlobSha1"))
        object.__setattr__(self, "byte_length", _nonnegative_integer(self.byte_length, "Character source input byteLength"))
        object.__setattr__(self, "record_count", _nonnegative_integer(self.record_count, "Character source input recordCount"))

    def to_dict(self) -> dict[str, object]:
        return {
            "recordKind": self.record_kind,
            "sourcePath": self.source_path,
            "bundledPath": self.bundled_path,
            "sha256": self.sha256,
            "gitBlobSha1": self.git_blob_sha1,
            "byteLength": self.byte_length,
            "recordCount": self.record_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CharacterSourceInput":
        data = document_object(
            value,
            "Character source input",
            required=(
                "recordKind", "sourcePath", "bundledPath", "sha256",
                "gitBlobSha1", "byteLength", "recordCount",
            ),
        )
        return cls(
            record_kind=data["recordKind"],
            source_path=data["sourcePath"],
            bundled_path=data["bundledPath"],
            sha256=data["sha256"],
            git_blob_sha1=data["gitBlobSha1"],
            byte_length=data["byteLength"],
            record_count=data["recordCount"],
        )


@dataclass(frozen=True, slots=True)
class CharacterSourceSnapshotDocument:
    snapshot_id: str
    generated_at: str
    generator_id: str
    generator_version: str
    source: SourceMetadata
    inputs: tuple[CharacterSourceInput, ...]
    heroes: FrozenJsonObject | Mapping[str, object]
    artifacts: FrozenJsonObject | Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceMetadata):
            raise SchemaValidationError("Character source snapshot source must be SourceMetadata.")
        inputs = tuple(self.inputs)
        if len(inputs) != 2 or {item.record_kind for item in inputs} != {"hero", "artifact"}:
            raise SchemaValidationError("Character source snapshot must contain one hero and one artifact input.")
        unique_values([item.source_path for item in inputs], "Character source snapshot input source paths")
        unique_values([item.bundled_path for item in inputs], "Character source snapshot bundled paths")
        heroes = freeze_json_object(self.heroes, "Character source snapshot heroes")
        artifacts = freeze_json_object(self.artifacts, "Character source snapshot artifacts")
        expected_counts = {item.record_kind: item.record_count for item in inputs}
        if len(heroes) != expected_counts["hero"] or len(artifacts) != expected_counts["artifact"]:
            raise SchemaValidationError("Character source snapshot record counts do not match its inputs.")
        object.__setattr__(self, "snapshot_id", required_text(self.snapshot_id, "Character source snapshot snapshotId"))
        object.__setattr__(self, "generated_at", utc_timestamp(self.generated_at, "Character source snapshot generatedAt"))
        object.__setattr__(self, "generator_id", required_text(self.generator_id, "Character source snapshot generatorId"))
        object.__setattr__(self, "generator_version", required_text(self.generator_version, "Character source snapshot generatorVersion"))
        object.__setattr__(self, "inputs", tuple(sorted(inputs, key=lambda item: item.record_kind)))
        object.__setattr__(self, "heroes", heroes)
        object.__setattr__(self, "artifacts", artifacts)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaId": CHARACTER_SOURCE_SCHEMA_ID,
            "schemaVersion": CHARACTER_SOURCE_CURRENT_VERSION,
            "snapshotId": self.snapshot_id,
            "generatedAt": self.generated_at,
            "generatorId": self.generator_id,
            "generatorVersion": self.generator_version,
            "source": self.source.to_dict(),
            "inputs": [item.to_dict() for item in self.inputs],
            "records": {
                "heroes": thaw_json(self.heroes),
                "artifacts": thaw_json(self.artifacts),
            },
        }

    def to_json(self) -> str:
        return deterministic_json(self)

    @classmethod
    def from_current_dict(cls, value: Mapping[str, Any]) -> "CharacterSourceSnapshotDocument":
        data = document_object(
            value,
            "Character source snapshot",
            required=(
                "schemaId", "schemaVersion", "snapshotId", "generatedAt",
                "generatorId", "generatorVersion", "source", "inputs", "records",
            ),
        )
        records = document_object(
            data["records"], "Character source snapshot records", required=("heroes", "artifacts")
        )
        if isinstance(data["inputs"], (str, bytes, bytearray, Mapping)):
            raise SchemaValidationError("Character source snapshot inputs must be an array.")
        return cls(
            snapshot_id=data["snapshotId"],
            generated_at=data["generatedAt"],
            generator_id=data["generatorId"],
            generator_version=data["generatorVersion"],
            source=SourceMetadata.from_dict(data["source"], family="Character source snapshot"),
            inputs=tuple(CharacterSourceInput.from_dict(item) for item in data["inputs"]),
            heroes=records["heroes"],
            artifacts=records["artifacts"],
        )


@dataclass(frozen=True, slots=True)
class NormalizationRejection:
    code: str
    path: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", required_text(self.code, "Normalization rejection code"))
        object.__setattr__(self, "path", required_text(self.path, "Normalization rejection path"))
        object.__setattr__(self, "reason", required_text(self.reason, "Normalization rejection reason"))

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "reason": self.reason}

    @classmethod
    def from_dict(cls, value: object) -> "NormalizationRejection":
        data = document_object(value, "Normalization rejection", required=("code", "path", "reason"))
        return cls(code=data["code"], path=data["path"], reason=data["reason"])


@dataclass(frozen=True, slots=True)
class CharacterNormalizationOutcome:
    record_kind: RecordKind
    source_path: str
    source_key: str
    source_index: int
    status: OutcomeStatus
    canonical_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    rejection: NormalizationRejection | None = None

    def __post_init__(self) -> None:
        if self.record_kind not in {"hero", "artifact"}:
            raise SchemaValidationError("Normalization outcome recordKind must be hero or artifact.")
        if self.status not in {"normalized", "rejected"}:
            raise SchemaValidationError("Normalization outcome status must be normalized or rejected.")
        canonical_ids = tuple(required_text(value, "Normalization outcome canonicalId") for value in self.canonical_ids)
        warnings = tuple(required_text(value, "Normalization outcome warning") for value in self.warnings)
        unique_values(canonical_ids, "Normalization outcome canonical IDs")
        unique_values(warnings, "Normalization outcome warnings")
        if self.status == "normalized" and (not canonical_ids or self.rejection is not None):
            raise SchemaValidationError("A normalized outcome requires canonical IDs and no rejection.")
        if self.status == "rejected" and (canonical_ids or not isinstance(self.rejection, NormalizationRejection)):
            raise SchemaValidationError("A rejected outcome requires one rejection and no canonical IDs.")
        object.__setattr__(self, "source_path", _relative_path(self.source_path, "Normalization outcome sourcePath"))
        object.__setattr__(self, "source_key", required_text(self.source_key, "Normalization outcome sourceKey"))
        object.__setattr__(self, "source_index", _nonnegative_integer(self.source_index, "Normalization outcome sourceIndex"))
        object.__setattr__(self, "canonical_ids", canonical_ids)
        object.__setattr__(self, "warnings", tuple(sorted(warnings)))

    def to_dict(self) -> dict[str, object]:
        return {
            "recordKind": self.record_kind,
            "sourcePath": self.source_path,
            "sourceKey": self.source_key,
            "sourceIndex": self.source_index,
            "status": self.status,
            "canonicalIds": list(self.canonical_ids),
            "warnings": list(self.warnings),
            "rejection": None if self.rejection is None else self.rejection.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CharacterNormalizationOutcome":
        data = document_object(
            value,
            "Character normalization outcome",
            required=(
                "recordKind", "sourcePath", "sourceKey", "sourceIndex",
                "status", "canonicalIds", "warnings", "rejection",
            ),
        )
        for field in ("canonicalIds", "warnings"):
            if isinstance(data[field], (str, bytes, bytearray, Mapping)):
                raise SchemaValidationError(f"Character normalization outcome {field} must be an array.")
        rejection = data["rejection"]
        return cls(
            record_kind=data["recordKind"],
            source_path=data["sourcePath"],
            source_key=data["sourceKey"],
            source_index=data["sourceIndex"],
            status=data["status"],
            canonical_ids=tuple(data["canonicalIds"]),
            warnings=tuple(data["warnings"]),
            rejection=None if rejection is None else NormalizationRejection.from_dict(rejection),
        )


def _report_summary(outcomes: Sequence[CharacterNormalizationOutcome]) -> dict[str, int]:
    normalized = [item for item in outcomes if item.status == "normalized"]
    rejected = [item for item in outcomes if item.status == "rejected"]
    heroes = [item for item in outcomes if item.record_kind == "hero"]
    artifacts = [item for item in outcomes if item.record_kind == "artifact"]
    return {
        "sourceRecords": len(outcomes),
        "sourceHeroRecords": len(heroes),
        "sourceArtifactRecords": len(artifacts),
        "normalizedRecords": len(normalized),
        "normalizedHeroRecords": sum(item.status == "normalized" for item in heroes),
        "normalizedArtifactRecords": sum(item.status == "normalized" for item in artifacts),
        "rejectedRecords": len(rejected),
        "rejectedHeroRecords": sum(item.status == "rejected" for item in heroes),
        "rejectedArtifactRecords": sum(item.status == "rejected" for item in artifacts),
        "canonicalHeroes": sum(item.status == "normalized" for item in heroes),
        "canonicalProfiles": sum(
            canonical_id.startswith("profile.")
            for item in normalized
            for canonical_id in item.canonical_ids
        ),
        "canonicalArtifacts": sum(item.status == "normalized" for item in artifacts),
        "warningCount": sum(len(item.warnings) for item in outcomes),
        "warningRecords": sum(bool(item.warnings) for item in outcomes),
    }


@dataclass(frozen=True, slots=True)
class CharacterNormalizationReportDocument:
    report_id: str
    snapshot_id: str
    generated_at: str
    generator_id: str
    generator_version: str
    inputs: tuple[CharacterSourceInput, ...]
    outcomes: tuple[CharacterNormalizationOutcome, ...]

    def __post_init__(self) -> None:
        inputs = tuple(self.inputs)
        outcomes = tuple(self.outcomes)
        if len(inputs) != 2 or {item.record_kind for item in inputs} != {"hero", "artifact"}:
            raise SchemaValidationError("Character normalization report must contain one hero and one artifact input.")
        source_keys = [(item.record_kind, item.source_key) for item in outcomes]
        unique_values(source_keys, "Character normalization report source records")
        canonical_ids = [canonical_id for item in outcomes for canonical_id in item.canonical_ids]
        unique_values(canonical_ids, "Character normalization report canonical IDs")
        counts = Counter(item.record_kind for item in outcomes)
        expected = {item.record_kind: item.record_count for item in inputs}
        if counts != Counter(expected):
            raise SchemaValidationError("Character normalization report does not account for every input record exactly once.")
        for kind in ("hero", "artifact"):
            indexes = sorted(item.source_index for item in outcomes if item.record_kind == kind)
            if indexes != list(range(expected[kind])):
                raise SchemaValidationError(f"Character normalization report {kind} indexes must be contiguous and unique.")
        object.__setattr__(self, "report_id", required_text(self.report_id, "Character normalization report reportId"))
        object.__setattr__(self, "snapshot_id", required_text(self.snapshot_id, "Character normalization report snapshotId"))
        object.__setattr__(self, "generated_at", utc_timestamp(self.generated_at, "Character normalization report generatedAt"))
        object.__setattr__(self, "generator_id", required_text(self.generator_id, "Character normalization report generatorId"))
        object.__setattr__(self, "generator_version", required_text(self.generator_version, "Character normalization report generatorVersion"))
        object.__setattr__(self, "inputs", tuple(sorted(inputs, key=lambda item: item.record_kind)))
        object.__setattr__(self, "outcomes", tuple(sorted(outcomes, key=lambda item: (item.record_kind, item.source_index))))

    @property
    def summary(self) -> Mapping[str, int]:
        return _report_summary(self.outcomes)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaId": CHARACTER_VALIDATION_SCHEMA_ID,
            "schemaVersion": CHARACTER_VALIDATION_CURRENT_VERSION,
            "reportId": self.report_id,
            "snapshotId": self.snapshot_id,
            "generatedAt": self.generated_at,
            "generatorId": self.generator_id,
            "generatorVersion": self.generator_version,
            "inputs": [item.to_dict() for item in self.inputs],
            "summary": dict(self.summary),
            "outcomes": [item.to_dict() for item in self.outcomes],
        }

    def to_json(self) -> str:
        return deterministic_json(self)

    @classmethod
    def from_current_dict(cls, value: Mapping[str, Any]) -> "CharacterNormalizationReportDocument":
        data = document_object(
            value,
            "Character normalization report",
            required=(
                "schemaId", "schemaVersion", "reportId", "snapshotId", "generatedAt",
                "generatorId", "generatorVersion", "inputs", "summary", "outcomes",
            ),
        )
        for field in ("inputs", "outcomes"):
            if isinstance(data[field], (str, bytes, bytearray, Mapping)):
                raise SchemaValidationError(f"Character normalization report {field} must be an array.")
        report = cls(
            report_id=data["reportId"],
            snapshot_id=data["snapshotId"],
            generated_at=data["generatedAt"],
            generator_id=data["generatorId"],
            generator_version=data["generatorVersion"],
            inputs=tuple(CharacterSourceInput.from_dict(item) for item in data["inputs"]),
            outcomes=tuple(CharacterNormalizationOutcome.from_dict(item) for item in data["outcomes"]),
        )
        supplied_summary = document_object(
            data["summary"], "Character normalization report summary", required=tuple(report.summary)
        )
        if supplied_summary != report.summary:
            raise SchemaValidationError("Character normalization report summary does not match its outcomes.")
        return report


@dataclass(frozen=True, slots=True)
class CharacterManifestFile:
    relative_path: str
    kind: Literal["source-input", "generated-output"]
    sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        if self.kind not in {"source-input", "generated-output"}:
            raise SchemaValidationError("Character manifest file kind is invalid.")
        object.__setattr__(self, "relative_path", _relative_path(self.relative_path, "Character manifest relativePath"))
        object.__setattr__(self, "sha256", sha256_checksum(self.sha256, "Character manifest sha256"))
        object.__setattr__(self, "byte_length", _nonnegative_integer(self.byte_length, "Character manifest byteLength"))

    def to_dict(self) -> dict[str, object]:
        return {
            "relativePath": self.relative_path,
            "kind": self.kind,
            "sha256": self.sha256,
            "byteLength": self.byte_length,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CharacterManifestFile":
        data = document_object(value, "Character manifest file", required=("relativePath", "kind", "sha256", "byteLength"))
        return cls(
            relative_path=data["relativePath"],
            kind=data["kind"],
            sha256=data["sha256"],
            byte_length=data["byteLength"],
        )


@dataclass(frozen=True, slots=True)
class CharacterSnapshotManifestDocument:
    snapshot_id: str
    generated_at: str
    generator_id: str
    generator_version: str
    files: tuple[CharacterManifestFile, ...]

    def __post_init__(self) -> None:
        files = tuple(self.files)
        if not files:
            raise SchemaValidationError("Character snapshot manifest must contain files.")
        unique_values([item.relative_path for item in files], "Character snapshot manifest paths")
        object.__setattr__(self, "snapshot_id", required_text(self.snapshot_id, "Character snapshot manifest snapshotId"))
        object.__setattr__(self, "generated_at", utc_timestamp(self.generated_at, "Character snapshot manifest generatedAt"))
        object.__setattr__(self, "generator_id", required_text(self.generator_id, "Character snapshot manifest generatorId"))
        object.__setattr__(self, "generator_version", required_text(self.generator_version, "Character snapshot manifest generatorVersion"))
        object.__setattr__(self, "files", tuple(sorted(files, key=lambda item: item.relative_path)))

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaId": CHARACTER_MANIFEST_SCHEMA_ID,
            "schemaVersion": CHARACTER_MANIFEST_CURRENT_VERSION,
            "snapshotId": self.snapshot_id,
            "generatedAt": self.generated_at,
            "generatorId": self.generator_id,
            "generatorVersion": self.generator_version,
            "files": [item.to_dict() for item in self.files],
        }

    def to_json(self) -> str:
        return deterministic_json(self)

    @classmethod
    def from_current_dict(cls, value: Mapping[str, Any]) -> "CharacterSnapshotManifestDocument":
        data = document_object(
            value,
            "Character snapshot manifest",
            required=(
                "schemaId", "schemaVersion", "snapshotId", "generatedAt",
                "generatorId", "generatorVersion", "files",
            ),
        )
        if isinstance(data["files"], (str, bytes, bytearray, Mapping)):
            raise SchemaValidationError("Character snapshot manifest files must be an array.")
        return cls(
            snapshot_id=data["snapshotId"],
            generated_at=data["generatedAt"],
            generator_id=data["generatorId"],
            generator_version=data["generatorVersion"],
            files=tuple(CharacterManifestFile.from_dict(item) for item in data["files"]),
        )


@dataclass(frozen=True, slots=True)
class CharacterSnapshotBundle:
    catalog: CharacterCatalogDocument
    source_snapshot: CharacterSourceSnapshotDocument
    validation_report: CharacterNormalizationReportDocument
    hero_source_bytes: bytes
    artifact_source_bytes: bytes

    def generated_bytes(self) -> Mapping[str, bytes]:
        return {
            BUNDLED_CATALOG_FILENAME: self.catalog.to_json().encode("utf-8"),
            BUNDLED_SOURCE_FILENAME: self.source_snapshot.to_json().encode("utf-8"),
            BUNDLED_VALIDATION_FILENAME: self.validation_report.to_json().encode("utf-8"),
        }


class _SourceRecordError(ValueError):
    def __init__(self, code: str, path: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.path = path
        self.reason = reason


def _source_mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _SourceRecordError("invalid-type", path, "Expected an object.")
    if not all(isinstance(key, str) for key in value):
        raise _SourceRecordError("invalid-field-name", path, "Object field names must be strings.")
    return value


def _required_source_text(data: Mapping[str, Any], field: str, path: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _SourceRecordError("missing-required-text", f"{path}.{field}", "Expected a non-empty string.")
    return value.strip()


def _required_source_number(data: Mapping[str, Any], field: str, path: str) -> int | float:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise _SourceRecordError("missing-required-number", f"{path}.{field}", "Expected a finite non-negative number.")
    return value


def _percentage(value: int | float) -> int:
    """Match Fribbels' ``Math.round(source * 100)`` conversion for nonnegative stats."""

    return math.floor(value * 100 + 0.5)


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "record"


def _normalize_hero(
    source_key: str,
    value: object,
    *,
    hero_dense_id: int,
    first_profile_dense_id: int,
) -> HeroDefinition:
    path = f"heroes[{json.dumps(source_key, ensure_ascii=False)}]"
    data = _source_mapping(value, path)
    name = _required_source_text(data, "name", path)
    if name != source_key:
        raise _SourceRecordError("ambiguous-source-key", f"{path}.name", "Hero key and name must match exactly.")
    source_id = _required_source_text(data, "_id", path)
    if not _HERO_SOURCE_ID_PATTERN.fullmatch(source_id):
        raise _SourceRecordError("invalid-source-id", f"{path}._id", "Hero _id must be a lowercase hyphenated identifier.")
    statuses = _source_mapping(data.get("calculatedStatus"), f"{path}.calculatedStatus")
    profiles: list[HeroBaseProfile] = []
    for offset, (source_profile, level, stars, label) in enumerate(_PROFILE_SPECS):
        stats = _source_mapping(statuses.get(source_profile), f"{path}.calculatedStatus.{source_profile}")
        final_stats: list[tuple[FinalStat, int | float]] = []
        for final_stat, source_field, percentage in _FINAL_STAT_SOURCE_FIELDS:
            number = _required_source_number(stats, source_field, f"{path}.calculatedStatus.{source_profile}")
            final_stats.append((final_stat, _percentage(number) if percentage else number))
        profiles.append(
            HeroBaseProfile(
                profile_id=f"profile.fribbels.{source_id}.{level}.{stars}",
                label=label,
                level=level,
                stars=stars,
                final_stats=tuple(final_stats),
                dense_id=first_profile_dense_id + offset,
            )
        )
    return HeroDefinition(
        hero_id=f"hero.fribbels.{source_id}",
        name=name,
        base_profiles=tuple(profiles),
        dense_id=hero_dense_id,
    )


def _normalize_artifact(source_key: str, value: object, *, dense_id: int) -> ArtifactDefinition:
    path = f"artifacts[{json.dumps(source_key, ensure_ascii=False)}]"
    data = _source_mapping(value, path)
    name = _required_source_text(data, "name", path)
    if name != source_key:
        raise _SourceRecordError("ambiguous-source-key", f"{path}.name", "Artifact key and name must match exactly.")
    code = _required_source_text(data, "code", path)
    stats = _source_mapping(data.get("stats"), f"{path}.stats")
    attack = _required_source_number(stats, "attack", f"{path}.stats")
    health = _required_source_number(stats, "health", f"{path}.stats")
    _required_source_number(stats, "defense", f"{path}.stats")
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    artifact_id = f"artifact.fribbels.{_slug(code)}.{_slug(name)}.{digest}"
    return ArtifactDefinition(
        artifact_id=artifact_id,
        name=name,
        max_level=30,
        base_attack=attack,
        base_health=health,
        max_attack=attack * 13,
        max_health=health * 13,
        dense_id=dense_id,
    )


def _load_source_root(raw: bytes, family: str) -> Mapping[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SchemaValidationError(f"{family} must be UTF-8: {error}") from error
    root = parse_json_document(text, family)
    if not all(isinstance(key, str) and key.strip() for key in root):
        raise SchemaValidationError(f"{family} keys must be non-empty strings.")
    return root


def _provenance(*, generated_at: str, fetched_at: str, inputs: Sequence[CharacterSourceInput]) -> SourceMetadata:
    generated_at = utc_timestamp(generated_at, "Character snapshot generatedAt")
    fetched_at = utc_timestamp(fetched_at, "Character snapshot fetchedAt")
    return SourceMetadata(
        source_name="Fribbels E7 Optimizer offline character caches",
        source_version=FRIBBELS_CHARACTER_SOURCE_VERSION,
        source_revision=FRIBBELS_CHARACTER_SOURCE_REVISION,
        unknown_fields={
            "repository": FRIBBELS_CHARACTER_REPOSITORY,
            "repositoryUrl": FRIBBELS_CHARACTER_REPOSITORY_URL,
            "revisionUrl": FRIBBELS_CHARACTER_SOURCE_REVISION_URL,
            "sourceBranch": FRIBBELS_CHARACTER_SOURCE_BRANCH,
            "offlineAppVersion": FRIBBELS_OFFLINE_APP_VERSION,
            "generatedAt": generated_at,
            "fetchedAt": fetched_at,
            "generator": {
                "id": CHARACTER_SNAPSHOT_GENERATOR_ID,
                "version": CHARACTER_SNAPSHOT_GENERATOR_VERSION,
            },
            "inputs": [item.to_dict() for item in sorted(inputs, key=lambda item: item.record_kind)],
            "cacheLineage": {
                "lastUpdateRevision": FRIBBELS_LAST_CACHE_UPDATE_REVISION,
                "lastUpdateAt": FRIBBELS_LAST_CACHE_UPDATE_AT,
                "heroRuntimeSourcePath": "app/js/lib/heroData.js",
                "artifactRuntimeSourcePath": "app/js/lib/artifact.js",
                "heroCacheEndpoints": [
                    "http://e7-optimizer-game-data.s3-accelerate.amazonaws.com/herodata.json",
                    "https://fribbels-epic-7-optimizer-cn.azurewebsites.net/data/cache/herodata.json",
                ],
                "artifactCacheEndpoints": [
                    "http://e7-optimizer-game-data.s3-accelerate.amazonaws.com/artifactdata.json",
                    "https://fribbels-epic-7-optimizer-cn.azurewebsites.net/data/cache/artifactdata.json",
                ],
                "manualHeroApiDocumentedBySource": "https://api.epicsevendb.com/hero/",
            },
            "attribution": {
                "project": "Fribbels E7 Optimizer",
                "projectAuthor": "Fribbels",
                "offlineForkOwner": "RexQian",
                "licenseDeclared": "MIT",
                "licenseEvidencePath": "app/package.json",
                "rootLicenseFilePresentAtRevision": False,
                "gameDataNotice": (
                    "Epic Seven names, statistics, and artwork are owned by their respective rights holders; "
                    "this free application uses public catalog data with attribution."
                ),
            },
        },
    )


def build_character_snapshot(
    hero_source_bytes: bytes,
    artifact_source_bytes: bytes,
    *,
    generated_at: str,
    fetched_at: str,
    require_pinned_hashes: bool = True,
) -> CharacterSnapshotBundle:
    """Normalize explicit source bytes without reading files, network, or a clock."""

    if not isinstance(hero_source_bytes, bytes) or not isinstance(artifact_source_bytes, bytes):
        raise SchemaValidationError("Character snapshot inputs must be bytes.")
    hero_sha256 = hashlib.sha256(hero_source_bytes).hexdigest()
    artifact_sha256 = hashlib.sha256(artifact_source_bytes).hexdigest()
    if require_pinned_hashes and hero_sha256 != FRIBBELS_HERO_SHA256:
        raise SchemaValidationError("Hero source SHA-256 does not match the pinned Fribbels input.")
    if require_pinned_hashes and artifact_sha256 != FRIBBELS_ARTIFACT_SHA256:
        raise SchemaValidationError("Artifact source SHA-256 does not match the pinned Fribbels input.")

    heroes_source = _load_source_root(hero_source_bytes, "Fribbels hero cache")
    artifacts_source = _load_source_root(artifact_source_bytes, "Fribbels artifact cache")
    inputs = (
        CharacterSourceInput(
            record_kind="hero",
            source_path=FRIBBELS_HERO_SOURCE_PATH,
            bundled_path=BUNDLED_HERO_SOURCE_PATH,
            sha256=hero_sha256,
            git_blob_sha1=FRIBBELS_HERO_GIT_BLOB_SHA1,
            byte_length=len(hero_source_bytes),
            record_count=len(heroes_source),
        ),
        CharacterSourceInput(
            record_kind="artifact",
            source_path=FRIBBELS_ARTIFACT_SOURCE_PATH,
            bundled_path=BUNDLED_ARTIFACT_SOURCE_PATH,
            sha256=artifact_sha256,
            git_blob_sha1=FRIBBELS_ARTIFACT_GIT_BLOB_SHA1,
            byte_length=len(artifact_source_bytes),
            record_count=len(artifacts_source),
        ),
    )
    source = _provenance(generated_at=generated_at, fetched_at=fetched_at, inputs=inputs)
    snapshot_id = f"character-snapshot.fribbels.{FRIBBELS_CHARACTER_SOURCE_REVISION[:12]}"

    heroes: list[HeroDefinition] = []
    artifacts: list[ArtifactDefinition] = []
    outcomes: list[CharacterNormalizationOutcome] = []
    profile_dense_id = 0
    hero_ids: set[str] = set()
    profile_ids: set[str] = set()
    artifact_ids: set[str] = set()
    artifact_code_counts = Counter(
        value.get("code")
        for value in artifacts_source.values()
        if isinstance(value, Mapping) and isinstance(value.get("code"), str)
    )

    for source_index, source_key in enumerate(sorted(heroes_source)):
        try:
            hero = _normalize_hero(
                source_key,
                heroes_source[source_key],
                hero_dense_id=len(heroes),
                first_profile_dense_id=profile_dense_id,
            )
            canonical_ids = (hero.hero_id, *(profile.profile_id for profile in hero.base_profiles))
            if hero.hero_id in hero_ids or any(profile_id in profile_ids for profile_id in canonical_ids[1:]):
                raise _SourceRecordError("duplicate-canonical-id", f"heroes[{json.dumps(source_key)}]", "Normalized hero or profile ID is not unique.")
            heroes.append(hero)
            hero_ids.add(hero.hero_id)
            profile_ids.update(canonical_ids[1:])
            profile_dense_id += len(hero.base_profiles)
            outcomes.append(
                CharacterNormalizationOutcome(
                    record_kind="hero",
                    source_path=FRIBBELS_HERO_SOURCE_PATH,
                    source_key=source_key,
                    source_index=source_index,
                    status="normalized",
                    canonical_ids=canonical_ids,
                    warnings=("hero-source-fields-preserved-in-sidecar",),
                )
            )
        except _SourceRecordError as error:
            outcomes.append(
                CharacterNormalizationOutcome(
                    record_kind="hero",
                    source_path=FRIBBELS_HERO_SOURCE_PATH,
                    source_key=source_key,
                    source_index=source_index,
                    status="rejected",
                    rejection=NormalizationRejection(error.code, error.path, error.reason),
                )
            )

    for source_index, source_key in enumerate(sorted(artifacts_source)):
        try:
            artifact = _normalize_artifact(source_key, artifacts_source[source_key], dense_id=len(artifacts))
            if artifact.artifact_id in artifact_ids:
                raise _SourceRecordError("duplicate-canonical-id", f"artifacts[{json.dumps(source_key)}]", "Normalized artifact ID is not unique.")
            raw_record = _source_mapping(artifacts_source[source_key], f"artifacts[{json.dumps(source_key)}]")
            raw_stats = _source_mapping(raw_record.get("stats"), f"artifacts[{json.dumps(source_key)}].stats")
            warnings: list[str] = []
            if raw_stats.get("defense") != 0:
                warnings.append("artifact-defense-preserved-in-sidecar")
            raw_code = raw_record.get("code")
            if isinstance(raw_code, str) and artifact_code_counts[raw_code] > 1:
                warnings.append("artifact-source-code-not-unique")
            artifacts.append(artifact)
            artifact_ids.add(artifact.artifact_id)
            outcomes.append(
                CharacterNormalizationOutcome(
                    record_kind="artifact",
                    source_path=FRIBBELS_ARTIFACT_SOURCE_PATH,
                    source_key=source_key,
                    source_index=source_index,
                    status="normalized",
                    canonical_ids=(artifact.artifact_id,),
                    warnings=tuple(warnings),
                )
            )
        except _SourceRecordError as error:
            outcomes.append(
                CharacterNormalizationOutcome(
                    record_kind="artifact",
                    source_path=FRIBBELS_ARTIFACT_SOURCE_PATH,
                    source_key=source_key,
                    source_index=source_index,
                    status="rejected",
                    rejection=NormalizationRejection(error.code, error.path, error.reason),
                )
            )

    catalog = CharacterCatalogDocument(
        catalog_id=f"catalog.fribbels.{FRIBBELS_CHARACTER_SOURCE_REVISION[:12]}",
        generated_at=generated_at,
        source=source,
        heroes=tuple(heroes),
        artifacts=tuple(artifacts),
    )
    source_snapshot = CharacterSourceSnapshotDocument(
        snapshot_id=snapshot_id,
        generated_at=generated_at,
        generator_id=CHARACTER_SNAPSHOT_GENERATOR_ID,
        generator_version=CHARACTER_SNAPSHOT_GENERATOR_VERSION,
        source=source,
        inputs=inputs,
        heroes=heroes_source,
        artifacts=artifacts_source,
    )
    validation_report = CharacterNormalizationReportDocument(
        report_id=f"normalization-report.fribbels.{FRIBBELS_CHARACTER_SOURCE_REVISION[:12]}",
        snapshot_id=snapshot_id,
        generated_at=generated_at,
        generator_id=CHARACTER_SNAPSHOT_GENERATOR_ID,
        generator_version=CHARACTER_SNAPSHOT_GENERATOR_VERSION,
        inputs=inputs,
        outcomes=tuple(outcomes),
    )
    return CharacterSnapshotBundle(
        catalog=catalog,
        source_snapshot=source_snapshot,
        validation_report=validation_report,
        hero_source_bytes=hero_source_bytes,
        artifact_source_bytes=artifact_source_bytes,
    )


def create_character_snapshot_manifest(
    bundle: CharacterSnapshotBundle,
    *,
    relative_paths: Mapping[str, str] | None = None,
) -> CharacterSnapshotManifestDocument:
    generated = bundle.generated_bytes()
    paths = {
        BUNDLED_CATALOG_FILENAME: BUNDLED_CATALOG_FILENAME,
        BUNDLED_SOURCE_FILENAME: BUNDLED_SOURCE_FILENAME,
        BUNDLED_VALIDATION_FILENAME: BUNDLED_VALIDATION_FILENAME,
        BUNDLED_HERO_SOURCE_PATH: BUNDLED_HERO_SOURCE_PATH,
        BUNDLED_ARTIFACT_SOURCE_PATH: BUNDLED_ARTIFACT_SOURCE_PATH,
    }
    if relative_paths is not None:
        if set(relative_paths) != set(paths):
            raise SchemaValidationError("Character snapshot manifest paths must name every source and generated file.")
        paths = dict(relative_paths)
    content = {
        BUNDLED_CATALOG_FILENAME: generated[BUNDLED_CATALOG_FILENAME],
        BUNDLED_SOURCE_FILENAME: generated[BUNDLED_SOURCE_FILENAME],
        BUNDLED_VALIDATION_FILENAME: generated[BUNDLED_VALIDATION_FILENAME],
        BUNDLED_HERO_SOURCE_PATH: bundle.hero_source_bytes,
        BUNDLED_ARTIFACT_SOURCE_PATH: bundle.artifact_source_bytes,
    }
    source_keys = {BUNDLED_HERO_SOURCE_PATH, BUNDLED_ARTIFACT_SOURCE_PATH}
    files = tuple(
        CharacterManifestFile(
            relative_path=paths[key],
            kind="source-input" if key in source_keys else "generated-output",
            sha256=hashlib.sha256(value).hexdigest(),
            byte_length=len(value),
        )
        for key, value in content.items()
    )
    return CharacterSnapshotManifestDocument(
        snapshot_id=bundle.source_snapshot.snapshot_id,
        generated_at=bundle.source_snapshot.generated_at,
        generator_id=CHARACTER_SNAPSHOT_GENERATOR_ID,
        generator_version=CHARACTER_SNAPSHOT_GENERATOR_VERSION,
        files=files,
    )


def load_character_source_snapshot(value: object) -> CharacterSourceSnapshotDocument:
    return load_versioned_document(
        value,
        family="Character source snapshot",
        schema_id=CHARACTER_SOURCE_SCHEMA_ID,
        current_version=CHARACTER_SOURCE_CURRENT_VERSION,
        migrations=CHARACTER_SOURCE_MIGRATIONS,
        parser=CharacterSourceSnapshotDocument.from_current_dict,
    )


def load_character_source_snapshot_json(value: object) -> CharacterSourceSnapshotDocument:
    return load_character_source_snapshot(parse_json_document(value, "Character source snapshot"))


def load_character_normalization_report(value: object) -> CharacterNormalizationReportDocument:
    return load_versioned_document(
        value,
        family="Character normalization report",
        schema_id=CHARACTER_VALIDATION_SCHEMA_ID,
        current_version=CHARACTER_VALIDATION_CURRENT_VERSION,
        migrations=CHARACTER_VALIDATION_MIGRATIONS,
        parser=CharacterNormalizationReportDocument.from_current_dict,
    )


def load_character_normalization_report_json(value: object) -> CharacterNormalizationReportDocument:
    return load_character_normalization_report(parse_json_document(value, "Character normalization report"))


def load_character_snapshot_manifest(value: object) -> CharacterSnapshotManifestDocument:
    return load_versioned_document(
        value,
        family="Character snapshot manifest",
        schema_id=CHARACTER_MANIFEST_SCHEMA_ID,
        current_version=CHARACTER_MANIFEST_CURRENT_VERSION,
        migrations=CHARACTER_MANIFEST_MIGRATIONS,
        parser=CharacterSnapshotManifestDocument.from_current_dict,
    )


def load_character_snapshot_manifest_json(value: object) -> CharacterSnapshotManifestDocument:
    return load_character_snapshot_manifest(parse_json_document(value, "Character snapshot manifest"))


def bundled_character_data_path(relative_path: str) -> Path:
    normalized = _relative_path(relative_path, "Bundled character data path")
    return BUNDLED_CHARACTER_DATA_DIRECTORY.joinpath(*PurePosixPath(normalized).parts)


def load_bundled_character_catalog() -> CharacterCatalogDocument:
    return load_character_catalog_json(
        bundled_character_data_path(BUNDLED_CATALOG_FILENAME).read_text(encoding="utf-8")
    )


def load_bundled_character_source_snapshot() -> CharacterSourceSnapshotDocument:
    return load_character_source_snapshot_json(
        bundled_character_data_path(BUNDLED_SOURCE_FILENAME).read_text(encoding="utf-8")
    )


def load_bundled_character_normalization_report() -> CharacterNormalizationReportDocument:
    return load_character_normalization_report_json(
        bundled_character_data_path(BUNDLED_VALIDATION_FILENAME).read_text(encoding="utf-8")
    )


def load_bundled_character_snapshot_manifest() -> CharacterSnapshotManifestDocument:
    return load_character_snapshot_manifest_json(
        bundled_character_data_path(BUNDLED_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )


__all__ = [
    "BUNDLED_ARTIFACT_SOURCE_PATH",
    "BUNDLED_CATALOG_FILENAME",
    "BUNDLED_CHARACTER_DATA_DIRECTORY",
    "BUNDLED_HERO_SOURCE_PATH",
    "BUNDLED_MANIFEST_FILENAME",
    "BUNDLED_SOURCE_FILENAME",
    "BUNDLED_VALIDATION_FILENAME",
    "CHARACTER_MANIFEST_CURRENT_VERSION",
    "CHARACTER_MANIFEST_SCHEMA_ID",
    "CHARACTER_SNAPSHOT_GENERATOR_ID",
    "CHARACTER_SNAPSHOT_GENERATOR_VERSION",
    "CHARACTER_SOURCE_CURRENT_VERSION",
    "CHARACTER_SOURCE_SCHEMA_ID",
    "CHARACTER_VALIDATION_CURRENT_VERSION",
    "CHARACTER_VALIDATION_SCHEMA_ID",
    "FRIBBELS_ARTIFACT_SHA256",
    "FRIBBELS_ARTIFACT_SOURCE_PATH",
    "FRIBBELS_CHARACTER_REPOSITORY",
    "FRIBBELS_CHARACTER_REPOSITORY_URL",
    "FRIBBELS_CHARACTER_SOURCE_REVISION",
    "FRIBBELS_CHARACTER_SOURCE_VERSION",
    "FRIBBELS_HERO_SHA256",
    "FRIBBELS_HERO_SOURCE_PATH",
    "CharacterManifestFile",
    "CharacterNormalizationOutcome",
    "CharacterNormalizationReportDocument",
    "CharacterSnapshotBundle",
    "CharacterSnapshotManifestDocument",
    "CharacterSourceInput",
    "CharacterSourceSnapshotDocument",
    "NormalizationRejection",
    "build_character_snapshot",
    "bundled_character_data_path",
    "create_character_snapshot_manifest",
    "load_bundled_character_catalog",
    "load_bundled_character_normalization_report",
    "load_bundled_character_snapshot_manifest",
    "load_bundled_character_source_snapshot",
    "load_character_normalization_report",
    "load_character_normalization_report_json",
    "load_character_snapshot_manifest",
    "load_character_snapshot_manifest_json",
    "load_character_source_snapshot",
    "load_character_source_snapshot_json",
]
