# Result sorting, indexes, and paging

`indexing.py` defines P07-T04's immutable sort/page contracts and optional
disk-backed ordinal cache. It consumes a completed result run plus either the
base row set or a P07-T03 `FilteredResultView`. It does not alter the result-run
manifest or resolve gear/build objects.

## Sort authority and ordering

`ResultSortRequest` is hash-stable and pinned to
`e7.optimizer.result-sort-index` version 1. Its 27 canonical keys are:

- all eight effective final stats in `FINAL_STAT_ORDER`;
- all 15 derived metrics in `DERIVED_METRIC_IDS`;
- summed Fribbels item-priority score;
- normalized constraint distance;
- replacement count; and
- equipped-item count.

Every key supports `ascending` and `descending`. The default is priority score
descending, matching final-build ranking. Signed `<i8`, exact `<f4`, and `u1`
columns are gathered without casting. Valid result runs already reject
nonfinite values, and the index builder independently rejects NaN/infinity if a
selected float column was later corrupted.

Ordering is `(numeric value, physical row ordinal)` for ascending and
`(reverse numeric value, physical row ordinal)` for descending. The ordinal is
always ascending inside equal-value groups, including descending sorts. This
gives deterministic first/middle/last pages even when many builds share a
value. Descending integer/byte keys are complemented in their existing dtype;
float keys are negated after the finite check. Neither operation narrows values
or overflows signed-int64 minima. `np.lexsort` then uses the physical ordinal as
the explicit final tie-breaker.

The base source is `0..rowCount-1`. A filtered source is the exact read-only
ascending `<u4` ordinal vector from P07-T03 and is checked against the completed
run. Sorting never admits a row outside that source.

## Memory projection

The complete index is always one `<u4` ordinal per selected row. The declared
build projection accounts for source ordinals, gathered key values, the native
indirect order, one equally sized conservative sort workspace, and final output
ordinals. At five million rows:

| Key storage | Source | Values | Order | Workspace | Output | Declared peak |
|---|---:|---:|---:|---:|---:|---:|
| signed int64 | 20,000,000 | 40,000,000 | 40,000,000 | 40,000,000 | 20,000,000 | 160,000,000 bytes |
| binary32 | 20,000,000 | 20,000,000 | 40,000,000 | 40,000,000 | 20,000,000 | 140,000,000 bytes |
| unsigned byte | 20,000,000 | 5,000,000 | 40,000,000 | 40,000,000 | 20,000,000 | 125,000,000 bytes |

`DEFAULT_MAXIMUM_BUILD_ARRAY_BYTES` is 192 MiB. A projection above the supplied
budget fails before sorting. This declared budget covers the complete retained
arrays and a conservative indirect-sort workspace; Python/NumPy module state,
memmapped source pages, and the OS filesystem cache are not heap copies of the
index and remain outside the projection. Cache hits skip the build arrays and
map the 20 MB cap index read-only.

## Deterministic cache and publication

`ResultSortIndexCache` requires an explicit root and does no filesystem work at
construction. Its SHA-256 key includes:

- result-index, result-filter, result-run, and result-schema format identities
  and versions;
- run ID, completion time, row count, and all completed column digests;
- either the base-run identity or the exact SHA-256 of filtered ordinal bytes;
  and
- canonical sort key and direction.

Each entry is a raw `row-ordinals.u4` plus a strict v1 JSON manifest containing
identity, row/byte counts, completion time, and an index SHA-256. Cache hits
validate manifest fields, exact size, and digest before opening a read-only
memmap. Invalid or manifest-less final entries are discarded and rebuilt.

New content is written/fsynced under a unique direct-child temporary directory.
The pending manifest is renamed inside that invisible directory; a final
directory rename publishes the complete pair atomically. Injected failures
before publication leave no discoverable completed entry. The desktop is a
single-instance application; exact-key publication is owned by that process.

The default cache budget is 256 MiB and eight completed entries. Completed
entries are refreshed on hits and oldest-first eviction enforces both limits.
Windows entries with a currently open memmap are skipped; if no old entry can
be removed, the new index remains usable in memory but is dropped from disk
rather than exceeding the budget. Cache-temporary retention after a real
process crash remains part of P07-T06 lifecycle cleanup.

At five million rows, the index file is exactly 20,000,000 bytes. For the
primary Attack descending key, the exact base-entry projection is a 576-byte
manifest and 20,000,576 bytes total; the filtered-view identity uses a 588-byte
manifest and 20,000,588 bytes total. Other keys/directions are projected by
`project_result_sort_cache_entry()` because their stable text lengths differ.
The v1 result-run manifest remains unchanged.

## Paging

`ResultPageRequest` is pinned to `e7.optimizer.result-page` version 1. Page
indexes are zero-based and sizes are checked from 1 through 1,000 (default
100). `ResultPage` reports total rows, page count, start/end offsets,
previous/next flags, and whether the requested page is out of range. Empty view
page zero is valid with zero pages; later page numbers are explicitly out of
range. A page beyond the last returns an empty array rather than fabricating an
error row.

After an index exists, paging copies only the requested ordinal slice: at the
maximum page size the returned read-only array is 4,000 bytes. It does not
reopen numeric sort columns or materialize domain objects.

P07-T04 does not resolve detailed gear/replacement explanations, export, retain
or clean result runs, persist optimization requests, add optimizer protocol/UI,
use SQLite, or import CuPy. Those remain P07-T05 through P08 work.
