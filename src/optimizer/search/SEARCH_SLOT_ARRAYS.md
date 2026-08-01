# Search-ready slot array contract

`slot_arrays.py` is the pure preparation boundary for the CPU and future CUDA
searches. It performs request-specific filtering and converts retained owned
gear into immutable numeric arrays. It does not compile set patterns, enumerate
builds, calculate full metrics, count matches, persist rows, or call
desktop/UI code.

## Inputs and saved filters

`prepare_search_slot_arrays(request, profile_selection, inventory)` requires an
explicit current/reforged projection mode, the exact selected hero/base
profile, and complete persisted `FribbelsInventoryItem` records. It deliberately
does not accept `DenseInventorySnapshot`: that older repository view omits
projection evidence and assigns dense IDs before request-specific filtering.

`OptimizationRequest.gear_filters` is a frozen `GearSearchFilters` value:

- `right_side_main_stats` optionally restricts Necklace, Ring, and Boots. An
  omitted slot is unrestricted. A supplied slot is non-empty, duplicate-free,
  legal for that slot, and canonicalized in Fribbels item-stat order. Fixed-side
  slots are rejected.
- `minimum_enhance` is an inclusive integer from 0 through 15. The default 0
  keeps every imported enhancement level.
- `excluded_item_ids` is a sorted unique tuple of stable IDs. IDs no longer in
  inventory are harmless and retained in diagnostics as unmatched.

The parser and filter record share `ALLOWED_MAIN_STATS_BY_SLOT` from the domain
catalog, preventing import/search legality drift.

## Deterministic filtering

Input is validated completely and then ordered by canonical slot followed by
stable item ID. The terminal decision order is:

1. existing equipment eligibility policy;
2. explicit stable-ID exclusion;
3. inclusive minimum enhancement;
4. right-side main-stat selection;
5. selected projection availability.

With `Include equipped` disabled, unequipped items and the selected hero's own
items remain eligible. Other and stale non-null owners are excluded. Locked
state is never a filter.

Fribbels import always retains complete current and projected totals. Missing
or inconsistent upstream aggregate fields use the parser's deterministic
per-stat fallback and retain `fribbels.missing` or
`fribbels.invalid-fallback` evidence. Search preparation accepts those complete
fallback projections; it never invents another projection. A genuinely absent
projected view is excluded as `filter.reforged_unavailable`.

Every input item receives one immutable diagnostic containing its stable ID,
slot, eligibility evidence, final inclusion/exclusion reason, and selected
projection evidence when included. Slot summaries retain counts for every
reason in canonical order. If any of the six slots has no candidate,
preparation raises `empty-search-slots` with these completed diagnostics.

## Numeric layout

Dense IDs are assigned only after all filters, contiguously from zero in
Weapon/Helmet/Armor/Necklace/Ring/Boots then stable-ID order. A reverse
dense-ID-to-stable-ID tuple lives outside the hot arrays.

The returned container also retains the request, hero, and base-profile stable
IDs plus the eight selected base stats in `FINAL_STAT_ORDER`. These values are
cold identity evidence: later numeric evaluation must reject a context prepared
for a different request/profile or base-stat vector instead of silently mixing
incompatible precomputed Attack/Health/Defense contributions.

Each `SearchSlotArray` contains equal-length parallel tuples:

1. dense integer item IDs;
2. Fribbels set indices `0..23`;
3. eight binary32 pre-set item contributions in `FINAL_STAT_ORDER`;
4. integer substat-only Fribbels gear-score contributions.

The contribution vector reuses P03-T01 operation boundaries. Flat
Attack/Health/Defense is added to the selected percentage total multiplied by
the chosen hero base stat; the other five entries are direct selected item
totals. Final hero multipliers and set additions are deliberately deferred to
full-build evaluation.

Per-item gear score reuses P03-T04 exactly. The selected main-stat value is
removed, then WSS weights are applied to the eleven item stat totals and Java's
nonnegative `floor(value + 0.5)` rounding is used. Imported cached scores are
never trusted.

All result records are frozen and contain no repository or gear domain objects
inside their numeric arrays. Mapping order, inventory order, repeated calls,
and unrelated source metadata cannot change dense identities or array order.

## Deliberate exclusions

This task does not interpret the requested set pattern, enumerate the Cartesian
product, aggregate six-item stats, enforce primary/derived bounds, rank builds,
enforce the result cap, cancel work, use CUDA, store results, or expose desktop
protocol/UI operations.
