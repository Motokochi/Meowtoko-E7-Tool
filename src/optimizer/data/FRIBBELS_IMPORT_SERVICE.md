# Fribbels import service contract

`fribbels_import_service.py` is the UI-independent Phase 01 orchestration
boundary. It accepts a path already selected by a caller; it does not implement
a native picker, Electron IPC, logging, or optimizer eligibility/search.

## Request and execution order

`FribbelsImportRequest` contains an explicit source path, stable import ID, UTC
timestamp, and recursively frozen metadata that the caller has classified as
safe for history. Its representation suppresses the source path. Request and
report construction never read the clock.

`FribbelsImportService.import_file()` performs these steps in order:

1. Parse the selected path with `parse_fribbels_gear_file()`.
2. Initialize or migrate the explicit `InventoryRepository`.
3. Load the complete stored inventory and run the pure P01-T03 merge.
4. Build the import-history record and validate the complete aggregate report.
5. Atomically persist merged items, aliases, imported heroes, and history.
6. Return the prevalidated report only after the transaction commits.

Parsing precedes repository initialization. A missing, unreadable, or fatally
malformed source therefore cannot create a new empty database as a side effect.
Building the report before the write also prevents a report-validation failure
after a successful commit.

Equipment eligibility is intentionally absent. Every accepted owned item is
persisted; P01-T05 filters gear only when preparing a later search.

## Report

`FribbelsImportReport` contains only caller audit identity/time and aggregate
state:

- source encoding/variant and source, accepted, rejected, warning, and
  warning-bearing-item counts;
- inserted, updated, unchanged, conflict, and unseen-existing counts;
- equipped accepted-item and imported-hero counts;
- resulting inventory total and canonical counts for all six slots;
- repository created/migrated flags, prior/current schema versions, and an
  optional adjacent recovery backup path;
- immutable structural issue summaries.

Issue summaries contain severity, stable code, JSON path, fixed parser/merge
message, and optional item or hero row index. They never contain raw row
objects. Ordering is deterministic: parser warnings first, parser rejections
second, then merge conflicts; source order is retained inside each group.

Reports do not include the source filesystem path or name, root metadata, raw
JSON, item/owner IDs, hero names, or stat values. History receives only the
request's explicitly supplied privacy-safe metadata, never parser root metadata
or the selected path.

## Failures and atomicity

`FribbelsImportServiceError` provides a stable category and code, an optional
structural document path, and an optional migration recovery backup path.
Categories distinguish source access, fatal documents, repository
initialization, repository reads, merge-state validation, and repository
writes. Public messages are structural and do not copy raw source values or
filesystem paths. Original exceptions remain chained for programmatic
diagnostics.

Recoverable row rejections and merge conflicts are successful imports: valid
rows commit and aggregate issues explain the skipped rows. A repository write
failure returns no report and relies on P01-T04's transaction to preserve the
entire prior item, alias, hero, and history state.
