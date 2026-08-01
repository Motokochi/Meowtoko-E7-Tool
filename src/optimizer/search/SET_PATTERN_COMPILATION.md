# Set-pattern compilation

`compile_set_pattern()` is the pure boundary between the saved domain request
and later CPU/CUDA search stages. It accepts only an already validated,
canonical `SetPattern`; raw mappings and stable-ID parsing remain domain work.

## Numeric layout

`CompiledSetPattern` is frozen, deeply immutable, and hashable. It stores:

- `kind`: the stable `4+2` or `2+2+2` shape;
- `selected_set_indices`: one Fribbels index per selected group, preserving the
  domain's four-piece-first then set-index ordering (and repeated groups);
- `group_piece_counts`: `(4, 2)` or `(2, 2, 2)`;
- `required_piece_counts`: 24 counts in `FRIBBELS_SET_ORDER`, aggregating
  repeated groups and summing to six;
- `expanded_required_set_indices`: six indices obtained by expanding each
  selected group in canonical group order.

For example, Speed + Health compiles to selected indices `(3, 0)`, group
counts `(4, 2)`, required counts of four at index 3 and two at index 0, and
expanded indices `(3, 3, 3, 3, 0, 0)`. Health + Health + Defense compiles to
`(0, 0, 1)`, aggregates to four Health plus two Defense pieces, and expands to
`(0, 0, 0, 0, 1, 1)`.

The compiler consumes `SET_CATALOG` for piece sizes, stackability, and stored
Fribbels indices. Before emitting data, it verifies every stored index against
the corresponding position in the fixed 24-entry `FRIBBELS_SET_ORDER`.
`CompiledSetPattern` independently revalidates its complete numeric shape, so
invalid direct construction cannot introduce a corrupt hot-path vector.

## Deliberate exclusions

This module does not inspect inventory, filter items, enumerate combinations,
calculate build stats or metrics, count/retain results, handle cancellation,
persist data, invoke CUDA, or communicate with the desktop application.
