"""Shared primitives for immutable, independently versioned JSON envelopes."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, TypeVar

from src.optimizer.domain import DomainValidationError


class SchemaValidationError(ValueError):
    """Raised when a persisted optimizer document violates its schema."""


JsonScalar = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class FrozenJsonObject(Mapping[str, "FrozenJson"]):
    """A deterministic, recursively immutable JSON object."""

    entries: tuple[tuple[str, "FrozenJson"], ...] = ()

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, key: str) -> "FrozenJson":
        for item_key, value in self.entries:
            if item_key == key:
                return value
        raise KeyError(key)


@dataclass(frozen=True, slots=True)
class FrozenJsonArray(Sequence["FrozenJson"]):
    """A recursively immutable JSON array."""

    values: tuple["FrozenJson", ...] = ()

    def __getitem__(self, index: int | slice) -> "FrozenJson | tuple[FrozenJson, ...]":
        return self.values[index]

    def __len__(self) -> int:
        return len(self.values)


FrozenJson = JsonScalar | FrozenJsonObject | FrozenJsonArray


def freeze_json(value: object, field: str = "metadata") -> FrozenJson:
    """Validate a JSON value and recursively freeze it in deterministic order."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SchemaValidationError(f"{field} must not contain NaN or infinity.")
        return value
    if isinstance(value, Mapping):
        entries: list[tuple[str, FrozenJson]] = []
        if not all(isinstance(key, str) for key in value):
            raise SchemaValidationError(f"{field} object keys must be strings.")
        for key in sorted(value):
            entries.append((key, freeze_json(value[key], f"{field}.{key}")))
        return FrozenJsonObject(tuple(entries))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return FrozenJsonArray(
            tuple(freeze_json(item, f"{field}[{index}]") for index, item in enumerate(value))
        )
    raise SchemaValidationError(f"{field} must contain only JSON-compatible values.")


def freeze_json_object(value: object, field: str = "metadata") -> FrozenJsonObject:
    frozen = freeze_json(value, field)
    if not isinstance(frozen, FrozenJsonObject):
        raise SchemaValidationError(f"{field} must be an object.")
    return frozen


def thaw_json(value: FrozenJson) -> Any:
    """Return a mutable JSON-compatible copy of a frozen JSON value."""

    if isinstance(value, FrozenJsonObject):
        return {key: thaw_json(item) for key, item in value.entries}
    if isinstance(value, FrozenJsonArray):
        return [thaw_json(item) for item in value.values]
    return value


def _text(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        suffix = " or null" if optional else ""
        raise SchemaValidationError(f"{field} must be a non-empty string{suffix}.")
    return value.strip()


def required_text(value: object, field: str) -> str:
    normalized = _text(value, field)
    assert normalized is not None
    return normalized


def optional_text(value: object, field: str) -> str | None:
    return _text(value, field, optional=True)


def document_object(
    value: object,
    family: str,
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{family} document must be an object.")
    if not all(isinstance(key, str) for key in value):
        raise SchemaValidationError(f"{family} document field names must be strings.")
    required_fields = set(required)
    allowed_fields = required_fields | set(optional)
    missing = sorted(required_fields - set(value))
    unknown = sorted(set(value) - allowed_fields)
    if missing:
        raise SchemaValidationError(
            f"{family} document is missing required field(s): {', '.join(missing)}."
        )
    if unknown:
        raise SchemaValidationError(
            f"{family} document contains unknown field(s): {', '.join(unknown)}. "
            "Source-specific fields belong in source.unknownFields."
        )
    return value


def json_array(value: object, field: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise SchemaValidationError(f"{field} must be an array.")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise SchemaValidationError(f"{field} must be an array.") from None


UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


def utc_timestamp(value: object, field: str) -> str:
    timestamp = required_text(value, field)
    if not UTC_TIMESTAMP_PATTERN.fullmatch(timestamp):
        raise SchemaValidationError(
            f"{field} must be an ISO-8601 UTC timestamp ending in Z, for example "
            "2026-07-20T12:34:56Z."
        )
    try:
        parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError:
        raise SchemaValidationError(f"{field} is not a valid calendar timestamp.") from None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SchemaValidationError(f"{field} must use UTC.")
    return timestamp


def timestamp_value(value: str) -> datetime:
    """Parse a timestamp already validated by :func:`utc_timestamp`."""

    return datetime.fromisoformat(value[:-1] + "+00:00")


SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def sha256_checksum(value: object, field: str) -> str:
    checksum = required_text(value, field)
    if not SHA256_PATTERN.fullmatch(checksum):
        raise SchemaValidationError(f"{field} must be a 64-character hexadecimal SHA-256 checksum.")
    return checksum.lower()


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Attribution plus a lossless, immutable home for source-specific fields."""

    source_name: str
    source_version: str | None = None
    source_revision: str | None = None
    unknown_fields: FrozenJsonObject | Mapping[str, object] = FrozenJsonObject()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_name", required_text(self.source_name, "SourceMetadata.source_name"))
        object.__setattr__(
            self,
            "source_version",
            optional_text(self.source_version, "SourceMetadata.source_version"),
        )
        object.__setattr__(
            self,
            "source_revision",
            optional_text(self.source_revision, "SourceMetadata.source_revision"),
        )
        object.__setattr__(
            self,
            "unknown_fields",
            freeze_json_object(self.unknown_fields, "SourceMetadata.unknown_fields"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "sourceName": self.source_name,
            "sourceVersion": self.source_version,
            "sourceRevision": self.source_revision,
            "unknownFields": thaw_json(self.unknown_fields),
        }

    @classmethod
    def from_dict(cls, value: object, *, family: str) -> "SourceMetadata":
        data = document_object(
            value,
            f"{family} source metadata",
            required=("sourceName",),
            optional=("sourceVersion", "sourceRevision", "unknownFields"),
        )
        return cls(
            source_name=data["sourceName"],
            source_version=data.get("sourceVersion"),
            source_revision=data.get("sourceRevision"),
            unknown_fields=data.get("unknownFields", {}),
        )


def unique_values(values: Sequence[object], field: str) -> None:
    if len(values) != len(set(values)):
        raise SchemaValidationError(f"{field} must contain unique values.")


def optional_dense_values(values: Sequence[int | None], field: str) -> None:
    supplied = [value for value in values if value is not None]
    unique_values(supplied, field)


T = TypeVar("T")


def domain_value(family: str, field: str, factory: Callable[[], T]) -> T:
    try:
        return factory()
    except DomainValidationError as error:
        raise SchemaValidationError(f"{family} {field} is invalid: {error}") from error


Migration = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def migration_registry(migrations: Mapping[int, Migration]) -> Mapping[int, Migration]:
    """Freeze a family's explicit old-version -> next-version migration table."""

    return MappingProxyType(dict(migrations))


def load_versioned_document(
    value: object,
    *,
    family: str,
    schema_id: str,
    current_version: int,
    migrations: Mapping[int, Migration],
    parser: Callable[[Mapping[str, Any]], T],
) -> T:
    """Validate an envelope version, run sequential migrations, and parse it."""

    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{family} document must be an object.")

    raw_schema_id = value.get("schemaId")
    supplied_schema_id = required_text(raw_schema_id, f"{family} schemaId")
    if supplied_schema_id != schema_id:
        raise SchemaValidationError(
            f"{family} schemaId must be {schema_id!r}; received {supplied_schema_id!r}."
        )

    version = value.get("schemaVersion")
    if isinstance(version, bool) or not isinstance(version, int):
        raise SchemaValidationError(f"{family} schemaVersion must be an integer.")
    if version < 1:
        raise SchemaValidationError(f"{family} schemaVersion must be at least 1.")
    if version > current_version:
        raise SchemaValidationError(
            f"{family} schemaVersion {version} is newer than supported version {current_version}; "
            "update the application before loading this document."
        )

    mutable = thaw_json(freeze_json_object(value, f"{family} document"))
    while version < current_version:
        migrate = migrations.get(version)
        if migrate is None:
            raise SchemaValidationError(
                f"{family} schemaVersion {version} is older than supported version {current_version} "
                "and no migration is registered."
            )
        try:
            migrated = migrate(mutable)
        except SchemaValidationError:
            raise
        except Exception as error:
            raise SchemaValidationError(
                f"{family} migration from version {version} failed: {error}"
            ) from error
        if not isinstance(migrated, Mapping):
            raise SchemaValidationError(
                f"{family} migration from version {version} must return an object."
            )
        next_version = migrated.get("schemaVersion")
        if isinstance(next_version, bool) or next_version != version + 1:
            raise SchemaValidationError(
                f"{family} migration from version {version} must produce schemaVersion {version + 1}."
            )
        if migrated.get("schemaId") != schema_id:
            raise SchemaValidationError(
                f"{family} migration from version {version} changed schemaId unexpectedly."
            )
        mutable = thaw_json(freeze_json_object(migrated, f"{family} migrated document"))
        version = next_version

    return parser(mutable)


def deterministic_json(value: object) -> str:
    """Serialize a schema record or mapping with stable keys and compact spacing."""

    payload = value.to_dict() if hasattr(value, "to_dict") else value
    frozen = freeze_json(payload, "JSON document")
    return json.dumps(
        thaw_json(frozen),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_json_document(value: object, family: str) -> Mapping[str, Any]:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{family} JSON must be a string.")

    def reject_constant(constant: str) -> None:
        raise SchemaValidationError(f"{family} JSON contains invalid number {constant}.")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise SchemaValidationError(f"{family} JSON contains duplicate object key {key!r}.")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, parse_constant=reject_constant, object_pairs_hook=object_pairs)
    except SchemaValidationError:
        raise
    except (json.JSONDecodeError, TypeError) as error:
        raise SchemaValidationError(f"{family} JSON is malformed: {error}") from error
    if not isinstance(parsed, Mapping):
        raise SchemaValidationError(f"{family} JSON root must be an object.")
    return parsed
