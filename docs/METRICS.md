# Optimizer metric reference

All minimum and maximum fields are inclusive. A blank field means “do not
filter on this boundary”; numeric zero is an actual boundary. Final displayed
integer metrics truncate rather than round unless stated otherwise. The engine
uses deterministic IEEE-754 arithmetic, so this page gives the readable form
while the [engine](../src/optimizer/engine) and its tests define exact arithmetic.

## Primary stats

- **Attack** is final offensive power after the selected base profile,
  artifact, imprint, exclusive equipment, custom modifiers, six items, and
  completed numeric sets.
- **Health** is final maximum Health after those contributions.
- **Defense** is final Defense after those contributions.
- **Speed** is final action speed.
- **Critical Hit Chance** is the displayed percentage and is capped at 100%
  for gameplay damage calculations.
- **Critical Hit Damage** is the displayed percentage and is capped at 350%
  for gameplay damage calculations.
- **Effectiveness** is the displayed debuff-application percentage.
- **Effect Resistance** is the displayed debuff-resistance percentage.

Raw over-cap Critical stats can still contribute to Build Score and item
priority. Critical Hit Chance filters use the raw displayed value; gameplay
damage uses the capped value.

## Derived metrics

Let `A`, `H`, `D`, and `S` be Attack, Health, Defense, and Speed. Let `C` be
capped Critical Hit Chance divided by 100 and `K` capped Critical Hit Damage
divided by 100. `PercentDamage` contains completed Rage, Torrent, and Fervor
set multipliers. `PenSet` is the configured target-Defense multiplier when a
Penetration set is complete, otherwise 1.

```text
Combat Power = trunc(((A*1.6 + A*1.6*C*K) * (1 + (S-45)*0.02)
                     + H + D*9.3)
                    * (1 + (Resistance/100 + Effectiveness/100)/4))

Effective Health (EHP) = trunc(H * (D/300 + 1))
Health × Speed         = trunc(H * S/1000)
EHP × Speed            = trunc(EHP * S/1000)
Average Damage         = trunc((C*A*K + (1-C)*A) * PenSet * PercentDamage)
Damage × Speed         = trunc(Average Damage * S/1000)
Max Critical Damage    = trunc(A*K * PenSet * PercentDamage)
MCD × Speed            = trunc(Max Critical Damage * S/1000)
Damage × Health        = trunc(K*H * PenSet * PercentDamage / 10)
Damage × Defense       = trunc(K*D * PenSet * PercentDamage)
```

**Gear Score** is the sum of six rounded, main-stat-excluding item scores:

```text
WSS = Attack% + Defense% + Health% + Resistance + Effectiveness
      + Speed*(8/4) + CritDamage*(8/7) + CritChance*(8/5)
      + FlatAttack*(3.46/39) + FlatDefense*(4.99/31)
      + FlatHealth*(3.09/174)
```

The read-only **Gear** workspace applies that same formula to each item's
reforged projection and rounds to the nearest whole number:

```text
RGS = complete WSS formula
CGS = WSS excluding Effectiveness and Effect Resistance
SGS = Health% + Defense% + Effect Resistance + Speed*(8/4)
      + FlatDefense*(4.99/31) + FlatHealth*(3.09/174)
```

Main stats are excluded from all three item scores. The selected item card
still displays current imported stats, including the current main stat.

**Build Score** removes the fixed naked-hero baseline and numeric set
contributions, then weights the residual build stats:

```text
Build Score = trunc(HP% + Attack% + Defense%
                    + CritChance*1.6 + CritDamage*1.14
                    + Effectiveness + Resistance + Speed*2)
```

**S1**, **S2**, and **S3** evaluate each skill's selected source option, hit
type, target count, penetration override, and target Defense. Critical,
crushing, normal, and miss use distinct hit multipliers. Single-target
Penetration-set behavior applies only when target count is exactly one.
Non-damaging heal/barrier options return their support amount; passive or
insufficient source evidence is marked unavailable and produces zero rather
than fabricated damage. The full skill equation and set multipliers are in
[the calculation code](../src/optimizer/engine/derived_metrics.py).

## Ranking and closeness

**Priority score** evaluates each gear piece, including its main stat, in
maximum-roll-equivalent units. Each piece is
weighted from `-1` to `3`, and rounded independently; the build score is the
sum of the six piece integers. Set bonuses, hero modifiers, final-stat caps,
and derived metrics do not change `Prio`. `3` strongly favors more of a stat,
`0` is neutral, and `-1` penalizes more of it. Derived metrics remain
independently filterable and sortable. See
[the priority calculation](../src/optimizer/engine/priority_scoring.py).

**Normalized constraint distance** is the build's worst relative miss across
all supplied primary and derived bounds:

```text
minimum miss = (minimum - actual) / max(abs(minimum), stat floor)
maximum miss = (actual - maximum) / max(abs(maximum), stat floor)
build distance = maximum failed-boundary miss, or 0 when all supplied bounds pass
```

The optimizer treats supplied primary and derived bounds as hard filters.
Blank bounds remain unrestricted.


## Per-piece priority formula

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
