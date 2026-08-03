"""Privacy-safe aggregate inventory operations for the Electron desktop UI."""

from __future__ import annotations

import json
import math
import uuid
import os
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.optimizer.data import (
    FribbelsImportReport,
    FribbelsImportRequest,
    FribbelsImportService,
    FribbelsImportServiceError,
    InventoryRepository,
    InventoryRepositoryError,
    InventoryRepositoryMigrationError,
    InventoryRepositoryReadError,
    InventoryRepositorySchemaError,
    load_bundled_character_repository,
    resolve_inventory_database_path,
)
from src.optimizer.engine import (
    DerivedMetricError,
    ProjectedGearItem,
    StatAggregationError,
    calculate_item_gear_score,
)
from src.core.live_packet_source import (
    LivePacketSource,
    PacketCaptureUnavailable,
)
from src.core.packet_inventory import PacketInventoryError, normalize_account_inventory
from src.optimizer.domain import (
    GEAR_RANK_CATALOG,
    GEAR_SLOT_CATALOG,
    GEAR_SLOT_ORDER,
    ITEM_STAT_CATALOG,
    SET_CATALOG,
    ItemProjectionMode,
    ItemStatType,
)


MAX_DESKTOP_IMPORT_ISSUES = 20
_OPTIMIZER_DATABASE_FILES = ("optimizer.db", "optimizer.db-wal", "optimizer.db-shm")
_OPTIMIZER_DATA_DIRECTORIES = (
    "optimizer_profiles",
    "optimizer_results",
    "optimizer_result_sort_cache",
)


class OptimizerInventoryServiceError(RuntimeError):
    """A stable, path-free error suitable for the desktop protocol."""

    def __init__(
        self,
        category: str,
        *,
        code: str,
        message: str,
        document_path: str | None = None,
    ) -> None:
        self.category = category
        self.code = code
        self.document_path = document_path
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Desktop inventory clock must return a timezone-aware value.")
    normalized = value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return normalized.replace("+00:00", "Z")


def _slot_counts(values: tuple[tuple[object, int], ...]) -> list[dict[str, Any]]:
    counts = dict(values)
    return [
        {
            "slot": slot.value,
            "label": GEAR_SLOT_CATALOG[slot].display_name,
            "count": counts[slot],
        }
        for slot in GEAR_SLOT_ORDER
    ]


def _history_summary(record: object | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "importedAt": record.imported_at,
        "sourceEncoding": record.source_encoding.value,
        "sourceVariant": record.source_variant.value,
        "sourceItemCount": record.source_item_count,
        "acceptedCount": record.accepted_count,
        "rejectedCount": record.rejected_count,
        "warningCount": record.warning_count,
        "insertedCount": record.inserted_count,
        "updatedCount": record.updated_count,
        "unchangedCount": record.unchanged_count,
        "conflictCount": record.conflict_count,
        "unseenExistingCount": record.unseen_existing_count,
    }


def _empty_snapshot() -> dict[str, Any]:
    return {
        "state": "empty",
        "totalItems": 0,
        "equippedItems": 0,
        "lockedItems": 0,
        "gear": [],
        "lastImport": None,
        "itemsBySlot": [
            {
                "slot": slot.value,
                "label": GEAR_SLOT_CATALOG[slot].display_name,
                "count": 0,
            }
            for slot in GEAR_SLOT_ORDER
        ],
    }


def _reforged_scores(item: object) -> tuple[int, int, int]:
    projected = ProjectedGearItem.from_fribbels_inventory_item(item)
    totals = dict(projected.totals_for(ItemProjectionMode.REFORGED))
    assert projected.main_stat is not None
    main_value = projected.main_value_for(ItemProjectionMode.REFORGED)
    reforged = calculate_item_gear_score(
        projected.item_id,
        totals,
        projected.main_stat,
        main_value,
    ).score
    totals[projected.main_stat] -= main_value

    attack = (
        totals[ItemStatType.ATTACK_PERCENT]
        + totals[ItemStatType.FLAT_ATTACK] * (3.46 / 39.0)
    )
    health = (
        totals[ItemStatType.HEALTH_PERCENT]
        + totals[ItemStatType.FLAT_HEALTH] * (3.09 / 174.0)
    )
    defense = (
        totals[ItemStatType.DEFENSE_PERCENT]
        + totals[ItemStatType.FLAT_DEFENSE] * (4.99 / 31.0)
    )
    speed = totals[ItemStatType.SPEED] * 2
    critical = (
        totals[ItemStatType.CRITICAL_HIT_CHANCE_PERCENT] * (8.0 / 5.0)
        + totals[ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT] * (8.0 / 7.0)
    )
    combat = math.floor(attack + health + defense + speed + critical + 0.5)
    support = math.floor(
        health
        + defense
        + speed
        + totals[ItemStatType.EFFECT_RESISTANCE_PERCENT]
        + 0.5
    )
    return reforged, combat, support


def _public_gear(repository: InventoryRepository) -> list[dict[str, Any]]:
    owners = {hero.hero_id: hero.name for hero in repository.load_heroes()}
    result = []
    for stored in repository.load_inventory():
        gear = stored.gear_item
        if gear.enhance != 15:
            continue
        reforged, combat, support = _reforged_scores(stored)
        owner_name = (
            owners.get(gear.equipped_hero_id)
            if gear.equipped_hero_id is not None
            else None
        ) or stored.equipped_by_name
        result.append({
            "gearKey": f"gear-{len(result) + 1}",
            "slotId": gear.slot.value,
            "slotLabel": GEAR_SLOT_CATALOG[gear.slot].display_name,
            "setId": gear.gear_set.value,
            "setLabel": SET_CATALOG[gear.gear_set].display_name,
            "rankId": stored.rank.value,
            "rankLabel": GEAR_RANK_CATALOG[stored.rank].display_name,
            "itemLevel": gear.item_level,
            "enhance": gear.enhance,
            "gearScore": reforged,
            "reforgedGearScore": reforged,
            "combatGearScore": combat,
            "supportGearScore": support,
            "locked": gear.locked,
            "equippedStatus": (
                "unequipped" if gear.equipped_hero_id is None else "other-hero"
            ),
            "equippedHeroName": owner_name,
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
                }
                for stat, value in gear.substats
            ],
        })
    return result


def _ready_snapshot(repository: InventoryRepository) -> dict[str, Any]:
    summary = repository.inventory_summary()
    history = repository.load_import_history()
    return {
        "state": "ready",
        "totalItems": summary.total_items,
        "equippedItems": summary.equipped_items,
        "lockedItems": summary.locked_items,
        "gear": _public_gear(repository),
        "lastImport": _history_summary(history[-1] if history else None),
        "itemsBySlot": _slot_counts(summary.items_by_slot),
    }


def _serialize_report(report: FribbelsImportReport) -> dict[str, Any]:
    visible_issues = report.issues[:MAX_DESKTOP_IMPORT_ISSUES]
    return {
        "importedAt": report.imported_at,
        "sourceEncoding": report.source_encoding.value,
        "sourceVariant": report.source_variant.value,
        "sourceItemCount": report.source_item_count,
        "acceptedCount": report.accepted_count,
        "rejectedCount": report.rejected_count,
        "warningCount": report.warning_count,
        "warningItemCount": report.warning_item_count,
        "insertedCount": report.inserted_count,
        "updatedCount": report.updated_count,
        "unchangedCount": report.unchanged_count,
        "conflictCount": report.conflict_count,
        "unseenExistingCount": report.unseen_existing_count,
        "equippedItemCount": report.equipped_item_count,
        "importedHeroCount": report.imported_hero_count,
        "resultingInventoryCount": report.resulting_inventory_count,
        "repositoryCreated": report.repository_created,
        "repositoryMigrated": report.repository_migrated,
        "issues": [
            {
                "kind": issue.kind.value,
                "code": issue.code,
                "documentPath": issue.document_path,
                "message": issue.message,
                "itemIndex": issue.item_index,
                "heroIndex": issue.hero_index,
            }
            for issue in visible_issues
        ],
        "additionalIssueCount": len(report.issues) - len(visible_issues),
    }


class OptimizerInventoryService:
    """Expose only bounded aggregate inventory state to desktop callers."""

    def __init__(
        self,
        user_data_dir: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
        import_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        packet_source_factory: Callable[[], Any] = LivePacketSource,
        inventory_normalizer: Callable[
            [Sequence[bytes]], Mapping[str, Any]
        ] | None = None,
        capture_directory: str | Path | None = None,
    ) -> None:
        self.database_path = resolve_inventory_database_path(
            {"E7_USER_DATA_DIR": str(user_data_dir)}
        )
        self.clock = clock
        self.import_id_factory = import_id_factory
        self.packet_source_factory = packet_source_factory
        self.inventory_normalizer = inventory_normalizer
        documents = (
            Path(capture_directory)
            if capture_directory is not None
            else Path(os.environ.get("E7_DOCUMENTS_DIR") or Path.home() / "Documents")
        )
        self.capture_document_path = documents / "MeowtokoE7Hub" / "gear.txt"
        self._capture_lock = threading.Lock()
        self._capture_source: Any = None

    def get_snapshot(self) -> dict[str, Any]:
        if not self.database_path.exists():
            return _empty_snapshot()
        repository = InventoryRepository(self.database_path)
        try:
            repository.initialize()
            return _ready_snapshot(repository)
        except InventoryRepositoryMigrationError as error:
            raise OptimizerInventoryServiceError(
                "repository-initialization",
                code="repository-migration",
                message="The inventory database migration failed and was rolled back.",
            ) from error
        except (
            DerivedMetricError,
            InventoryRepositorySchemaError,
            InventoryRepositoryReadError,
            InventoryRepositoryError,
            StatAggregationError,
        ) as error:
            raise OptimizerInventoryServiceError(
                "repository-read",
                code="repository-read",
                message="The saved inventory summary could not be read safely.",
            ) from error

    def import_file(self, source_path: str | Path) -> dict[str, Any]:
        try:
            selected = Path(source_path)
        except (TypeError, ValueError) as error:
            raise OptimizerInventoryServiceError(
                "source-selection",
                code="file-selection",
                message="Choose a Fribbels gear.txt file.",
            ) from error
        if selected.suffix.casefold() != ".txt":
            raise OptimizerInventoryServiceError(
                "source-selection",
                code="file-type",
                message="Choose a Fribbels gear.txt file.",
            )

        return self._import_file(selected, source_kind="desktop-native-file-picker")

    def _import_file(self, selected: Path, *, source_kind: str) -> dict[str, Any]:
        repository = InventoryRepository(self.database_path)
        request = FribbelsImportRequest(
            source_path=selected,
            import_id=self.import_id_factory(),
            imported_at=_timestamp(self.clock()),
            privacy_safe_source_metadata={"sourceKind": source_kind},
        )
        try:
            report = FribbelsImportService(repository).import_file(request)
            snapshot = _ready_snapshot(repository)
        except FribbelsImportServiceError as error:
            raise OptimizerInventoryServiceError(
                error.category.value,
                code=error.code,
                message=str(error),
                document_path=error.document_path,
            ) from error
        except (
            DerivedMetricError,
            InventoryRepositoryReadError,
            InventoryRepositoryError,
            StatAggregationError,
        ) as error:
            raise OptimizerInventoryServiceError(
                "repository-read",
                code="repository-read",
                message="The imported inventory summary could not be read safely.",
            ) from error
        return {"inventory": snapshot, "report": _serialize_report(report)}

    def start_game_inventory_capture(self) -> dict[str, str]:
        """Start a capture session and return immediately."""

        with self._capture_lock:
            if self._capture_source is not None:
                return {"state": "capturing"}
            source = self.packet_source_factory()
            try:
                source.start()
            except PacketCaptureUnavailable as error:
                raise OptimizerInventoryServiceError(
                    "packet-capture",
                    code="capture-unavailable",
                    message=str(error),
                ) from error
            self._capture_source = source
            return {"state": "capturing"}

    def finish_game_inventory_capture(self) -> dict[str, Any]:
        """Import the latest account snapshot and close the capture session."""

        with self._capture_lock:
            source = self._capture_source
            if source is None:
                raise OptimizerInventoryServiceError(
                    "packet-capture",
                    code="capture-not-started",
                    message="Start capturing before finishing the game import.",
                )
            try:
                payload_reader = getattr(source, "captured_payloads", None)
                payloads = payload_reader() if callable(payload_reader) else []
                if not payloads:
                    raise OptimizerInventoryServiceError(
                        "packet-capture",
                        code="account-packet-missing",
                        message=self._missing_account_message(source),
                    )
                if self.inventory_normalizer is None:
                    raise OptimizerInventoryServiceError(
                        "packet-capture",
                        code="capture-service-unavailable",
                        message="The private packet service is unavailable.",
                    )
                try:
                    document = self.inventory_normalizer(payloads)
                except Exception as error:
                    raise OptimizerInventoryServiceError(
                        "packet-capture",
                        code="account-packet-missing",
                        message=(
                            "The packet service did not recognize an account inventory "
                            "snapshot. Fully reopen Epic Seven, then start a new capture."
                        ),
                    ) from error
                return self._save_and_import_document(document)
            finally:
                source.stop()
                self._capture_source = None

    @staticmethod
    def _missing_account_message(source: Any) -> str:
        status_reader = getattr(source, "capture_status", None)
        status = status_reader() if callable(status_reader) else None
        if not isinstance(status, Mapping):
            return (
                "No account inventory packet has arrived yet. Fully reopen Epic Seven, "
                "then start a new capture."
            )
        packets = int(status.get("packetsSeen") or 0)
        game_packets = int(status.get("gamePacketsSeen") or 0)
        if status.get("running") is not True:
            return (
                "Packet capture stopped unexpectedly. Restart capture and check Npcap "
                "in Health Center before reopening Epic Seven."
            )
        if game_packets == 0:
            adapters = int(status.get("activeAdapters") or 0)
            raw_ports = status.get("observedTcpSourcePorts")
            ports = []
            if isinstance(raw_ports, Sequence) and not isinstance(raw_ports, (str, bytes)):
                for value in raw_ports[:5]:
                    if not isinstance(value, Mapping):
                        continue
                    try:
                        port = int(value.get("port") or 0)
                        count = int(value.get("packets") or 0)
                    except (TypeError, ValueError):
                        continue
                    if 0 < port <= 65_535 and count > 0:
                        ports.append(f"{port} ({count:,})")
            evidence = (
                f" Capture received packets on {adapters:,} adapter"
                f"{'s' if adapters != 1 else ''}."
                if adapters
                else ""
            )
            if ports:
                evidence += f" TCP payload source ports observed: {', '.join(ports)}."
            return (
                f"Packet capture is running and saw {packets:,} network packets, but no "
                f"supported game response traffic.{evidence} Fully exit the game instead "
                "of minimizing "
                "it, reopen it to the main screen, then start a new capture."
            )
        return (
            f"Captured {game_packets:,} supported game packets, but no complete account "
            "snapshot was available. Fully reopen Epic Seven, then start a new capture."
        )

    def close(self) -> None:
        with self._capture_lock:
            source = self._capture_source
            self._capture_source = None
            if source is not None:
                source.stop()

    def import_account_data(self, account_data: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize decoded account data for bounded tests and recovery tools."""

        try:
            characters = load_bundled_character_repository()
            hero_names = {
                hero.source_code: hero.name
                for hero in characters.heroes
            }
            document, _skipped = normalize_account_inventory(
                account_data,
                hero_names=hero_names,
            )
        except (PacketInventoryError, TypeError, ValueError, RuntimeError) as error:
            raise OptimizerInventoryServiceError(
                "packet-document",
                code="invalid-account-packet",
                message=f"The captured account inventory could not be interpreted: {error}",
            ) from error
        return self._save_and_import_document(document)

    def _save_and_import_document(self, document: Mapping[str, Any]) -> dict[str, Any]:
        try:
            data = json.dumps(
                document,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise OptimizerInventoryServiceError(
                "packet-document",
                code="invalid-account-packet",
                message="The packet service returned an invalid inventory document.",
            ) from error

        try:
            destination = self.capture_document_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".gear-{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_bytes(data)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as error:
            raise OptimizerInventoryServiceError(
                "packet-document",
                code="capture-file-write",
                message="The captured gear.txt could not be saved in your Documents folder.",
            ) from error
        return self._import_file(destination, source_kind="live-game-packet")

    def reset_all_optimizer_data(self) -> dict[str, Any]:
        """Atomically detach and then erase every mutable Optimizer data store."""

        root = self.database_path.parent.resolve(strict=False)
        targets = [
            *(root / name for name in _OPTIMIZER_DATABASE_FILES),
            *(root / name for name in _OPTIMIZER_DATA_DIRECTORIES),
        ]
        for target in targets:
            if target.parent.resolve(strict=False) != root or target.is_symlink():
                raise OptimizerInventoryServiceError(
                    "data-reset",
                    code="unsafe-reset-target",
                    message="Optimizer data could not be erased because a storage target was unsafe.",
                )

        existing = [target for target in targets if target.exists()]
        database_files = sum(target.is_file() for target in existing if target.name in _OPTIMIZER_DATABASE_FILES)
        profile_root = root / "optimizer_profiles"
        result_roots = {
            root / "optimizer_results",
            root / "optimizer_result_sort_cache",
        }

        def file_count(directory: Path) -> int:
            if not directory.is_dir():
                return 0
            return sum(1 for item in directory.rglob("*") if item.is_file() and not item.is_symlink())

        profile_files = file_count(profile_root)
        result_artifacts = sum(file_count(directory) for directory in result_roots)
        quarantine = root / f".optimizer-reset-{uuid.uuid4().hex}.tmp"
        moved: list[tuple[Path, Path]] = []
        try:
            root.mkdir(parents=True, exist_ok=True)
            quarantine.mkdir()
            for target in existing:
                detached = quarantine / target.name
                os.replace(target, detached)
                moved.append((target, detached))
        except OSError as error:
            for target, detached in reversed(moved):
                try:
                    if detached.exists() and not target.exists():
                        os.replace(detached, target)
                except OSError:
                    pass
            try:
                quarantine.rmdir()
            except OSError:
                pass
            raise OptimizerInventoryServiceError(
                "data-reset",
                code="reset-detach-failed",
                message="Optimizer data is still in use and could not be erased safely.",
            ) from error

        try:
            shutil.rmtree(quarantine)
        except OSError as error:
            raise OptimizerInventoryServiceError(
                "data-reset",
                code="reset-delete-failed",
                message="Optimizer data was detached but Windows could not finish erasing it.",
            ) from error

        return {
            "state": "cleared",
            "inventory": _empty_snapshot(),
            "removed": {
                "databaseFiles": database_files,
                "profileFiles": profile_files,
                "resultArtifacts": result_artifacts,
            },
        }


__all__ = [
    "MAX_DESKTOP_IMPORT_ISSUES",
    "OptimizerInventoryService",
    "OptimizerInventoryServiceError",
]
