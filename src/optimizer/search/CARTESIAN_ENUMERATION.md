# Batched Cartesian enumeration

`cartesian.py` is the pure iteration boundary between prepared per-slot arrays
and later build evaluation. `create_cartesian_search_space()` accepts only a
validated `SearchReadySlotArrays` value and retains six positive integer
radices—not inventory, gear objects, stable IDs, diagnostics, or contribution
arrays.

## Mixed-radix order

The six radix positions always mean Weapon, Helmet, Armor, Necklace, Ring, and
Boots. Boots is the fastest-changing (rightmost) position, matching ordinary
nested-loop/Cartesian-product order. For radices `(2, 1, 1, 1, 2, 3)`, flat
indices begin:

```text
0 -> (0, 0, 0, 0, 0, 0)
1 -> (0, 0, 0, 0, 0, 1)
2 -> (0, 0, 0, 0, 0, 2)
3 -> (0, 0, 0, 0, 1, 0)
```

`flat_index_to_slot_offsets()` and `slot_offsets_to_flat_index()` are exact
inverses over the product. Python integers retain arbitrarily large exact
products; there is no platform-width overflow or five-million result cap at
this enumeration layer.

## Batches and progress

`iter_cartesian_batches()` returns a lazy `BatchedCartesianEnumerator`.
Construction consumes no permutations. Each `CartesianBatch` owns only a
bounded tuple of six-offset rows for its nonempty half-open `[start, stop)`
range. Batches are contiguous, nonoverlapping, and the final batch may be
shorter than the requested size. A caller may select a validated half-open
subrange for deterministic resume or partitioning; `searched_count` is the
absolute next flat index, so an interior start assumes preceding indices were
already handled.

The enumerator uses an injectable monotonic clock. Frozen
`CartesianEnumerationSummary` snapshots report exact total permutations,
absolute searched count, elapsed seconds, completion, and a caller-supplied
cancellation state. Supplying `cancelled=True` records orchestration evidence;
it does not mutate or stop the iterator. P04-T06's separate synchronous CPU
coordinator owns cancellation checks and progress callbacks at batch
boundaries.

`CartesianSearchSpace`, `CartesianBatch`, and summary records are frozen,
deeply immutable, hashable, and self-validating. The stateful iterator is
deliberately not a retained result record.

## Complexity and exclusions

Search-space creation is `O(6)` time and space. Index conversion is `O(6)`.
One emitted batch uses `O(batch_size * 6)` time and bounded memory independent
of the full Cartesian product.

This module does not filter gear, compile or evaluate set patterns, aggregate
stats, calculate metrics or scores, enforce bounds, match requested sets,
count/retain matches, enforce result overflow, invoke CUDA, persist data, or
communicate with desktop/UI code.
