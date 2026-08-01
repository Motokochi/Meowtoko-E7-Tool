# Fribbels-compatible item priority scoring

`priority_scoring.py` is the CPU authority for the optimizer's `Prio` column.
It follows Fribbels' item-priority model: score each of the six equipped pieces
independently, round each piece to an integer, and sum those six integers.
Final hero stats and all 15 derived metrics are retained unchanged.

## Pinned source

The formula is pinned to Fribbels revision
`b291cbbc415f11abede146859edc7b67d26e9c4b`:

- `app/js/lib/priorityFilter.js`, Git blob
  `f1aacc90e5e45c6724c8d4521a85de39976f4be3`.

## Per-piece formula

An item's selected current or reforged totals include both its main stat and
substats. Flat Attack, Health, and Defense are converted against the selected
hero's base stat, then combined with their percentage counterparts. The eight
maximum-roll divisors are:

| Stat | Divisor |
| --- | ---: |
| Attack | `baseAttack * 0.08` |
| Health | `baseHealth * 0.08` |
| Defense | `baseDefense * 0.08` |
| Speed | `4` |
| Crit Chance | `5` |
| Crit Damage | `7` |
| Effectiveness | `8` |
| Effect Resistance | `8` |

For each item:

```text
itemUnrounded = sum((itemStatContribution / divisor) * priority)
itemPriority  = Math.round(itemUnrounded)
buildPriority = sum(itemPriority for the six pieces)
```

Priorities are integers from `-1` through `3`. The implementation reproduces
JavaScript `Math.round` as `floor(value + 0.5)` and uses the project's
CPU/CUDA binary32 contribution contract before that rounding boundary.
`PriorityPieceDiagnostic` retains each piece's unrounded and rounded score;
`PriorityStatDiagnostic` retains the normalized per-stat evidence.

## What does not affect Prio

The score measures owned gear, not the completed final-stat sheet. Therefore
it excludes:

- all set bonuses and penalties;
- artifacts, imprints, exclusive equipment, and custom hero bonuses;
- final Attack, Health, or Defense multipliers;
- Crit Chance and Crit Damage caps;
- derived metrics and target Defense;
- primary/derived range pass or failure.

Raw Crit contribution beyond the final hero cap still affects an item's
priority, exactly as it does in Fribbels. Current/reforged selection does
affect the score because it changes the selected item totals.

The optimizer remains exhaustive. Fribbels' optional per-slot “Top X%” item
prefilter is intentionally not enabled, so priority never removes a
permutation before search.
