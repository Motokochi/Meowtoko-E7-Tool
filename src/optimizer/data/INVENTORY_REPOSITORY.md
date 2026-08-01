# Inventory repository contract

`inventory_repository.py` is the UI-independent SQLite boundary for normalized
Fribbels inventory state. Importing the module or constructing an
`InventoryRepository` performs no filesystem work. Callers must explicitly
call `initialize()` before reading or writing.

## Database path

`resolve_inventory_database_path()` resolves `optimizer.db` beneath the
directory supplied by `E7_USER_DATA_DIR`. Without that override, it resolves
`.local/user-data/optimizer.db` beneath the supplied or current working
directory.
The helper does not create directories or open the database.

All repository methods accept an explicitly constructed repository. Tests use
temporary paths exclusively; P01-T04 does not create or open the development
`.local/user-data/optimizer.db`.

## Versioning and recovery

The current SQLite schema is version 1. `PRAGMA user_version` and the
`repository_metadata.schema_version` row must agree, while
`repository_metadata.repository_kind` prevents an unrelated SQLite database
from being treated as inventory.

Migrations are explicit sequential functions keyed by their source version.
A path that did not exist is initialized without a backup. Any existing
older-version database is backed up with SQLite's database backup API before
the migration transaction begins. The adjacent backup is integrity checked,
returned in `RepositoryInitialization`, and never silently overwritten.

Migration DDL, metadata changes, and both version markers commit in one
transaction. On failure the transaction is rolled back, the original database
remains at its prior version, and `InventoryRepositoryMigrationError` exposes
the backup path. A database from a newer application is rejected with guidance
to update the application and is not mutated or backed up unnecessarily.

The fixture-backed recovery rehearsal restores a stopped database from the
integrity-checked adjacent backup through SQLite's backup API, validates the
restored copy, and replays migration. The original backup is retained and the
next migration chooses a non-overwriting backup name. A mismatch between the
two current-version markers is rejected without mutation or backup.

Backup time and naming are injectable. Import-history timestamps and IDs are
always caller supplied; record construction never reads the clock.

## Stored state

The schema has five owned tables:

- `inventory_items` stores explicit normalized gear columns, descriptive
  Fribbels fields, projection totals/evidence, and separate deterministic JSON
  for complete source and user metadata.
- `item_identity_aliases` stores every current or historical identity alias.
  Partial unique indexes make in-game and source aliases globally unique.
  Fingerprint values may be shared by multiple items, while each item has
  exactly one current fingerprint row.
- `imported_heroes` stores the minimal equipped-item hero references and their
  complete frozen source objects.
- `import_history` stores caller-supplied import identity/time, source
  encoding/variant, aggregate parser and merge counts, and only explicitly
  supplied privacy-safe source metadata.
- `repository_metadata` stores auditable repository and schema markers.

Nested values use compact deterministic JSON. Pickle and raw source-document
blobs are not used. Alias rows have cascading foreign keys to their stable
items; equipped hero IDs deliberately remain plain nullable identifiers so an
items-only import or stale owner reference remains representable.

## Atomic imports

`apply_import()` accepts one complete `FribbelsMergeResult`, the complete
minimal hero snapshot, and one `ImportHistoryRecord`. It validates history
counts against the merge, then performs all item upserts, obsolete-state
removal, alias replacement, hero replacement, and history insertion inside one
transaction. P01-T03 unseen items remain present because they are included in
the complete merge result.

Strong aliases are deleted for the complete snapshot before replacements are
inserted, avoiding transient conflicts when an alias moves legitimately. Any
item, alias, hero, or history constraint failure rolls back every change.
Routine repository exceptions contain only structural recovery guidance, not
inventory rows, owner IDs, item IDs, or stats.

## Read and search contracts

`load_inventory()`, `load_heroes()`, and `load_import_history()` reconstruct
immutable records in deterministic order. Source/user metadata and identity
aliases round-trip without loss.

`inventory_summary()` returns aggregate item, equipped, locked, hero, import,
alias, and canonical six-slot counts without exposing raw rows.

`dense_snapshot()` groups `GearItem` copies across all six slots in canonical
`GEAR_SLOT_ORDER`. Within each slot, items are sorted by stable item ID. It
assigns ephemeral dense IDs contiguously from zero and returns a reverse
dense-ID-to-stable-ID mapping. Empty slots are present as empty tuples. Dense
IDs are never written to SQLite and the stored immutable `GearItem` records
remain unchanged.
