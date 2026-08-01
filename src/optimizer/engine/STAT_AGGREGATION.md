# Pre-set stat aggregation

`stat_aggregation.py` is the CPU authority for P03-T01. It is pure: callers
provide an explicit character/profile selection, the artifact selection that
resolves the request, and exactly six immutable projected items. It does not
read SQLite, files, clocks, the network, or UI state.

## Pinned formula evidence

The behavior is pinned to Fribbels offline revision
`f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`:

- `backend/src/main/java/com/fribbels/core/StatCalculator.java`, Git blob
  `dfd9b1e363905a0aef3a2fca2e3369acde8d020e`;
- `backend/src/main/java/com/fribbels/gpu/GpuOptimizerKernel.java`, Git blob
  `80d34477fd0548be8f63f4086884756febac5425`.

`StatCalculator.buildStatAccumulatorArr` calculates each item's Attack,
Health, and Defense contribution as `flat + base * percentagePoints / 100`.
Its other displayed values are direct additive contributions. The six item
accumulators are then added in weapon, helmet, armor, necklace, ring, and boots
order. `setBaseValues` places flat and percent hero contributions against the
naked base stat and combines source/profile plus configured final-stat
multipliers. `addAccumulatorArrsToHero` applies those multipliers after item
and set additions, then casts the displayed result to an integer. The GPU
kernel uses the same ordering and single-precision values.

`calculate_item_final_contributions()` exposes this same validated binary32
per-item conversion to P04 search preparation. It returns eight pre-set values
in canonical final-stat order and deliberately does not apply completed sets,
final hero multipliers, or display truncation.

## Units and formulas

Gear Attack/Health/Defense percentages are percentage points: `18` means 18%.
Typed hero-modifier percentages are ratios: `0.18` means 18%. Conversion is
performed only where the modifier joins a displayed percentage-point stat.
Artifact Attack, Health, and Defense are flat values.

For `X` in Attack, Health, and Defense, each item is first reduced to:

```text
itemX = itemFlatX + baseX * itemPercentX / 100
```

The pre-set value is then:

```text
insideX = baseX + baseX * typedRatioX + typedFlatX + artifactFlatX
          + itemX[weapon] + itemX[helmet] + itemX[armor]
          + itemX[necklace] + itemX[ring] + itemX[boots]
finalMultiplierX = 1 + sourceBonusMaxXRatio + typedFinalXRatio
X = insideX * finalMultiplierX
```

The exact selected profile's immutable source record supplies
`bonusMaxAtkPercent`, `bonusMaxHpPercent`, and `bonusMaxDefPercent`, defaulting
each missing field to zero. Typed final multiplier contributions use ratio
units (`0.25` means 25%) and combine additively with that source ratio, matching
Fribbels' `1 + source/100 + configured/100` expression. They are applied after
numeric set insertion by P03-T02.

Speed is `base + six item values + typed flat Speed`. Crit chance,
Effectiveness, and Effect Resistance are `base + six item percentage-point
values + typedRatio * 100`. Crit damage has no typed modifier kind and is
`base + six item percentage-point values`. Dual-attack chance is retained in
modifier diagnostics but has no member among the eight P03-T01 final stats.

The compatibility maps `imprintBonuses`, `exclusiveEquipmentBonuses`, and
`customBonuses` are never added. When typed data exists it is authoritative;
when a nonempty legacy map has no typed source, aggregation fails because flat
and percent Attack/Health/Defense cannot be recovered without guessing.

## Precision and rounding

Every primitive numeric operation is rounded to IEEE-754 binary32, matching
the pinned Java `float` and CUDA `float` paths. Item accumulators and final
addition order are fixed as described above. No intermediate decimal,
display, or integer rounding occurs. All eight nonnegative display totals are
truncated toward zero once, at the final boundary, matching Java's float-to-int
cast. Diagnostics retain the final unrounded binary32 values.

Future CPU and CUDA implementations must preserve these operation boundaries
and canonical slot order. A mathematically equivalent regrouping can differ at
a binary32 boundary and is therefore not the contract.

Diagnostics also expose `configured_naked_stats`, `final_stat_multipliers`,
`set_insertion_stats`, and `post_set_modifier_contributions`. The configured
naked view excludes all item and set contributions but includes every fixed
profile/artifact/typed modifier and final multiplier. Attack/Health/Defense
insertion values include ordinary typed modifiers and artifact flats but stop
before the final multiplier. The other five insertion values stop after item
additions, with their additive typed modifier retained separately. P03-T02 uses
these boundaries to insert set effects before final multiplication/addition;
the ordinary P03-T01 result remains the complete no-set final value.

## Current and reforged projections

`OptimizationRequest.itemProjectionMode` must explicitly select
`projection.current` or `projection.reforged` before aggregation. Persisted
profile and run schemas are version 7. Their v4-to-v5 migration writes `null`,
which means “not selected”; it never invents a historical choice. New callers
must resolve that null before calculating. The later v5-to-v6 migration adds
only no-op gear-filter defaults and does not alter projection selection.
The v6-to-v7 migration adds only the unrelated default maximum replacement
distance and likewise does not alter projection selection.

`ProjectedGearItem` always requires a complete, finite, nonnegative total for
all eleven item stat kinds. `from_gear_item` supplies only a current view and
does not fabricate a reforge. `from_fribbels_inventory_item` retains both
parser totals and maps the exact source evidence state to valid, missing, or
invalid-fallback diagnostics. Missing or invalid Fribbels summary arrays can
still have a complete deterministic total derived by the parser from its
main/substat evidence; the engine uses that total and exposes the evidence. A
genuinely absent or partial requested total fails actionably.

The projection also retains the item's canonical main-stat type and selected
current/reforged main value when it is known. These values do not change stat
aggregation. P03-T04 uses them only to subtract the main stat from the complete
projection before reproducing Fribbels' substat-only item gear score. Domain
and imported-item adapters populate this evidence without trusting cached
`wss` fields; direct low-level projections may omit it until gear score is
requested.

Stable IDs must be unique. Ephemeral dense IDs remain separate and must also
be unique when supplied; null dense IDs are valid before a search snapshot
assigns them.

## Deliberately deferred

P03-T01 does not activate or report completed sets, apply stat caps, evaluate
bounds, calculate damage or derived metrics, score priorities, calculate
constraint distance, enumerate builds, or interpret skill formulas. Those
belong to P03-T02 and later tasks.
