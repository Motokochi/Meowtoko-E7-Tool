# Streaming result export contract

`src.optimizer.result_store.exporting` is the hardware-independent
`e7.optimizer.result-export` version 1 boundary. It streams a selected compact
P07 view to CSV or JSON without resolving millions of P07-T05 page objects.

## Checked view authority

An export view is immutable and names the exact completed-run fingerprint:

- a base view has no ordinal array and visits physical ordinals `0..rowCount`;
- a filtered view reuses the read-only, strictly ascending `<u4` ordinals from
  P07-T03 and fingerprints their exact bytes; and
- a sorted view reuses the completed P07-T04 `<u4` index, its cache key, and its
  base/filtered source-view fingerprint.

Before any output file is opened, export requires the request, active resolver
context, reproducibility record, view, and completed run to agree on session,
run, view, hero/profile, row count, run fingerprint, inventory/search snapshot,
projection mode, and compiled set target. A stale or forged boundary fails
closed.

## Stable row schema

Both formats use the following 43 fields in this exact order:

1. `rowOrdinal`;
2. six `item:<slot-id>` stable owned-gear IDs in canonical slot order;
3. six `set:<slot-id>` canonical `set.*` IDs;
4. `category` and `replacementCount`;
5. eight `primary:<final-stat-id>` signed-int64 values;
6. 15 `derived:<metric-id>` signed-int64 values;
7. `priorityScore` and its exact lowercase eight-hex-digit
   `priorityScoreBits` binary32 representation;
8. `constraintDistance` and `constraintDistanceBits`; and
9. `equippedItemCount`.

Dense IDs are never exported as durable gear identity. Each is checked against
the active search snapshot, full inventory gear, canonical slot, stored set,
and equipped owner before its stable ID is written. The complete gathered chunk
also passes the P07 column schema validator without dtype conversion.

CSV is UTF-8 without a BOM, always has one header row, uses `\n` line endings,
and applies standard CSV quoting for commas, quotes, and embedded newlines.
Signed int64 values are base-10 decimal text. JSON is a UTF-8 array with one
compact object per line, exact field insertion order, numeric decimal integer
tokens, and a trailing newline; an empty view is exactly `[]` plus newline.
The JSON text preserves the full decimal integer, although consumers that map
JSON numbers only to IEEE-754 doubles must use an arbitrary-precision parser for
values outside their safe-integer range. Binary32 numeric values remain useful
for ordinary tools while the adjacent bit fields provide exact lossless
reconstruction, including adjacent values and signed zero.

## Bounded execution

The default chunk is 8,192 rows and the checked maximum is 131,072. A nonempty
export opens each of the ten result columns once and gathers only the selected
ordinal slice for the current chunk. It writes one serialized row at a time;
it never builds a full-view list or calls the page/detail resolver.

The declared numeric array peak is:

`peak rows * (233 stored bytes + 4 ordinal bytes)`

That is 1,941,504 bytes at the default chunk and 31,064,064 bytes at the maximum
chunk, independent of a view containing one row or five million. Python string,
CSV/JSON encoder, memmap, and filesystem buffering overhead is runtime-specific;
the implementation retains at most one serialized row object beyond the
numeric chunk. P07-T07 will measure actual cap-scale RAM and latency.

## Atomic publication, cancellation, and overwrite

Construction normalizes the explicit destination but performs no I/O. Execution
requires its parent to already be a plain, non-reparse directory and the file
extension to match the selected format. It writes the adjacent
`.<destination>.<operation-token>.e7-export.tmp`, flushes and fsyncs it, computes
its SHA-256, checks cancellation again, and only then publishes.

The default no-overwrite path uses a same-directory hard link so a destination
that appears concurrently is never replaced. `overwrite=True` is explicit and
can replace only a destination that was verified as a plain file. Cancellation,
stale authority, encoding/schema failure, an injected checkpoint failure, or a
publication conflict leaves no visible partial destination. A recognized temp
that cannot be unlinked after successful hard-link publication is harmless—the
destination is already complete—and P07 lifecycle cleanup can reclaim it.

`ResultExportOutcome` reports destination, format, view fingerprint, row/chunk
counts, exact byte size, SHA-256, and the declared memory projection. Export
does not touch SQLite, import CuPy, execute CUDA, create UI/protocol state, or
generate P05 replacement explanations.
