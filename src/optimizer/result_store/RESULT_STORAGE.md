# Transactional result runs

`storage.py` implements result-run format `e7.optimizer.result-run` version 1.
Every store receives an explicit filesystem root. Importing the package and
constructing `ResultRunStore` are read/write side-effect free; directories are
created only by `begin_run()`. Production callers may later choose a user-data
root, while all current tests inject temporary directories and never open the
live optimizer database.

## Layout and trust boundary

```text
<explicit root>/
  .incomplete/
    <run-id>.<random-token>.tmp/
      columns/
        00-dense_item_ids.bin
        ...
        08-equipped_item_counts.bin
      manifest.json.pending         # never discoverable as completed
  .locks/
    <run-id>.lock/                  # atomic single-writer claim
  runs/
    <run-id>/                       # atomically moved from .incomplete
      manifest.json                 # atomic visibility boundary
      columns/
        00-dense_item_ids.bin
        ...
        08-equipped_item_counts.bin
```

Run IDs are 1–128 ASCII letters, digits, dots, underscores, or hyphens, start
with an alphanumeric character, and cannot be `.` or `..`. Manifest paths are
never trusted as arbitrary paths: the reader derives every expected filename
from schema order and requires exact descriptor equality. Store namespaces,
published runs, manifests, column directories, and column files must be plain
filesystem objects rather than symlinks or Windows junctions/reparse targets.
Cleanup verifies the exact transaction parent before recursive removal.

Only a plain `runs/<run-id>` directory with a strictly valid completed manifest
is a completed run. `.incomplete` content, stale locks, missing manifests,
unknown fields, wrong schema/format versions, truncated columns, unsafe paths,
and corrupt descriptors are ignored by discovery and rejected by direct open.

## Column files and manifest

Each v2 column file is raw C-order bytes using the exact dtype and row shape in
`RESULT_SCHEMA.md`. There is no per-file header, alignment, capacity padding,
or preallocation. A small run consumes only its actual rows. The completed
manifest pins:

- format, schema, state, run identity, row count, 233 bytes per row, and payload
  bytes;
- UTC creation/completion timestamps;
- all ten columns in canonical order;
- fixed filename, dtype string, per-row shape, full shape, bytes per row, and
  file length; and
- a lowercase SHA-256 digest accumulated while each column is streamed.

`open_run()` always checks manifest identity, descriptor equality, and file
lengths. `verify_hashes=True` additionally streams every file in 1 MiB chunks
to detect same-size corruption without loading a column into RAM. Discovery
uses structural/size verification rather than rehashing up to 1.165 GB on every
list operation; callers can explicitly request the stronger audit.

`CompletedResultRun.open_column()` returns a read-only NumPy memmap in the exact
schema shape. A zero-row column is a read-only empty ndarray because NumPy does
not memmap an empty file.

## Transaction state machine

| State | Allowed action | Outcome |
|---|---|---|
| `open` | append at exactly the current physical row ordinal | validates the complete schema batch, streams all columns, advances row count |
| `open` | complete at the exact appended count | flushes/fsyncs columns, verifies lengths, writes/fsyncs manifest, atomically publishes directory |
| `open` | abort/cancel | closes files, deletes exact temporary directory, releases lock |
| `failed` | abort | performs the same safe cleanup; no retry or publish is allowed |
| `aborted` | abort | idempotent no-op |
| `published` | read | completed run is discoverable; append/abort are rejected |

Any validation, cap, write, flush, verification, manifest, or publish exception
makes the writer failed and prevents subsequent writes. The shared cap is
checked before any bytes in that batch are written. Batch `start_ordinal` must
equal the accumulated row count, rejecting duplicated, skipped, and reordered
appends. A requested terminal count must equal the appended count.

Every column is flushed and fsynced before length verification. The canonical
manifest is written to `manifest.json.pending`, flushed, and fsynced. The
temporary directory is atomically staged within the same root at
`runs/<run-id>` while still lacking `manifest.json`, so readers continue to
ignore it. Publication is the final atomic rename from `manifest.json.pending`
to `manifest.json`; directory fsync is used where the platform supports it.
There is no checkpoint after that visibility boundary: once the manifest rename
succeeds, the run is complete even if a later process exit leaves a harmless
stale lock.

Explicit aborts and context-manager cancellation delete temporary or staged
data. A process crash can leave temporary data, a manifest-less staged
directory, and a lock, but discovery never presents them and no pre-boundary
checkpoint creates `manifest.json`. Automated stale-remnant retention/cleanup belongs to P07-T06;
P07-T02 deliberately favors evidence preservation over guessing that an
unowned remnant is safe to delete.

## CPU and CUDA adapters

`DenseItemEquippedLookup` is an immutable, contiguous boolean snapshot indexed
by dense item ID. It rejects gaps, non-boolean ownership, signed-32 overflow,
and unknown result IDs. Equipped counts are a vectorized lookup and six-value
sum, producing `u1` values from zero through six.

`result_columns_from_cpu_rows()` converts only the supplied bounded
`CategorizedBuildRow` batch into the ten canonical arrays. It uses stored P04/
P05 category, sets, effective stats, all metrics, priority, and constraint
values, then validates the entire schema. It never constructs a full-run array.

`result_columns_from_cuda_batch()` requires a complete compact host batch; a
cap-truncated accepted prefix is rejected. The adapter reuses the existing
dense IDs, set IDs, category, distance, stat, metric, priority, and constraint
arrays by identity, renames `set_indices` logically to `owned_set_indices`,
discards the already-validated transient flat-index/counter arrays, and creates
only the equipped-count vector. Binary32 priority/distance bytes are unchanged.

The writer itself accepts either adapter's ordered mapping, so persistence has
no CPU/CUDA branch.

## Exact storage overhead

`project_result_run_storage(run_id, row_count)` serializes a length-equivalent
manifest with fixed-width timestamps/digests, so its byte result is exact for
that ASCII run ID and row count. Raw column headers and alignment cost zero.

| Run ID / rows | Payload | Manifest | Published total | Transaction peak |
|---|---:|---:|---:|---:|
| `zero` / 0 | 0 | 2,509 | 2,509 | 2,509 |
| `one` / 1 | 233 | 2,514 | 2,747 | 2,747 |
| `cap` / 5,000,000 | 1,165,000,000 | 2,653 | 1,165,002,653 | 1,165,002,653 |

Manifest length changes predictably with the ASCII run-ID length and decimal
row/file byte counts. `CompletedResultRun.manifest_bytes` reports the actual
value. Publication is a same-volume directory move, so no second payload copy
is created; pending manifest and final manifest are the same bytes renamed.
Filesystem directory entries, allocation-unit slack, OS write cache, and sort/
filter indexes are filesystem/runtime costs rather than bytes owned by this
format and are reported separately when relevant.

## Deferred work

P07-T02 does not define user filters, masks, sort indexes, paging, stale-run
retention, exports, reproducibility metadata, replacement-detail caches,
desktop protocol/UI, or large-run interaction targets. P07-T03/P07-T04 can map
the raw columns directly; P07-T05 can resolve visible rows by dense IDs; P07-T06
owns lifecycle/export metadata; P07-T07 owns five-million-row latency and disk
allocation measurements.
