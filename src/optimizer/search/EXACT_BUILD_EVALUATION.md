# Exact Build Evaluation

`exact_evaluation.py` is the deterministic P04 CPU numeric oracle for complete
requested sets. It consumes boundaries that have already been validated and
prepared by earlier phases:

- `SearchReadySlotArrays` supplies six canonical parallel candidate arrays in
  Weapon, Helmet, Armor, Necklace, Ring, Boots order;
- `CompiledSetPattern` supplies the exact 24-position required-piece vector;
- `CartesianBatch` supplies canonical mixed-radix candidate offsets; and
- resolved P02 request, profile, artifact, and skill selections compile once
  into `ExactBuildEvaluationContext`.

The evaluator does not query repositories or rebuild `GearItem` or
`ProjectedGearItem` objects. The hot loop indexes numeric arrays, counts six
set indices, and skips a permutation unless its actual 24-position piece-count
vector equals the required vector exactly.

## Compiled context

`compile_exact_build_context()` validates selection identity and precomputes
all values shared by a batch: base and configured-naked stats, final
multipliers, set-insertion stats before item contributions, post-set additive
modifiers, artifact flats, exact activation counts, canonical numeric set
contributions, hard-bound vectors, priorities, damage-set multipliers, and
three scalar skill formulas.

The record is frozen, slotted, deeply immutable, hashable, and self-validating.
It pins the request ID, hero ID, profile ID, item projection mode, and base
stats so a prepared inventory cannot accidentally be evaluated against a
different request or hero configuration. Set contribution rows and damage
multipliers are checked against the authoritative P03 helpers during direct
construction.

`validate_exact_build_search_context()` exposes the same request, hero,
profile, base-stat, projection-mode, and slot-radix validation independently
of a batch. P04-T06 uses it before its first cancellation check can return, so
even a zero-permutation cancelled run cannot accept mismatched prepared and
compiled contexts. `evaluate_exact_build_batch()` delegates to this one
validation authority before checking its batch radices.

## Numeric evaluation order

For an exact row, each stat starts from its compiled set-insertion base. Six
precomputed item contributions are added in canonical slot order with an
IEEE-754 binary32 boundary after every operation. Numeric completed-set
effects are inserted in P03's canonical stat-specific order. Attack, Health,
and Defense then receive their final multipliers; the other supported hero
modifiers are added after set insertion. Displayed raw stats truncate only
after those operations.

Crit Chance and Crit Damage effective values are capped at 100 and 350. Hard
primary bounds use effective values and are inclusive. All 15 canonical
derived metrics are still computed in `DERIVED_METRIC_IDS` order, using P03's
mixed unrounded/effective views, damage-set effects, three resolved skills,
summed precomputed item gear score, and build score. Derived bounds are also
inclusive. Priority scoring sums the six independently rounded, weighted item
contribution scores. Sets, final-stat caps, and configured hero bonuses do not
affect priority; priorities never affect derived metrics.

## Compact layouts

`ExactBuildRow` contains only stable numeric values:

1. one flat permutation index;
2. six dense item IDs in Weapon through Boots order;
3. eight unrounded final stats in `FINAL_STAT_ORDER`;
4. eight raw truncated final stats in `FINAL_STAT_ORDER`;
5. eight capped effective final stats in `FINAL_STAT_ORDER`;
6. 15 integer derived metrics in `DERIVED_METRIC_IDS`; and
7. one binary32 representation of the integer item-priority sum.

Stable string item IDs remain in the cold reverse map on
`SearchReadySlotArrays`; verbose P03 diagnostics are not copied into hot rows.

`ExactBuildBatchResult` preserves ascending flat-index order and reports:

- every permutation in the supplied half-open batch as `evaluated_count`;
- exact set-vector matches as `exact_set_count`;
- exact rows omitted by primary or derived bounds as
  `hard_bound_rejected_count`; and
- retained rows as `emitted_count` and `rows`.

Nonexact permutations are `evaluated_count - exact_set_count`. Exact rows
partition exactly into rejected plus emitted counts.

## Deliberate exclusions

This layer does not calculate near-set distance, one-away/two-away categories,
or replacement guidance. It also does not own the five-million overflow rule,
cancellation/progress callbacks, clocks, CUDA, persistence, paging, desktop
protocol, or UI behavior; the CPU coordinator composes those run-level
boundaries.
