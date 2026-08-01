"""Transactional SQLite persistence for merged Fribbels inventory state.

The repository is deliberately UI-independent. Constructing one performs no
filesystem work; callers explicitly initialize it before reading or writing.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.core.path_safety import lexical_absolute_path, same_existing_path
from src.core.workspace_paths import (
    USER_DATA_DIRECTORY_ENV,
    resolve_user_data_directory,
)
from src.optimizer.data.fribbels import (
    FribbelsEncoding,
    FribbelsItemProjection,
    FribbelsVariant,
    ImportedHeroReference,
    ProjectionEvidenceState,
)
from src.optimizer.data.fribbels_merge import (
    FribbelsIdentityKind,
    FribbelsInventoryItem,
    FribbelsItemIdentity,
    FribbelsMergeResult,
)
from src.optimizer.data.schema_common import (
    FrozenJsonObject,
    deterministic_json,
    freeze_json,
    freeze_json_object,
    thaw_json,
    utc_timestamp,
)
from src.optimizer.domain import (
    GEAR_SLOT_ORDER,
    GearItem,
    GearRank,
    GearSet,
    GearSlot,
    ItemStatType,
    ReforgeMaterial,
)


INVENTORY_REPOSITORY_SCHEMA_VERSION = 1
INVENTORY_REPOSITORY_KIND = "fribbels-inventory"
DEFAULT_BUSY_TIMEOUT_MS = 5_000
MAX_BUSY_TIMEOUT_MS = 60_000
DATABASE_FILENAME = "optimizer.db"


class InventoryRepositoryError(RuntimeError):
    """Base class for privacy-safe repository failures."""


class InventoryRepositorySchemaError(InventoryRepositoryError):
    """Raised when a database cannot be safely interpreted by this version."""


class InventoryRepositoryMigrationError(InventoryRepositoryError):
    """Raised after a migration transaction is rolled back."""

    def __init__(self, message: str, *, backup_path: Path | None) -> None:
        self.backup_path = backup_path
        super().__init__(message)


class InventoryRepositoryWriteError(InventoryRepositoryError):
    """Raised when an atomic repository write is rolled back."""


class InventoryRepositoryReadError(InventoryRepositoryError):
    """Raised when persisted state cannot be reconstructed safely."""


def resolve_inventory_database_path(
    environment: Mapping[str, str] | None = None,
    *,
    working_directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the default database path without touching the filesystem."""

    return (
        resolve_user_data_directory(
            environment,
            working_directory=working_directory,
        )
        / DATABASE_FILENAME
    )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")
    return value.strip()


def _count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


@dataclass(frozen=True, slots=True)
class ImportHistoryRecord:
    """Privacy-safe, caller-timestamped audit record for one import attempt."""

    import_id: str
    imported_at: str
    source_encoding: FribbelsEncoding
    source_variant: FribbelsVariant
    source_item_count: int
    accepted_count: int
    rejected_count: int
    warning_count: int
    inserted_count: int
    updated_count: int
    unchanged_count: int
    conflict_count: int
    unseen_existing_count: int
    source_metadata: FrozenJsonObject | Mapping[str, object] = FrozenJsonObject()

    def __post_init__(self) -> None:
        object.__setattr__(self, "import_id", _required_text(self.import_id, "import_id"))
        object.__setattr__(
            self,
            "imported_at",
            utc_timestamp(self.imported_at, "ImportHistoryRecord.imported_at"),
        )
        try:
            encoding = (
                self.source_encoding
                if isinstance(self.source_encoding, FribbelsEncoding)
                else FribbelsEncoding(self.source_encoding)
            )
            variant = (
                self.source_variant
                if isinstance(self.source_variant, FribbelsVariant)
                else FribbelsVariant(self.source_variant)
            )
        except (TypeError, ValueError):
            raise ValueError("Import history source encoding or variant is unsupported.") from None
        object.__setattr__(self, "source_encoding", encoding)
        object.__setattr__(self, "source_variant", variant)
        for field in (
            "source_item_count",
            "accepted_count",
            "rejected_count",
            "warning_count",
            "inserted_count",
            "updated_count",
            "unchanged_count",
            "conflict_count",
            "unseen_existing_count",
        ):
            object.__setattr__(self, field, _count(getattr(self, field), field))
        if self.source_item_count != self.accepted_count + self.rejected_count:
            raise ValueError("Import history source count must equal accepted plus rejected counts.")
        if self.accepted_count != (
            self.inserted_count
            + self.updated_count
            + self.unchanged_count
            + self.conflict_count
        ):
            raise ValueError("Import history outcome counts must equal the accepted count.")
        object.__setattr__(
            self,
            "source_metadata",
            (
                self.source_metadata
                if isinstance(self.source_metadata, FrozenJsonObject)
                else freeze_json_object(
                    self.source_metadata,
                    "ImportHistoryRecord.source_metadata",
                )
            ),
        )

    @classmethod
    def from_merge_result(
        cls,
        *,
        import_id: str,
        imported_at: str,
        source_encoding: FribbelsEncoding,
        source_variant: FribbelsVariant,
        source_item_count: int,
        merge_result: FribbelsMergeResult,
        source_metadata: FrozenJsonObject | Mapping[str, object] = FrozenJsonObject(),
    ) -> "ImportHistoryRecord":
        """Build counts from a merge while keeping time and safe metadata explicit."""

        if not isinstance(merge_result, FribbelsMergeResult):
            raise ValueError("merge_result must be a FribbelsMergeResult.")
        return cls(
            import_id=import_id,
            imported_at=imported_at,
            source_encoding=source_encoding,
            source_variant=source_variant,
            source_item_count=source_item_count,
            accepted_count=len(merge_result.outcomes),
            rejected_count=len(merge_result.source_rejections),
            warning_count=len(merge_result.source_warnings),
            inserted_count=len(merge_result.inserted),
            updated_count=len(merge_result.updated),
            unchanged_count=len(merge_result.unchanged),
            conflict_count=len(merge_result.conflicts),
            unseen_existing_count=len(merge_result.unseen_existing_ids),
            source_metadata=source_metadata,
        )


@dataclass(frozen=True, slots=True)
class RepositoryInitialization:
    database_path: Path
    previous_version: int
    schema_version: int
    created: bool
    backup_path: Path | None


@dataclass(frozen=True, slots=True)
class InventorySummary:
    total_items: int
    equipped_items: int
    locked_items: int
    imported_heroes: int
    import_history_records: int
    ingame_aliases: int
    source_aliases: int
    fingerprint_aliases: int
    items_by_slot: tuple[tuple[GearSlot, int], ...]

    def count_for_slot(self, slot: GearSlot) -> int:
        return dict(self.items_by_slot)[GearSlot(slot)]


@dataclass(frozen=True, slots=True)
class EquipmentAssignmentResult:
    """Aggregate outcome of one atomic local six-piece assignment."""

    assigned_items: int
    already_on_target: int
    moved_from_other_heroes: int
    newly_equipped_items: int
    unequipped_from_target: int
    total_equipped_items: int


@dataclass(frozen=True, slots=True)
class DenseInventorySnapshot:
    """Search-only gear arrays; dense IDs are ephemeral and contiguous."""

    items_by_slot: tuple[tuple[GearSlot, tuple[GearItem, ...]], ...]
    dense_id_to_stable_id: tuple[tuple[int, str], ...]

    def __post_init__(self) -> None:
        if tuple(slot for slot, _ in self.items_by_slot) != GEAR_SLOT_ORDER:
            raise ValueError("Dense snapshot slots must use canonical six-slot order.")
        expected = tuple(range(len(self.dense_id_to_stable_id)))
        if tuple(dense_id for dense_id, _ in self.dense_id_to_stable_id) != expected:
            raise ValueError("Dense snapshot reverse IDs must be contiguous from zero.")
        flattened = tuple(
            item for _, slot_items in self.items_by_slot for item in slot_items
        )
        if tuple(item.dense_id for item in flattened) != expected:
            raise ValueError("Dense snapshot item IDs must be contiguous from zero.")
        if tuple(item.item_id for item in flattened) != tuple(
            stable_id for _, stable_id in self.dense_id_to_stable_id
        ):
            raise ValueError("Dense snapshot reverse mapping must match its gear items.")

    def items_for_slot(self, slot: GearSlot) -> tuple[GearItem, ...]:
        return dict(self.items_by_slot)[GearSlot(slot)]

    def stable_item_id_for_dense_id(self, dense_id: int) -> str:
        if isinstance(dense_id, bool) or not isinstance(dense_id, int) or dense_id < 0:
            raise KeyError(dense_id)
        try:
            return self.dense_id_to_stable_id[dense_id][1]
        except IndexError:
            raise KeyError(dense_id) from None


RepositoryMigration = Callable[[sqlite3.Connection], None]
BackupNameFactory = Callable[[Path, datetime], Path]
Clock = Callable[[], datetime]


def _enum_check(values: Sequence[object]) -> str:
    return ",".join(f"'{value.value}'" for value in values)


def _migrate_0_to_1(connection: sqlite3.Connection) -> None:
    slot_values = _enum_check(GEAR_SLOT_ORDER)
    set_values = _enum_check(tuple(GearSet))
    rank_values = _enum_check(tuple(GearRank))
    material_values = _enum_check(tuple(ReforgeMaterial))
    stat_values = _enum_check(tuple(ItemStatType))
    evidence_values = _enum_check(tuple(ProjectionEvidenceState))
    identity_values = _enum_check(tuple(FribbelsIdentityKind))
    encoding_values = _enum_check(tuple(FribbelsEncoding))
    variant_values = _enum_check(tuple(FribbelsVariant))

    statements = (
        """
        CREATE TABLE repository_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE inventory_items (
            stable_item_id TEXT PRIMARY KEY,
            slot TEXT NOT NULL CHECK (slot IN ({slot_values})),
            gear_set TEXT NOT NULL CHECK (gear_set IN ({set_values})),
            rank TEXT NOT NULL CHECK (rank IN ({rank_values})),
            material TEXT CHECK (material IS NULL OR material IN ({material_values})),
            item_level INTEGER NOT NULL CHECK (item_level BETWEEN 1 AND 100),
            enhance INTEGER NOT NULL CHECK (enhance BETWEEN 0 AND 15),
            main_stat_type TEXT NOT NULL CHECK (main_stat_type IN ({stat_values})),
            main_stat_value_json TEXT NOT NULL,
            substats_json TEXT NOT NULL,
            equipped_hero_id TEXT,
            locked INTEGER NOT NULL CHECK (locked IN (0, 1)),
            current_ingame_id TEXT,
            current_source_id TEXT,
            name TEXT,
            equipped_by_name TEXT,
            projection_current_totals_json TEXT NOT NULL,
            projection_reforged_totals_json TEXT NOT NULL,
            augmented_evidence TEXT NOT NULL CHECK (augmented_evidence IN ({evidence_values})),
            reforged_evidence TEXT NOT NULL CHECK (reforged_evidence IN ({evidence_values})),
            source_metadata_json TEXT NOT NULL,
            user_metadata_json TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE item_identity_aliases (
            stable_item_id TEXT NOT NULL
                REFERENCES inventory_items(stable_item_id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ({identity_values})),
            value TEXT NOT NULL,
            PRIMARY KEY (stable_item_id, kind, value)
        )
        """,
        """
        CREATE UNIQUE INDEX uq_item_identity_strong_alias
        ON item_identity_aliases(kind, value)
        WHERE kind IN ('ingame', 'source')
        """,
        """
        CREATE UNIQUE INDEX uq_item_identity_current_fingerprint
        ON item_identity_aliases(stable_item_id)
        WHERE kind = 'fingerprint'
        """,
        """
        CREATE INDEX ix_item_identity_lookup
        ON item_identity_aliases(kind, value, stable_item_id)
        """,
        """
        CREATE INDEX ix_inventory_slot_stable_id
        ON inventory_items(slot, stable_item_id)
        """,
        """
        CREATE TABLE imported_heroes (
            hero_id TEXT PRIMARY KEY,
            name TEXT,
            stars INTEGER CHECK (stars IS NULL OR stars BETWEEN 0 AND 99),
            awaken INTEGER CHECK (awaken IS NULL OR awaken BETWEEN 0 AND 99),
            raw_metadata_json TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE import_history (
            import_id TEXT PRIMARY KEY,
            imported_at TEXT NOT NULL,
            source_encoding TEXT NOT NULL CHECK (source_encoding IN ({encoding_values})),
            source_variant TEXT NOT NULL CHECK (source_variant IN ({variant_values})),
            source_item_count INTEGER NOT NULL CHECK (source_item_count >= 0),
            accepted_count INTEGER NOT NULL CHECK (accepted_count >= 0),
            rejected_count INTEGER NOT NULL CHECK (rejected_count >= 0),
            warning_count INTEGER NOT NULL CHECK (warning_count >= 0),
            inserted_count INTEGER NOT NULL CHECK (inserted_count >= 0),
            updated_count INTEGER NOT NULL CHECK (updated_count >= 0),
            unchanged_count INTEGER NOT NULL CHECK (unchanged_count >= 0),
            conflict_count INTEGER NOT NULL CHECK (conflict_count >= 0),
            unseen_existing_count INTEGER NOT NULL CHECK (unseen_existing_count >= 0),
            source_metadata_json TEXT NOT NULL,
            CHECK (source_item_count = accepted_count + rejected_count),
            CHECK (
                accepted_count = inserted_count + updated_count
                    + unchanged_count + conflict_count
            )
        )
        """,
        """
        CREATE INDEX ix_import_history_time
        ON import_history(imported_at, import_id)
        """,
    )
    for statement in statements:
        connection.execute(statement)


INVENTORY_REPOSITORY_MIGRATIONS: Mapping[int, RepositoryMigration] = MappingProxyType(
    {0: _migrate_0_to_1}
)


def _system_clock() -> datetime:
    return datetime.now(timezone.utc)


def _default_backup_name(database_path: Path, timestamp: datetime) -> Path:
    stamp = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return database_path.with_name(f"{database_path.name}.backup-{stamp}")


def _decode_json(value: object, field: str) -> Any:
    if not isinstance(value, str):
        raise ValueError(f"{field} must contain JSON text.")

    def reject_constant(constant: str) -> None:
        raise ValueError(f"{field} contains an invalid number.")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"{field} contains a duplicate object key.")
            result[key] = item
        return result

    try:
        decoded = json.loads(
            value,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"{field} contains malformed JSON.") from error
    return thaw_json(freeze_json(decoded, field))


def _totals_payload(
    totals: Sequence[tuple[ItemStatType, int | float]],
) -> list[dict[str, object]]:
    return [{"type": stat.value, "value": value} for stat, value in totals]


def _decode_totals(value: object, field: str) -> tuple[tuple[ItemStatType, int | float], ...]:
    decoded = _decode_json(value, field)
    if not isinstance(decoded, list):
        raise ValueError(f"{field} must be an array.")
    totals: list[tuple[ItemStatType, int | float]] = []
    seen: set[ItemStatType] = set()
    for entry in decoded:
        if not isinstance(entry, Mapping) or set(entry) != {"type", "value"}:
            raise ValueError(f"{field} contains an invalid stat entry.")
        stat = ItemStatType(entry["type"])
        numeric = entry["value"]
        if isinstance(numeric, bool) or not isinstance(numeric, (int, float)):
            raise ValueError(f"{field} contains a non-numeric stat value.")
        if stat in seen:
            raise ValueError(f"{field} contains duplicate stat types.")
        seen.add(stat)
        totals.append((stat, numeric))
    return tuple(totals)


def _item_values(item: FribbelsInventoryItem) -> tuple[object, ...]:
    gear = item.gear_item
    return (
        item.stable_item_id,
        gear.slot.value,
        gear.gear_set.value,
        item.rank.value,
        None if item.material is None else item.material.value,
        gear.item_level,
        gear.enhance,
        gear.main_stat.value,
        deterministic_json(gear.main_stat_value),
        deterministic_json(
            [{"type": stat.value, "value": value} for stat, value in gear.substats]
        ),
        gear.equipped_hero_id,
        int(gear.locked),
        item.current_ingame_id,
        item.current_source_id,
        item.name,
        item.equipped_by_name,
        deterministic_json(_totals_payload(item.projection.current_totals)),
        deterministic_json(_totals_payload(item.projection.reforged_totals)),
        item.projection.augmented_evidence.value,
        item.projection.reforged_evidence.value,
        deterministic_json(item.source_metadata),
        deterministic_json(item.user_metadata),
    )


_UPSERT_ITEM_SQL = """
INSERT INTO inventory_items (
    stable_item_id, slot, gear_set, rank, material, item_level, enhance,
    main_stat_type, main_stat_value_json, substats_json, equipped_hero_id,
    locked, current_ingame_id, current_source_id, name, equipped_by_name,
    projection_current_totals_json, projection_reforged_totals_json,
    augmented_evidence, reforged_evidence, source_metadata_json,
    user_metadata_json
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
ON CONFLICT(stable_item_id) DO UPDATE SET
    slot = excluded.slot,
    gear_set = excluded.gear_set,
    rank = excluded.rank,
    material = excluded.material,
    item_level = excluded.item_level,
    enhance = excluded.enhance,
    main_stat_type = excluded.main_stat_type,
    main_stat_value_json = excluded.main_stat_value_json,
    substats_json = excluded.substats_json,
    equipped_hero_id = excluded.equipped_hero_id,
    locked = excluded.locked,
    current_ingame_id = excluded.current_ingame_id,
    current_source_id = excluded.current_source_id,
    name = excluded.name,
    equipped_by_name = excluded.equipped_by_name,
    projection_current_totals_json = excluded.projection_current_totals_json,
    projection_reforged_totals_json = excluded.projection_reforged_totals_json,
    augmented_evidence = excluded.augmented_evidence,
    reforged_evidence = excluded.reforged_evidence,
    source_metadata_json = excluded.source_metadata_json,
    user_metadata_json = excluded.user_metadata_json
"""


class InventoryRepository:
    """Own SQLite lifecycle and atomic inventory persistence."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        current_schema_version: int = INVENTORY_REPOSITORY_SCHEMA_VERSION,
        migrations: Mapping[int, RepositoryMigration] = INVENTORY_REPOSITORY_MIGRATIONS,
        clock: Clock = _system_clock,
        backup_name_factory: BackupNameFactory = _default_backup_name,
    ) -> None:
        if not isinstance(database_path, (str, os.PathLike)):
            raise ValueError("database_path must be a filesystem path.")
        path = Path(database_path)
        if not str(path).strip():
            raise ValueError("database_path must not be empty.")
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or not 1 <= busy_timeout_ms <= MAX_BUSY_TIMEOUT_MS
        ):
            raise ValueError(
                f"busy_timeout_ms must be between 1 and {MAX_BUSY_TIMEOUT_MS}."
            )
        if (
            isinstance(current_schema_version, bool)
            or not isinstance(current_schema_version, int)
            or current_schema_version < 1
        ):
            raise ValueError("current_schema_version must be a positive integer.")
        self.database_path = lexical_absolute_path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.current_schema_version = current_schema_version
        self.migrations = MappingProxyType(dict(migrations))
        self.clock = clock
        self.backup_name_factory = backup_name_factory

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.busy_timeout_ms / 1_000,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            if enabled != 1:
                connection.close()
                raise InventoryRepositorySchemaError(
                    "SQLite foreign-key enforcement could not be enabled."
                )
            return connection
        except InventoryRepositoryError:
            raise
        except sqlite3.Error as error:
            raise InventoryRepositorySchemaError(
                "The inventory database could not be opened."
            ) from error

    @staticmethod
    def _schema_version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _verify_current_schema(self, connection: sqlite3.Connection) -> None:
        version = self._schema_version(connection)
        if version > self.current_schema_version:
            raise InventoryRepositorySchemaError(
                f"Inventory schema version {version} is newer than supported version "
                f"{self.current_schema_version}; update the application before opening it."
            )
        if version != self.current_schema_version:
            raise InventoryRepositorySchemaError(
                "The inventory database requires initialization or migration before use."
            )
        try:
            metadata = dict(
                connection.execute(
                    "SELECT key, value FROM repository_metadata "
                    "WHERE key IN ('repository_kind', 'schema_version')"
                ).fetchall()
            )
        except sqlite3.Error as error:
            raise InventoryRepositorySchemaError(
                "The inventory schema metadata is missing or unreadable."
            ) from error
        if metadata.get("repository_kind") != INVENTORY_REPOSITORY_KIND:
            raise InventoryRepositorySchemaError(
                "The database is not a recognized inventory repository."
            )
        if metadata.get("schema_version") != str(version):
            raise InventoryRepositorySchemaError(
                "Inventory schema version markers do not agree."
            )

    def _next_backup_path(self, timestamp: datetime) -> Path:
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise InventoryRepositorySchemaError(
                "The migration clock must return a timezone-aware datetime."
            )
        requested = Path(self.backup_name_factory(self.database_path, timestamp))
        if not requested.is_absolute():
            requested = self.database_path.parent / requested
        requested = lexical_absolute_path(requested)
        if not same_existing_path(requested.parent, self.database_path.parent):
            raise InventoryRepositorySchemaError(
                "Inventory migration backups must be adjacent to the database."
            )
        candidate = requested
        suffix = 1
        while candidate.exists():
            candidate = requested.with_name(f"{requested.name}-{suffix}")
            suffix += 1
        return candidate

    def _create_backup(self, connection: sqlite3.Connection) -> Path:
        backup_path = self._next_backup_path(self.clock())
        destination: sqlite3.Connection | None = None
        try:
            destination = sqlite3.connect(backup_path)
            connection.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise sqlite3.DatabaseError("backup integrity check failed")
            destination.close()
            destination = None
            return backup_path
        except Exception as error:
            if destination is not None:
                destination.close()
            if backup_path.exists():
                backup_path.unlink()
            raise InventoryRepositorySchemaError(
                "A recoverable inventory migration backup could not be created."
            ) from error

    @staticmethod
    def _record_schema_version(connection: sqlite3.Connection, version: int) -> None:
        connection.execute(
            "INSERT INTO repository_metadata(key, value) VALUES('repository_kind', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (INVENTORY_REPOSITORY_KIND,),
        )
        connection.execute(
            "INSERT INTO repository_metadata(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
        connection.execute(f"PRAGMA user_version = {version}")

    def initialize(self) -> RepositoryInitialization:
        """Create or migrate the database and return actionable recovery state."""

        existed = self.database_path.exists()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        backup_path: Path | None = None
        try:
            previous_version = self._schema_version(connection)
            if previous_version > self.current_schema_version:
                raise InventoryRepositorySchemaError(
                    f"Inventory schema version {previous_version} is newer than supported "
                    f"version {self.current_schema_version}; update the application before "
                    "opening it."
                )
            missing = [
                version
                for version in range(previous_version, self.current_schema_version)
                if version not in self.migrations
            ]
            if missing:
                raise InventoryRepositorySchemaError(
                    "The inventory database needs a migration that this application does "
                    "not provide; update or reinstall the application."
                )
            if previous_version < self.current_schema_version:
                if existed:
                    backup_path = self._create_backup(connection)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    version = previous_version
                    while version < self.current_schema_version:
                        self.migrations[version](connection)
                        version += 1
                        self._record_schema_version(connection, version)
                    connection.commit()
                except Exception as error:
                    connection.rollback()
                    recovery = (
                        " The original database was not advanced; recoverable backup: "
                        f"{backup_path}."
                        if backup_path is not None
                        else " The new database was not initialized."
                    )
                    raise InventoryRepositoryMigrationError(
                        "Inventory schema migration failed and was rolled back." + recovery,
                        backup_path=backup_path,
                    ) from error
            self._verify_current_schema(connection)
            return RepositoryInitialization(
                database_path=self.database_path,
                previous_version=previous_version,
                schema_version=self.current_schema_version,
                created=not existed,
                backup_path=backup_path,
            )
        finally:
            connection.close()

    @contextmanager
    def _current_connection(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.exists():
            raise InventoryRepositorySchemaError(
                "The inventory database has not been initialized."
            )
        connection = self._connect()
        try:
            self._verify_current_schema(connection)
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _validate_history(
        merge_result: FribbelsMergeResult,
        history: ImportHistoryRecord,
    ) -> None:
        expected = {
            "accepted_count": len(merge_result.outcomes),
            "rejected_count": len(merge_result.source_rejections),
            "warning_count": len(merge_result.source_warnings),
            "inserted_count": len(merge_result.inserted),
            "updated_count": len(merge_result.updated),
            "unchanged_count": len(merge_result.unchanged),
            "conflict_count": len(merge_result.conflicts),
            "unseen_existing_count": len(merge_result.unseen_existing_ids),
        }
        if any(getattr(history, field) != value for field, value in expected.items()):
            raise ValueError("Import history counts do not match the supplied merge result.")

    def apply_import(
        self,
        merge_result: FribbelsMergeResult,
        heroes: Sequence[ImportedHeroReference],
        history: ImportHistoryRecord,
    ) -> None:
        """Atomically apply complete item/hero state and append import history."""

        if not isinstance(merge_result, FribbelsMergeResult):
            raise ValueError("merge_result must be a FribbelsMergeResult.")
        if not isinstance(history, ImportHistoryRecord):
            raise ValueError("history must be an ImportHistoryRecord.")
        hero_rows = tuple(heroes)
        if not all(isinstance(hero, ImportedHeroReference) for hero in hero_rows):
            raise ValueError("heroes must contain ImportedHeroReference values.")
        self._validate_history(merge_result, history)

        with self._current_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TEMP TABLE incoming_inventory_ids "
                    "(stable_item_id TEXT PRIMARY KEY) WITHOUT ROWID"
                )
                connection.executemany(
                    "INSERT INTO incoming_inventory_ids(stable_item_id) VALUES(?)",
                    ((item.stable_item_id,) for item in merge_result.items),
                )
                connection.execute(
                    "DELETE FROM inventory_items WHERE NOT EXISTS ("
                    "SELECT 1 FROM incoming_inventory_ids incoming "
                    "WHERE incoming.stable_item_id = inventory_items.stable_item_id)"
                )
                connection.executemany(
                    _UPSERT_ITEM_SQL,
                    (_item_values(item) for item in merge_result.items),
                )
                connection.execute(
                    "DELETE FROM item_identity_aliases WHERE stable_item_id IN "
                    "(SELECT stable_item_id FROM incoming_inventory_ids)"
                )
                connection.executemany(
                    "INSERT INTO item_identity_aliases(stable_item_id, kind, value) "
                    "VALUES(?, ?, ?)",
                    (
                        (item.stable_item_id, identity.kind.value, identity.value)
                        for item in merge_result.items
                        for identity in item.identities
                    ),
                )
                connection.execute("DELETE FROM imported_heroes")
                connection.executemany(
                    "INSERT INTO imported_heroes(hero_id, name, stars, awaken, raw_metadata_json) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (
                        (
                            hero.hero_id,
                            hero.name,
                            hero.stars,
                            hero.awaken,
                            deterministic_json(hero.raw),
                        )
                        for hero in hero_rows
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO import_history (
                        import_id, imported_at, source_encoding, source_variant,
                        source_item_count, accepted_count, rejected_count, warning_count,
                        inserted_count, updated_count, unchanged_count, conflict_count,
                        unseen_existing_count, source_metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        history.import_id,
                        history.imported_at,
                        history.source_encoding.value,
                        history.source_variant.value,
                        history.source_item_count,
                        history.accepted_count,
                        history.rejected_count,
                        history.warning_count,
                        history.inserted_count,
                        history.updated_count,
                        history.unchanged_count,
                        history.conflict_count,
                        history.unseen_existing_count,
                        deterministic_json(history.source_metadata),
                    ),
                )
                connection.commit()
            except Exception as error:
                connection.rollback()
                raise InventoryRepositoryWriteError(
                    "The inventory import could not be committed; no repository changes "
                    "were applied."
                ) from error

    @staticmethod
    def _row_to_item(
        row: sqlite3.Row,
        identities: Sequence[FribbelsItemIdentity],
    ) -> FribbelsInventoryItem:
        main_value = _decode_json(row["main_stat_value_json"], "main stat value")
        raw_substats = _decode_json(row["substats_json"], "substats")
        if not isinstance(raw_substats, list):
            raise ValueError("substats must be an array.")
        substats: list[tuple[ItemStatType, int | float]] = []
        for entry in raw_substats:
            if not isinstance(entry, Mapping) or set(entry) != {"type", "value"}:
                raise ValueError("substats contain an invalid entry.")
            substats.append((ItemStatType(entry["type"]), entry["value"]))
        gear = GearItem(
            item_id=row["stable_item_id"],
            dense_id=None,
            slot=GearSlot(row["slot"]),
            gear_set=GearSet(row["gear_set"]),
            item_level=row["item_level"],
            enhance=row["enhance"],
            main_stat=ItemStatType(row["main_stat_type"]),
            main_stat_value=main_value,
            substats=tuple(substats),
            equipped_hero_id=row["equipped_hero_id"],
            locked=bool(row["locked"]),
        )
        projection = FribbelsItemProjection(
            current_totals=_decode_totals(
                row["projection_current_totals_json"],
                "current projection totals",
            ),
            reforged_totals=_decode_totals(
                row["projection_reforged_totals_json"],
                "reforged projection totals",
            ),
            augmented_evidence=ProjectionEvidenceState(row["augmented_evidence"]),
            reforged_evidence=ProjectionEvidenceState(row["reforged_evidence"]),
        )
        return FribbelsInventoryItem(
            stable_item_id=row["stable_item_id"],
            gear_item=gear,
            identities=tuple(identities),
            current_ingame_id=row["current_ingame_id"],
            current_source_id=row["current_source_id"],
            name=row["name"],
            rank=GearRank(row["rank"]),
            material=(
                None if row["material"] is None else ReforgeMaterial(row["material"])
            ),
            equipped_by_name=row["equipped_by_name"],
            projection=projection,
            source_metadata=freeze_json_object(
                _decode_json(row["source_metadata_json"], "source metadata"),
                "source metadata",
            ),
            user_metadata=freeze_json_object(
                _decode_json(row["user_metadata_json"], "user metadata"),
                "user metadata",
            ),
        )

    def load_inventory(self) -> tuple[FribbelsInventoryItem, ...]:
        with self._current_connection() as connection:
            try:
                aliases: dict[str, list[FribbelsItemIdentity]] = {}
                for row in connection.execute(
                    "SELECT stable_item_id, kind, value FROM item_identity_aliases "
                    "ORDER BY stable_item_id, CASE kind WHEN 'ingame' THEN 0 "
                    "WHEN 'source' THEN 1 ELSE 2 END, value"
                ):
                    aliases.setdefault(row["stable_item_id"], []).append(
                        FribbelsItemIdentity(row["kind"], row["value"])
                    )
                return tuple(
                    self._row_to_item(row, aliases.get(row["stable_item_id"], ()))
                    for row in connection.execute(
                        "SELECT * FROM inventory_items ORDER BY stable_item_id"
                    )
                )
            except Exception as error:
                raise InventoryRepositoryReadError(
                    "Stored inventory state could not be reconstructed."
                ) from error

    def load_heroes(self) -> tuple[ImportedHeroReference, ...]:
        with self._current_connection() as connection:
            try:
                return tuple(
                    ImportedHeroReference(
                        hero_id=row["hero_id"],
                        name=row["name"],
                        stars=row["stars"],
                        awaken=row["awaken"],
                        raw=freeze_json_object(
                            _decode_json(row["raw_metadata_json"], "hero metadata"),
                            "hero metadata",
                        ),
                    )
                    for row in connection.execute(
                        "SELECT * FROM imported_heroes ORDER BY hero_id"
                    )
                )
            except Exception as error:
                raise InventoryRepositoryReadError(
                    "Stored imported hero references could not be reconstructed."
                ) from error

    def load_import_history(self) -> tuple[ImportHistoryRecord, ...]:
        with self._current_connection() as connection:
            try:
                return tuple(
                    ImportHistoryRecord(
                        import_id=row["import_id"],
                        imported_at=row["imported_at"],
                        source_encoding=row["source_encoding"],
                        source_variant=row["source_variant"],
                        source_item_count=row["source_item_count"],
                        accepted_count=row["accepted_count"],
                        rejected_count=row["rejected_count"],
                        warning_count=row["warning_count"],
                        inserted_count=row["inserted_count"],
                        updated_count=row["updated_count"],
                        unchanged_count=row["unchanged_count"],
                        conflict_count=row["conflict_count"],
                        unseen_existing_count=row["unseen_existing_count"],
                        source_metadata=freeze_json_object(
                            _decode_json(
                                row["source_metadata_json"],
                                "import source metadata",
                            ),
                            "import source metadata",
                        ),
                    )
                    for row in connection.execute(
                        "SELECT * FROM import_history ORDER BY imported_at, import_id"
                    )
                )
            except Exception as error:
                raise InventoryRepositoryReadError(
                    "Stored import history could not be reconstructed."
                ) from error

    def assign_equipment_build(
        self,
        target_hero_id: str,
        target_hero_name: str,
        stable_item_ids: Sequence[str],
    ) -> EquipmentAssignmentResult:
        """Assign exactly one owned item per slot to one imported hero atomically."""

        hero_id = _required_text(target_hero_id, "target_hero_id")
        hero_name = _required_text(target_hero_name, "target_hero_name")
        item_ids = tuple(
            _required_text(item_id, f"stable_item_ids[{index}]")
            for index, item_id in enumerate(stable_item_ids)
        )
        if len(item_ids) != len(GEAR_SLOT_ORDER) or len(set(item_ids)) != len(item_ids):
            raise ValueError("stable_item_ids must contain six unique owned item IDs.")

        placeholders = ",".join("?" for _ in item_ids)
        with self._current_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                hero = connection.execute(
                    "SELECT name FROM imported_heroes WHERE hero_id = ?",
                    (hero_id,),
                ).fetchone()
                if hero is None:
                    raise ValueError("The selected hero is absent from the imported gear data.")

                rows = connection.execute(
                    "SELECT stable_item_id, slot, equipped_hero_id "
                    f"FROM inventory_items WHERE stable_item_id IN ({placeholders})",
                    item_ids,
                ).fetchall()
                if len(rows) != len(item_ids):
                    raise ValueError("The selected build contains gear that is no longer owned.")
                if {GearSlot(row["slot"]) for row in rows} != set(GEAR_SLOT_ORDER):
                    raise ValueError("The selected build must contain exactly one item per gear slot.")

                selected = set(item_ids)
                current_target_ids = {
                    row["stable_item_id"]
                    for row in connection.execute(
                        "SELECT stable_item_id FROM inventory_items WHERE equipped_hero_id = ?",
                        (hero_id,),
                    )
                }
                already_on_target = sum(
                    row["equipped_hero_id"] == hero_id for row in rows
                )
                moved_from_other_heroes = sum(
                    row["equipped_hero_id"] is not None
                    and row["equipped_hero_id"] != hero_id
                    for row in rows
                )
                newly_equipped_items = sum(
                    row["equipped_hero_id"] is None for row in rows
                )
                unequipped_from_target = len(current_target_ids - selected)

                connection.execute(
                    "UPDATE inventory_items "
                    "SET equipped_hero_id = NULL, equipped_by_name = NULL "
                    "WHERE equipped_hero_id = ?",
                    (hero_id,),
                )
                connection.execute(
                    "UPDATE inventory_items "
                    "SET equipped_hero_id = ?, equipped_by_name = ? "
                    f"WHERE stable_item_id IN ({placeholders})",
                    (hero_id, hero_name, *item_ids),
                )
                total_equipped = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM inventory_items "
                        "WHERE equipped_hero_id IS NOT NULL"
                    ).fetchone()[0]
                )
                connection.commit()
                return EquipmentAssignmentResult(
                    assigned_items=len(item_ids),
                    already_on_target=already_on_target,
                    moved_from_other_heroes=moved_from_other_heroes,
                    newly_equipped_items=newly_equipped_items,
                    unequipped_from_target=unequipped_from_target,
                    total_equipped_items=total_equipped,
                )
            except ValueError:
                connection.rollback()
                raise
            except Exception as error:
                connection.rollback()
                raise InventoryRepositoryWriteError(
                    "The selected build could not be equipped; no ownership changes were applied."
                ) from error

    def inventory_summary(self) -> InventorySummary:
        with self._current_connection() as connection:
            counts = connection.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(equipped_hero_id IS NOT NULL), 0) AS equipped, "
                "COALESCE(SUM(locked), 0) AS locked FROM inventory_items"
            ).fetchone()
            slot_counts = {
                GearSlot(row["slot"]): row["item_count"]
                for row in connection.execute(
                    "SELECT slot, COUNT(*) AS item_count FROM inventory_items GROUP BY slot"
                )
            }
            alias_counts = {
                FribbelsIdentityKind(row["kind"]): row["alias_count"]
                for row in connection.execute(
                    "SELECT kind, COUNT(*) AS alias_count FROM item_identity_aliases "
                    "GROUP BY kind"
                )
            }
            heroes = connection.execute("SELECT COUNT(*) FROM imported_heroes").fetchone()[0]
            imports = connection.execute("SELECT COUNT(*) FROM import_history").fetchone()[0]
            return InventorySummary(
                total_items=counts["total"],
                equipped_items=counts["equipped"],
                locked_items=counts["locked"],
                imported_heroes=heroes,
                import_history_records=imports,
                ingame_aliases=alias_counts.get(FribbelsIdentityKind.INGAME, 0),
                source_aliases=alias_counts.get(FribbelsIdentityKind.SOURCE, 0),
                fingerprint_aliases=alias_counts.get(FribbelsIdentityKind.FINGERPRINT, 0),
                items_by_slot=tuple(
                    (slot, slot_counts.get(slot, 0)) for slot in GEAR_SLOT_ORDER
                ),
            )

    def dense_snapshot(self) -> DenseInventorySnapshot:
        stored = self.load_inventory()
        by_slot = {slot: [] for slot in GEAR_SLOT_ORDER}
        reverse: list[tuple[int, str]] = []
        dense_id = 0
        for slot in GEAR_SLOT_ORDER:
            for item in sorted(
                (state for state in stored if state.gear_item.slot is slot),
                key=lambda state: state.stable_item_id,
            ):
                by_slot[slot].append(replace(item.gear_item, dense_id=dense_id))
                reverse.append((dense_id, item.stable_item_id))
                dense_id += 1
        return DenseInventorySnapshot(
            items_by_slot=tuple((slot, tuple(by_slot[slot])) for slot in GEAR_SLOT_ORDER),
            dense_id_to_stable_id=tuple(reverse),
        )
