"""UI-independent orchestration for one complete Fribbels inventory import."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from src.core.path_safety import lexical_absolute_path
from src.optimizer.data.fribbels import (
    FribbelsDocumentError,
    FribbelsEncoding,
    FribbelsIssue,
    FribbelsParseResult,
    FribbelsVariant,
    parse_fribbels_gear_bytes,
    parse_fribbels_gear_file,
)
from src.optimizer.data.fribbels_merge import (
    FribbelsMergeInputError,
    FribbelsMergeOutcome,
    FribbelsMergeResult,
    merge_fribbels_inventory,
)
from src.optimizer.data.inventory_repository import (
    ImportHistoryRecord,
    InventoryRepository,
    InventoryRepositoryError,
    InventoryRepositoryMigrationError,
    InventoryRepositoryReadError,
    InventoryRepositorySchemaError,
    InventoryRepositoryWriteError,
    RepositoryInitialization,
)
from src.optimizer.data.schema_common import FrozenJsonObject, freeze_json_object, utc_timestamp
from src.optimizer.domain import GEAR_SLOT_ORDER, GearSlot


class FribbelsImportRequestError(ValueError):
    """Raised before I/O when an import request violates its public contract."""


class FribbelsImportErrorCategory(StrEnum):
    SOURCE_ACCESS = "source-access"
    DOCUMENT = "document"
    REPOSITORY_INITIALIZATION = "repository-initialization"
    REPOSITORY_READ = "repository-read"
    MERGE = "merge"
    REPOSITORY_WRITE = "repository-write"


class FribbelsImportIssueKind(StrEnum):
    WARNING = "warning"
    REJECTION = "rejection"
    CONFLICT = "conflict"


class FribbelsImportServiceError(RuntimeError):
    """A structured, privacy-safe failure from the import orchestration."""

    def __init__(
        self,
        category: FribbelsImportErrorCategory,
        *,
        code: str,
        message: str,
        document_path: str | None = None,
        recovery_backup_path: Path | None = None,
    ) -> None:
        self.category = FribbelsImportErrorCategory(category)
        self.code = _required_text(code, "error code")
        self.document_path = (
            None
            if document_path is None
            else _required_text(document_path, "document path")
        )
        self.recovery_backup_path = (
            None
            if recovery_backup_path is None
            else lexical_absolute_path(recovery_backup_path)
        )
        super().__init__(_required_text(message, "error message"))


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FribbelsImportRequestError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _optional_index(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FribbelsImportRequestError(
            f"{field_name} must be a non-negative integer or null."
        )
    return value


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FribbelsImportRequestError(
            f"{field_name} must be a non-negative integer."
        )
    return value


@dataclass(frozen=True, slots=True)
class FribbelsImportRequest:
    """Caller-selected source and caller-owned audit identity/time."""

    source_path: Path | str | os.PathLike[str] = field(repr=False)
    import_id: str
    imported_at: str
    privacy_safe_source_metadata: FrozenJsonObject | Mapping[str, object] = FrozenJsonObject()

    def __post_init__(self) -> None:
        if not isinstance(self.source_path, (str, os.PathLike)):
            raise FribbelsImportRequestError("source_path must be a filesystem path.")
        if isinstance(self.source_path, str) and not self.source_path.strip():
            raise FribbelsImportRequestError("source_path must not be empty.")
        try:
            source_path = Path(self.source_path)
        except (TypeError, ValueError):
            raise FribbelsImportRequestError("source_path must be a filesystem path.") from None
        object.__setattr__(self, "source_path", source_path)
        object.__setattr__(self, "import_id", _required_text(self.import_id, "import_id"))
        try:
            imported_at = utc_timestamp(
                self.imported_at,
                "FribbelsImportRequest.imported_at",
            )
        except ValueError as error:
            raise FribbelsImportRequestError(
                "imported_at must be a valid ISO-8601 UTC timestamp ending in Z."
            ) from error
        object.__setattr__(self, "imported_at", imported_at)
        try:
            source_metadata = (
                self.privacy_safe_source_metadata
                if isinstance(self.privacy_safe_source_metadata, FrozenJsonObject)
                else freeze_json_object(
                    self.privacy_safe_source_metadata,
                    "FribbelsImportRequest.privacy_safe_source_metadata",
                )
            )
        except ValueError as error:
            raise FribbelsImportRequestError(
                "privacy_safe_source_metadata must be a JSON-compatible object."
            ) from error
        object.__setattr__(self, "privacy_safe_source_metadata", source_metadata)


@dataclass(frozen=True, slots=True)
class FribbelsImportIssueSummary:
    """Sanitized structural issue details suitable for a UI report."""

    kind: FribbelsImportIssueKind
    code: str
    document_path: str
    message: str
    item_index: int | None = None
    hero_index: int | None = None

    def __post_init__(self) -> None:
        try:
            kind = (
                self.kind
                if isinstance(self.kind, FribbelsImportIssueKind)
                else FribbelsImportIssueKind(self.kind)
            )
        except (TypeError, ValueError):
            raise FribbelsImportRequestError("Import issue kind is unsupported.") from None
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "code", _required_text(self.code, "issue code"))
        object.__setattr__(
            self,
            "document_path",
            _required_text(self.document_path, "issue document path"),
        )
        object.__setattr__(self, "message", _required_text(self.message, "issue message"))
        object.__setattr__(
            self,
            "item_index",
            _optional_index(self.item_index, "issue item_index"),
        )
        object.__setattr__(
            self,
            "hero_index",
            _optional_index(self.hero_index, "issue hero_index"),
        )
        if self.item_index is not None and self.hero_index is not None:
            raise FribbelsImportRequestError(
                "Import issue cannot identify both an item and a hero row."
            )
        if kind in (FribbelsImportIssueKind.REJECTION, FribbelsImportIssueKind.CONFLICT):
            if self.item_index is None or self.hero_index is not None:
                raise FribbelsImportRequestError(
                    "Rejected and conflicted issues require one item row index."
                )


@dataclass(frozen=True, slots=True)
class FribbelsImportReport:
    """Immutable aggregate result returned only after an import commits."""

    import_id: str
    imported_at: str
    source_encoding: FribbelsEncoding
    source_variant: FribbelsVariant
    source_item_count: int
    accepted_count: int
    rejected_count: int
    warning_count: int
    warning_item_count: int
    inserted_count: int
    updated_count: int
    unchanged_count: int
    conflict_count: int
    unseen_existing_count: int
    equipped_item_count: int
    imported_hero_count: int
    resulting_inventory_count: int
    items_by_slot: tuple[tuple[GearSlot, int], ...]
    repository_created: bool
    repository_migrated: bool
    previous_schema_version: int
    schema_version: int
    recovery_backup_path: Path | None
    issues: tuple[FribbelsImportIssueSummary, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "import_id", _required_text(self.import_id, "report import_id"))
        try:
            imported_at = utc_timestamp(
                self.imported_at,
                "FribbelsImportReport.imported_at",
            )
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
        except (TypeError, ValueError) as error:
            raise FribbelsImportRequestError(
                "Import report timestamp, encoding, or variant is invalid."
            ) from error
        object.__setattr__(self, "imported_at", imported_at)
        object.__setattr__(self, "source_encoding", encoding)
        object.__setattr__(self, "source_variant", variant)
        for field_name in (
            "source_item_count",
            "accepted_count",
            "rejected_count",
            "warning_count",
            "warning_item_count",
            "inserted_count",
            "updated_count",
            "unchanged_count",
            "conflict_count",
            "unseen_existing_count",
            "equipped_item_count",
            "imported_hero_count",
            "resulting_inventory_count",
            "previous_schema_version",
            "schema_version",
        ):
            object.__setattr__(self, field_name, _count(getattr(self, field_name), field_name))
        if self.schema_version < 1:
            raise FribbelsImportRequestError("report schema_version must be positive.")
        if not isinstance(self.repository_created, bool) or not isinstance(
            self.repository_migrated,
            bool,
        ):
            raise FribbelsImportRequestError("Repository lifecycle flags must be boolean.")
        if self.repository_created and self.repository_migrated:
            raise FribbelsImportRequestError(
                "A repository cannot be both newly created and migrated."
            )
        if self.repository_migrated != (
            not self.repository_created
            and self.previous_schema_version < self.schema_version
        ):
            raise FribbelsImportRequestError(
                "Repository migration flag does not agree with schema versions."
            )
        slot_counts = tuple(self.items_by_slot)
        if tuple(slot for slot, _ in slot_counts) != GEAR_SLOT_ORDER:
            raise FribbelsImportRequestError(
                "Report slot counts must use canonical six-slot order."
            )
        normalized_slot_counts: list[tuple[GearSlot, int]] = []
        for slot, count in slot_counts:
            normalized_slot_counts.append((GearSlot(slot), _count(count, "slot count")))
        object.__setattr__(self, "items_by_slot", tuple(normalized_slot_counts))
        issues = tuple(self.issues)
        if not all(isinstance(issue, FribbelsImportIssueSummary) for issue in issues):
            raise FribbelsImportRequestError(
                "Report issues must contain FribbelsImportIssueSummary values."
            )
        object.__setattr__(self, "issues", issues)
        backup_path = (
            None
            if self.recovery_backup_path is None
            else lexical_absolute_path(self.recovery_backup_path)
        )
        object.__setattr__(self, "recovery_backup_path", backup_path)

        if self.source_item_count != self.accepted_count + self.rejected_count:
            raise FribbelsImportRequestError(
                "Report source count must equal accepted plus rejected counts."
            )
        if self.accepted_count != (
            self.inserted_count
            + self.updated_count
            + self.unchanged_count
            + self.conflict_count
        ):
            raise FribbelsImportRequestError(
                "Report outcome counts must equal the accepted count."
            )
        if self.warning_item_count > self.accepted_count:
            raise FribbelsImportRequestError(
                "Report warning-bearing item count exceeds accepted items."
            )
        if self.equipped_item_count > self.accepted_count:
            raise FribbelsImportRequestError(
                "Report equipped item count exceeds accepted items."
            )
        if sum(count for _, count in self.items_by_slot) != self.resulting_inventory_count:
            raise FribbelsImportRequestError(
                "Report slot counts do not equal resulting inventory count."
            )
        issue_counts = {
            kind: sum(issue.kind is kind for issue in issues)
            for kind in FribbelsImportIssueKind
        }
        if issue_counts[FribbelsImportIssueKind.WARNING] != self.warning_count:
            raise FribbelsImportRequestError("Report warning issues do not match warning_count.")
        if issue_counts[FribbelsImportIssueKind.REJECTION] != self.rejected_count:
            raise FribbelsImportRequestError(
                "Report rejection issues do not match rejected_count."
            )
        if issue_counts[FribbelsImportIssueKind.CONFLICT] != self.conflict_count:
            raise FribbelsImportRequestError(
                "Report conflict issues do not match conflict_count."
            )

    def count_for_slot(self, slot: GearSlot) -> int:
        return dict(self.items_by_slot)[GearSlot(slot)]


def _source_issue(
    issue: FribbelsIssue,
    kind: FribbelsImportIssueKind,
) -> FribbelsImportIssueSummary:
    return FribbelsImportIssueSummary(
        kind=kind,
        code=issue.code,
        document_path=issue.path,
        message=issue.message,
        item_index=issue.item_index,
        hero_index=issue.hero_index,
    )


def _conflict_issue(outcome: FribbelsMergeOutcome) -> FribbelsImportIssueSummary:
    if outcome.code is None or outcome.message is None:
        raise FribbelsImportRequestError(
            "Conflict outcome is missing its structured details."
        )
    return FribbelsImportIssueSummary(
        kind=FribbelsImportIssueKind.CONFLICT,
        code=outcome.code,
        document_path=f"$.items[{outcome.source_index}]",
        message=outcome.message,
        item_index=outcome.source_index,
    )


def _report_issues(parsed: FribbelsParseResult, merged: FribbelsMergeResult) -> tuple[
    FribbelsImportIssueSummary,
    ...,
]:
    return (
        *(
            _source_issue(issue, FribbelsImportIssueKind.WARNING)
            for issue in parsed.warnings
        ),
        *(
            _source_issue(issue, FribbelsImportIssueKind.REJECTION)
            for issue in parsed.rejections
        ),
        *(_conflict_issue(outcome) for outcome in merged.conflicts),
    )


def _build_report(
    request: FribbelsImportRequest,
    parsed: FribbelsParseResult,
    merged: FribbelsMergeResult,
    initialization: RepositoryInitialization,
) -> FribbelsImportReport:
    slot_counts = {slot: 0 for slot in GEAR_SLOT_ORDER}
    for item in merged.items:
        slot_counts[item.gear_item.slot] += 1
    return FribbelsImportReport(
        import_id=request.import_id,
        imported_at=request.imported_at,
        source_encoding=parsed.encoding,
        source_variant=parsed.variant,
        source_item_count=parsed.source_item_count,
        accepted_count=parsed.accepted_count,
        rejected_count=parsed.rejected_count,
        warning_count=parsed.warning_count,
        warning_item_count=parsed.warning_item_count,
        inserted_count=len(merged.inserted),
        updated_count=len(merged.updated),
        unchanged_count=len(merged.unchanged),
        conflict_count=len(merged.conflicts),
        unseen_existing_count=len(merged.unseen_existing_ids),
        equipped_item_count=sum(
            item.equipped_hero_id is not None for item in parsed.items
        ),
        imported_hero_count=len(parsed.heroes),
        resulting_inventory_count=len(merged.items),
        items_by_slot=tuple((slot, slot_counts[slot]) for slot in GEAR_SLOT_ORDER),
        repository_created=initialization.created,
        repository_migrated=(
            not initialization.created
            and initialization.previous_version < initialization.schema_version
        ),
        previous_schema_version=initialization.previous_version,
        schema_version=initialization.schema_version,
        recovery_backup_path=initialization.backup_path,
        issues=_report_issues(parsed, merged),
    )


class FribbelsImportService:
    """Compose parsing, merging, history, and persistence for one selected file."""

    def __init__(self, repository: InventoryRepository) -> None:
        if not isinstance(repository, InventoryRepository):
            raise FribbelsImportRequestError(
                "repository must be an InventoryRepository."
            )
        self.repository = repository

    def import_file(self, request: FribbelsImportRequest) -> FribbelsImportReport:
        if not isinstance(request, FribbelsImportRequest):
            raise FribbelsImportRequestError(
                "request must be a FribbelsImportRequest."
            )
        try:
            parsed = parse_fribbels_gear_file(request.source_path)
        except FribbelsDocumentError as error:
            category = (
                FribbelsImportErrorCategory.SOURCE_ACCESS
                if error.code == "file-read"
                else FribbelsImportErrorCategory.DOCUMENT
            )
            raise FribbelsImportServiceError(
                category,
                code=error.code,
                document_path=error.path,
                message=error.message,
            ) from error
        return self._import_parsed(request, parsed)

    def import_bytes(
        self,
        request: FribbelsImportRequest,
        data: bytes,
    ) -> FribbelsImportReport:
        """Import an already-acquired document through the same transaction."""

        if not isinstance(request, FribbelsImportRequest):
            raise FribbelsImportRequestError(
                "request must be a FribbelsImportRequest."
            )
        try:
            parsed = parse_fribbels_gear_bytes(data)
        except FribbelsDocumentError as error:
            raise FribbelsImportServiceError(
                FribbelsImportErrorCategory.DOCUMENT,
                code=error.code,
                document_path=error.path,
                message=error.message,
            ) from error
        return self._import_parsed(request, parsed)

    def _import_parsed(
        self,
        request: FribbelsImportRequest,
        parsed: FribbelsParseResult,
    ) -> FribbelsImportReport:
        try:
            initialization = self.repository.initialize()
        except InventoryRepositoryMigrationError as error:
            raise FribbelsImportServiceError(
                FribbelsImportErrorCategory.REPOSITORY_INITIALIZATION,
                code="repository-migration",
                message="The inventory database migration failed and was rolled back.",
                recovery_backup_path=error.backup_path,
            ) from error
        except (InventoryRepositorySchemaError, InventoryRepositoryError) as error:
            raise FribbelsImportServiceError(
                FribbelsImportErrorCategory.REPOSITORY_INITIALIZATION,
                code="repository-initialization",
                message="The inventory database could not be initialized safely.",
            ) from error

        try:
            existing = self.repository.load_inventory()
        except (InventoryRepositoryReadError, InventoryRepositoryError) as error:
            raise FribbelsImportServiceError(
                FribbelsImportErrorCategory.REPOSITORY_READ,
                code="repository-read",
                message="Existing inventory state could not be read safely.",
            ) from error

        try:
            merged = merge_fribbels_inventory(existing, parsed)
        except FribbelsMergeInputError as error:
            raise FribbelsImportServiceError(
                FribbelsImportErrorCategory.MERGE,
                code="inventory-merge",
                message="Existing inventory identities could not be merged safely.",
            ) from error

        history = ImportHistoryRecord.from_merge_result(
            import_id=request.import_id,
            imported_at=request.imported_at,
            source_encoding=parsed.encoding,
            source_variant=parsed.variant,
            source_item_count=parsed.source_item_count,
            merge_result=merged,
            source_metadata=request.privacy_safe_source_metadata,
        )
        report = _build_report(request, parsed, merged, initialization)
        try:
            self.repository.apply_import(merged, parsed.heroes, history)
        except (InventoryRepositoryWriteError, InventoryRepositoryError) as error:
            raise FribbelsImportServiceError(
                FribbelsImportErrorCategory.REPOSITORY_WRITE,
                code="repository-write",
                message="The inventory import was not committed; repository state is unchanged.",
            ) from error
        return report


__all__ = [
    "FribbelsImportErrorCategory",
    "FribbelsImportIssueKind",
    "FribbelsImportIssueSummary",
    "FribbelsImportReport",
    "FribbelsImportRequest",
    "FribbelsImportRequestError",
    "FribbelsImportService",
    "FribbelsImportServiceError",
]
