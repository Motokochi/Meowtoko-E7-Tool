# Compact result schema v2

`schema.py` is the single immutable authority for retained optimizer result
columns. Schema ID `e7.optimizer.result-columns`, version `2`, stores fixed-
width NumPy arrays only. Importing it does not create a run directory, memmap,
SQLite database, UI object, CUDA context, or user-data file.

## Identity and stable order

The zero-based physical row offset is the stable result ordinal. It is logically
an unsigned 32-bit value (`0..4,999,999`) but is not stored as a redundant
column. CPU batches already emit accepted rows in ascending Cartesian flat-
index order; CUDA compaction does the same. P07-T02 must append those batches in
that order, so the physical offset preserves the original stable order.

The six `dense_item_ids`, in canonical weapon, helmet, armor, necklace, ring,
boots order, are the durable permutation key. A source flat index is deliberately
not retained:

- it duplicates both physical ordering and the six-item key;
- the current CUDA transfer ABI needs signed 64-bit flat indices only while
  ordering compacted batches;
- the CPU Cartesian model can describe spaces larger than signed 64-bit, so
  persisting that transient CUDA width would not be a universally lossless
  identity; and
- a flat index can be reconstructed lazily from the six dense IDs and the run's
  canonical slot arrays when a diagnostic needs it.

This saves eight bytes per retained row without losing build identity or stable
tie order.

## Physical columns

Every array has a leading row dimension and is C-contiguous. Vector shapes in
the table exclude that leading dimension. Multi-byte values are explicitly
little-endian; one-byte values have no byte-order distinction. The payload is
structure-of-arrays storage, so there is no per-row struct alignment or padding.

| Order | Column | NumPy dtype | Shape | Bytes/row | Semantic order / rule |
|---:|---|---:|---:|---:|---|
| 1 | `dense_item_ids` | `<i4` | `(6,)` | 24 | canonical six gear slots; unique, `0..2,147,483,647` |
| 2 | `owned_set_indices` | `u1` | `(6,)` | 6 | canonical six gear slots; Fribbels set indices `0..23` |
| 3 | `category_codes` | `u1` | scalar | 1 | exact `0`, one-away `1`, two-away `2` |
| 4 | `replacement_distances` | `u1` | scalar | 1 | equals category code, `0..2` |
| 5 | `effective_final_stats` | `<i8` | `(8,)` | 64 | canonical `FINAL_STAT_ORDER` |
| 6 | `raw_critical_hit_chances` | `<i8` | scalar | 8 | uncapped displayed CR; gameplay formulas still use `min(CR, 100)` |
| 7 | `derived_metrics` | `<i8` | `(15,)` | 120 | canonical `DERIVED_METRIC_IDS` |
| 8 | `priority_scores` | `<f4` | scalar | 4 | six-piece Fribbels item-priority sum |
| 9 | `constraint_distances` | `<f4` | scalar | 4 | finite, nonnegative normalized distance; exact rows are zero |
| 10 | `equipped_item_counts` | `u1` | scalar | 1 | number of the six owned pieces currently equipped, `0..6` |
|  | **Total** |  |  | **233** | no padding |

All physical columns are non-nullable and have no numeric null sentinel. Exact
dtypes are mandatory: validators reject narrower, wider, differently signed,
wrong-endian, non-contiguous, or wrong-shaped arrays instead of allowing NumPy
to wrap or cast them.

New searches write exact rows, so category, replacement-distance, and
constraint-distance values are zero. Those columns remain in schema v2 only to
read existing result runs without an in-place migration.

### Width rationale

- Dense IDs use the signed 32-bit boundary already enforced by the CUDA input
  contract and produced by the contiguous inventory dense-ID map. Keeping the
  same width makes CPU/CUDA rows lossless and avoids a cast at the GPU boundary.
- Twenty-four set values, three categories, replacement distances, and counts
  from zero through six each fit in the smallest addressable NumPy integer,
  `u1`.
- Effective primary stats keep the signed 64-bit CUDA/CPU evaluation ABI. Their
  values are nonnegative, while signedness remains consistent with the engine
  and all arithmetic is validated before storage.
- Raw Critical Hit Chance is retained separately so results can display, sort,
  filter, and export overflow above 100. Damage, CP, and other gameplay
  metrics continue to consume the effective value capped at 100.
- Derived formulas can legally produce negative values, so all 15 metrics stay
  signed 64-bit. Both signed extremes are representable.
- Priority and normalized constraint distance are computed and compared as
  binary32. `<f4` preserves their exact four-byte payload, including every
  finite bit pattern, with no float64 expansion.
- A logical result ordinal fits `u4` because the combined result cap is five
  million. It consumes no payload bytes because array offset is the ordinal.

## Set signature

The schema stores the six owned set IDs in slot order, not only activated set
counts. Six bytes are enough to derive:

- piece counts for every set without resolving full gear;
- completed and activated counts using `SET_CATALOG` piece requirements and
  stackability;
- set filtering and display labels;
- the owned set occupying each slot for `slots to replace` filters; and
- repeated stackable two-piece sets—for example six Health pieces derive three
  activations.

A 24-entry piece-count vector would use 24 bytes and be redundant. Activated
counts alone would lose incomplete-set pieces, slot ownership, and the evidence
needed to explain near-set rows. Packing six five-bit set IDs into a word would
save only two bytes per row (10 MB at the cap) while losing direct NumPy/CUDA
compatibility and requiring bit extraction for every set/slot filter. Six `u1`
values are therefore the smallest practical vectorized representation.

## Lazy replacement metadata

Variable-length P05 replacement alternatives are not embedded in each row and
no four-byte side-table pointer is stored. `replacement_metadata_reference()`
defines the run-local lazy reference contract:

- exact rows return the explicit no-reference sentinel `None`;
- one-away and two-away rows return a frozen key containing schema version,
  physical row ordinal, category, and the six dense item IDs; and
- equal inputs produce equal, hash-stable keys.

The row already stores category, replacement distance, dense IDs, and owned set
IDs. Together with the run's target pattern and inventory/slot-array snapshot,
those fields deterministically reproduce P05 set alternatives, disruption
ranking, future-piece requirements, and display IDs. P07-T05 can therefore
generate and optionally cache details by the lazy key. A physical reference
column would duplicate the row ordinal and cost 20 MB at the result cap.

## Checked payload projection

`project_result_payload()` multiplies the checked row count by the schema's
column sizes and rejects counts above `MAX_RESULT_CAP`.

| Rows | Compact payload | MiB | GiB |
|---:|---:|---:|---:|
| 1 | 233 bytes | 0.000222 | 0.000000217 |
| 1,000,000 | 233,000,000 bytes | 222.206 | 0.216998 |
| 5,000,000 | 1,165,000,000 bytes | 1,111.031 | 1.084991 |

The current CUDA accepted-row transfer ABI is 240 bytes per row plus 96 fixed
counter bytes per batch. The durable schema removes its transient eight-byte
flat index and adds the one-byte equipped count, for a net reduction of seven
bytes per row (35 MB at five million rows).

No run header, array-file header, manifest, temporary duplicate, filter mask,
sort index, or cache format is fixed by P07-T01, so its currently committed
overhead is exactly zero. P07-T02 defines transactional storage/header costs;
P07-T03 and P07-T04 define filter and sort-index costs. Those future costs must
be reported separately from this 1,165,000,000-byte retained payload.

## CPU and CUDA adapter mapping

| Durable field | CPU exact row | CUDA compact batch |
|---|---|---|
| physical ordinal | append offset; input rows already stable | append offset after validating ascending `flat_indices` |
| `dense_item_ids` | `row.dense_ids` | `dense_item_ids` |
| `owned_set_indices` | lookup from dense item IDs | `set_indices` |
| category/distance | exact zero compatibility values | `category_codes`, `replacement_distances` |
| primary stats | `row.effective_final_stats` | `effective_final_stats` |
| raw displayed CR | `row.raw_final_stats[4]` | `raw_critical_hit_chances` |
| derived metrics | `row.derived_metrics` | `derived_metrics` |
| priority | `row.build.priority_score` as exact binary32 | `priority_scores` |
| constraint distance | `row.constraint_distance` as exact binary32 | `constraint_distances` |
| equipped count | count dense IDs whose snapshot item has a non-null owner | same inventory-snapshot lookup |

Adapters are intentionally deferred to P07-T02. This task does not change the
CUDA ABI, allocate five-million-row arrays, write memmaps, expose filters/sorts,
materialize domain results, or add desktop protocol/UI behavior.
