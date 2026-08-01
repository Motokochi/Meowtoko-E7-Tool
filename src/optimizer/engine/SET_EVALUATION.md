# Completed-set evaluation

`set_evaluation.py` is the P03-T02 CPU authority for counting the six selected
items and applying completed-set effects to the eight primary stats. It is a
pure layer over `aggregate_pre_set_stats`: no UI, SQLite connection, clock,
network, search state, or requested-target classification enters the result.

## Pinned sources

Behavior is pinned to public Fribbels offline revision
`f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`:

- `backend/src/main/java/com/fribbels/enums/Set.java`, Git blob
  `9b90048232956d96a0dc3a5da7ac90364f477c2d`, defines all 24 indices and
  required piece counts;
- `backend/src/main/java/com/fribbels/core/StatCalculator.java`, Git blob
  `dfd9b1e363905a0aef3a2fca2e3369acde8d020e`, defines the CPU primary-stat
  bonuses and expression order; and
- `backend/src/main/java/com/fribbels/gpu/GpuOptimizerKernel.java`, Git blob
  `80d34477fd0548be8f63f4086884756febac5425`, derives completed groups from
  packed counts and applies the same primary effects in CUDA-compatible
  binary32 values.

`SET_CATALOG` has the same declaration/index order and piece requirements.
`stackable` means that a second complete group changes the modeled effect; it
does not merely mean six items can share a set name.

## Source set table

| Index | Set | Pieces | Effective primary-stat behavior |
|---:|---|---:|---|
| 0 | Health | 2 | +20% naked base Health per complete pair |
| 1 | Defense | 2 | +20% naked base Defense per complete pair |
| 2 | Attack | 4 | +45% naked base Attack |
| 3 | Speed | 4 | +25% naked base Speed |
| 4 | Critical | 2 | +12 Crit chance points per complete pair |
| 5 | Hit | 2 | +20 Effectiveness points per complete pair |
| 6 | Destruction | 4 | +60 Crit damage points |
| 7 | Lifesteal | 4 | Active metadata; no primary stat |
| 8 | Counter | 4 | Active metadata; no primary stat |
| 9 | Resist | 2 | +20 Effect Resistance points per complete pair |
| 10 | Unity | 2 | Active metadata; no modeled primary stat |
| 11 | Rage | 4 | Active metadata; later damage multiplier |
| 12 | Immunity | 2 | Active metadata; no primary stat |
| 13 | Penetration | 2 | Active metadata; later damage/skill behavior |
| 14 | Revenge | 4 | +12% naked base Speed |
| 15 | Injury | 4 | Active metadata; no primary stat |
| 16 | Protection | 4 | Active metadata; no primary stat |
| 17 | Torrent | 2 | −10% naked base Health per complete pair |
| 18 | Reversal | 4 | +15% naked base Speed |
| 19 | Riposte | 4 | Active metadata; no primary stat |
| 20 | Warfare | 4 | +20% naked base Health |
| 21 | Pursuit | 2 | Active metadata; no primary stat |
| 22 | Weakening | 4 | +15% naked base Speed |
| 23 | Fervor | 2 | Active metadata; later damage multiplier |

Health, Defense, Critical, Hit, Resist, Unity, and Torrent are source-modeled
stackable two-piece effects. The other two-piece effects have one effective
activation even when raw piece counts contain multiple complete groups. Every
four-piece set can complete at most once in six slots.

Diagnostics preserve both `completed_groups` and `activation_count`, so six
Fervor pieces record three raw groups but one effective non-stacking effect,
while six Health pieces record and apply three effects.

## Numeric ordering and precision

Set percentages are multiplied by the configured naked base profile, never by
gear-, artifact-, modifier-, or set-increased totals. Each primitive operation
uses IEEE-754 binary32, as in P03-T01.

The set insertion boundary is intentionally exposed by P03-T01 diagnostics.
For Attack, Health, and Defense, base and typed/artifact/item contributions are
already present, numeric sets are added, and the retained source/configured
final-stat multiplier is applied. This matches Fribbels'
`(base + gear + set) * bonusMax` boundary. For Speed, Crit chance, Crit damage,
Effectiveness, and Resistance, the order is base, six item contributions, set
effects, then additive hero modifiers. Integer truncation happens once after
all active primary set effects.

Within a stat, CPU expression order is retained:

- Health: Health, Warfare, then Torrent;
- Speed: Speed, Revenge, Reversal, then Weakening;
- the other affected stats have one set kind each.

The result retains per-set contributions, the active source order, total
numeric contributions in canonical final-stat order, the P03-T01 pre-set
diagnostics, and final unrounded binary32 values.

Numeric-set diagnostics retain the unmultiplied base-relative contribution so
the source formula remains explicit. Final unrounded and display values include
the multiplier exactly once.

## Separation from requested patterns and later calculations

`OptimizationRequest.setPattern` describes what the user wants the later
search/recommendation engine to find. It does not change the actual stats of
the six supplied items. P03-T02 counts only `ProjectedGearItem.gear_set` and
activates only complete groups.

Rage, Penetration, Torrent's damage increase, Fervor's damage increase, Unity's
dual-attack behavior, and every other non-primary gameplay effect are retained
as activation metadata for later tasks. P03-T02 deliberately does not apply
caps or bounds; P03-T03 consumes its raw result through the separate contract
documented in `PRIMARY_STAT_BOUNDS.md`. Damage/derived metrics, skill formulas,
priority scoring and search filtering remain later work.
