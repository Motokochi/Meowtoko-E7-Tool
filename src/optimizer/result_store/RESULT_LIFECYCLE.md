# Result lifecycle and reproducibility contract

`src.optimizer.result_store.lifecycle` owns two hardware-independent version 1
boundaries:

- `e7.optimizer.result-lifecycle` decides which proven result artifacts may be
  retained or removed; and
- `e7.optimizer.result-reproducibility` records the immutable evidence needed
  to identify the inputs and implementation that produced a completed run.

Importing the module and constructing `ResultLifecycleManager` perform no
filesystem work. Result-store, sort-cache, and export roots are explicit
absolute paths. A missing root is an empty lifecycle scope; it is not created.
The module does not query SQLite, access CUDA, or infer a live user-data path.

## Ownership and deletion boundary

Cleanup recognizes only direct children with the exact names and plain-file or
plain-directory shapes below.

| Artifact | Recognized ownership evidence | Age source |
|---|---|---|
| Completed run | Canonical run ID; valid v1 manifest; exact ten-column directory; root contains only manifest, columns, and optional `reproducibility-v1.json` | Manifest `completedUtc` |
| Incomplete writer | `<run>.<32-lower-hex>.tmp` below `.incomplete`, with only the known partial columns layout | Filesystem modification time |
| Staged run | Canonical run ID below `runs`, no completed manifest, and only columns plus optional `manifest.json.pending` | Filesystem modification time |
| Writer lock | Empty canonical `<run>.lock` directory below `.locks` | Filesystem modification time |
| Completed sort cache | `<64-lower-hex>` directory with the v1 index manifest and ordinal file | Manifest `completedUtc` |
| Sort-cache transaction | `.<cache-key>.<32-lower-hex>.tmp` with only known index transaction files | Filesystem modification time |
| Export transaction | `.<destination>.<operation-token>.e7-export.tmp` plain file in a configured export root | Filesystem modification time |

Unknown names, unexpected files, malformed layouts, and completed-run folders
containing any unrelated file are counted and preserved. A recognized symlink,
junction, or other reparse-like artifact fails closed. Before deletion, the
manager rechecks that the configured parent is a plain directory and that the
resolved target is its plain direct child. It never follows a link or performs
a broad/glob deletion.

`ResultLifecycleRequest` supplies a UTC clock, active run IDs, active sort-cache
keys, and active export-temporary basenames. An active ID protects its completed
run, staged/incomplete transaction, and lock. An active cache key protects its
completed and transactional indexes. `ResultExportRequest.operation_token`
makes the export temporary name available to the coordinator before execution,
so the same active registry can protect a long-running export.

The default stale age is 86,400 seconds and the newest two completed runs are
retained even when otherwise stale. Age equality is eligible: an artifact with
age exactly equal to `stale_after_seconds` is stale. Active protection takes
precedence over retention, which takes precedence over age. Dry-run is the
default and reports `protected-active`, `protected-retention`, `too-young`, or
`eligible-dry-run` without mutation. Real cleanup reports `removed` and is
idempotent. Removing a completed run is intentionally irreversible; the dry-run
default, active registry, and newest-run retention are its recovery safeguards.

P07-T02 visibility is unchanged. Manifest-less remnants are never returned as
completed runs, and lifecycle cleanup never manufactures or repairs a
manifest.

P09 update/recovery acceptance reopens a completed run and its immutable
reproducibility sidecar byte-for-byte after restart. A frozen startup rehearsal
removes an old structurally proven incomplete writer while retaining both the
valid completed run and a corrupt completed copy that cannot prove ownership.

## Reproducibility sidecar

`ResultReproducibilityRecord` is written only for a supplied
`CompletedResultRun`. Its strict JSON payload contains:

- run ID, row count, creation/completion timestamps, and the canonical
  completed-run fingerprint over the v1 manifest identity and column hashes;
- the complete canonical `OptimizationRequest`, its SHA-256, selected hero,
  base profile, projection mode, set target, tolerance, bounds, priorities,
  filters, skill contexts, cap, and backend preference;
- SHA-256 identities for the full inventory snapshot and exact prepared search
  snapshot;
- ordered schema/version/SHA evidence for `artifact-catalog`,
  `character-catalog`, and `skill-context-catalog`;
- pinned engine source revisions for vocabulary, set evaluation, derived
  metrics, primary caps, and priority normalization;
- result-column, run, filter, sort-index, and resolution contract versions; and
- CPU or CUDA execution evidence. CUDA evidence additionally requires device
  name and runtime version.

Missing components, an unknown schema/version, noncanonical SHA evidence,
request/context drift, target/projection drift, or a stale inventory/search
snapshot is rejected instead of being described as reproducible.

The sidecar is `reproducibility-v1.json` adjacent to the locked v1 run
manifest. The existing run manifest is never edited. The canonical JSON is
itself covered by `recordSha256`; it is written to a unique pending file,
flushed and fsynced, then published without replacement through a same-directory
hard link. Repeating an identical write is idempotent. Different evidence,
concurrent publication, injected failure, or malformed persisted JSON fails
without replacing the immutable record. Lifecycle ownership admits the final
sidecar but preserves a run while any pending or unrelated root file exists.

## Deliberate exclusions

Lifecycle is not a scheduler and does not decide when a desktop session starts
or ends; P08 will own that integration and active registry. It does not perform
the P07-T07 five-million-row measurement, create protocol messages, access the
live database, or package optional CUDA dependencies.
