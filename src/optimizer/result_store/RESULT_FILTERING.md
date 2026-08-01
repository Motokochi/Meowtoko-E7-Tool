# Completed-result filtering

`filtering.py` defines the P07-T03 authority for selecting rows from a
published `e7.optimizer.result-run` v1 directory. It does not change the run
manifest, search output, or 233-byte row ABI.

## Request and scope contracts

`ResultFilterRequest` is immutable, hashable, and pinned to
`e7.optimizer.result-filter` version 1. Its vector axes are always the complete
canonical `FINAL_STAT_ORDER` (eight entries) and `DERIVED_METRIC_IDS` (15
entries); wrong-length or wrong-type axes are rejected rather than reordered or
narrowed implicitly. Duplicate category, item, and replacement-slot selections
canonicalize to sorted unique tuples. Duplicate per-slot or per-set predicates
must agree.

All predicates compose with logical AND:

- category selection covers exact, one-away, and two-away codes;
- replacement distance, equipped count, every primary/derived value, priority,
  and normalized constraint distance use inclusive minimum/maximum ranges;
- a blank minimum or maximum leaves that side unbounded, and two blanks make
  that range unrestricted;
- global included item IDs mean “the row contains any selected ID”; global
  excluded IDs mean “the row contains none of these IDs”;
- per-slot item predicates mean that the slot contains one of its allowed IDs;
- set predicates independently constrain owned-piece and activated-set counts;
  and
- replacement-slot selection means at least one selected slot occurs in at
  least one mathematically minimal P05 transition.

An empty category selection and an explicitly present empty per-slot item
selection match no rows. Empty global included/excluded item and replacement-
slot selections are no-ops. This distinction makes UI clearing deterministic.

Primary and derived endpoints are checked signed 64-bit integers and compared
directly to the stored `<i8` columns, including negative metric values.
Priority and constraint endpoints reject NaN, infinity, and binary32 overflow,
then explicitly canonicalize once to finite binary32. Comparisons therefore use
the exact stored `<f4` values and preserve adjacent binary32 boundaries.

`OriginalResultScope` belongs to the active completed-search session. It holds
the baseline predicates describing the run's retained scope and the compiled
target set pattern. P07-T06 will persist reproducibility data; v1 manifests are
unchanged here. `assess_filter_scope()` compares every new view to this base
scope—not to the current filtered view—and returns:

- `equal` when the canonical request is identical;
- `tightening` when every predicate is a subset of the base scope; or
- `rerun-required` with actionable reasons when any minimum is lowered,
  maximum raised, bound removed, category enabled, ANY-selection broadened,
  exclusion removed, or another predicate loosened.

A rerun-required outcome has no view index. This prevents an incomplete stored
run from masquerading as a complete answer. A tight view can later be widened
within the original scope by filtering the base run again.

## Set and replacement derivation

Owned set IDs remain aligned with the six canonical gear slots. For each chunk,
the filter counts all 24 set indices without loading inventory rows. Activated
counts divide owned pieces by `SET_CATALOG.pieces_required`; stackable sets keep
all completed activations while non-stackable sets cap at one. Thus six Health
pieces report three activations, while six Speed pieces report one.

For a target required-count vector, every minimal P05 assignment replaces only
surplus-set positions. The union of slots across all alternatives is therefore
exactly every slot whose owned set count exceeds that target set's required
count. The implementation also derives overlap distance and exposes slots only
for distance one or two. Exact and out-of-scope signatures expose none. Tests
compare this grouped/vectorized identity to
`identify_set_replacement_alternatives()` row by row, including the symmetric
five-choice one-away and six-slot two-away cases. No variable-length
alternative list is persisted or constructed in the filter hot path.

## Execution and memory

`filter_completed_result_run()` opens read-only raw-column memmaps only for
predicates that need them. It scans bounded chunks (131,072 rows by default;
the checked maximum is 1,000,000), maintains one vector Boolean mask, and
returns strictly ascending physical `<u4` row ordinals. It never creates
full gear or per-row Python objects.

The ordinal capacity is allocated once at four bytes per base row and shrunk
after the scan: zero rows/matches return an empty read-only array, while five
million all-matching rows use 20,000,000 bytes. Execution statistics report
that capacity separately from a conservative declared vector-workspace ceiling
of 128 bytes times the largest processed chunk. With the default chunk this is
16,777,216 bytes. Memmapped source pages, the small immutable request, and OS
filesystem cache are not heap copies and are outside that declared workspace.

P07-T03 does not sort, page, cache sort indexes, resolve detailed builds,
persist requests, export, clean retained runs, benchmark five-million-row UI
interaction, add protocol/UI routes, use SQLite, or import CuPy. Those remain
P07-T04 through P08 work.
