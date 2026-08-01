# Derived metrics

`derived_metrics.py` is the pure P03-T04 stage after completed sets and primary
bounds. It calculates every Fribbels-style filter metric even when a primary
bound already failed. It performs no repository lookup, persistence, search,
clock, UI, or device work.

## Pinned source evidence

All behavior was traced at Fribbels offline revision
`f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`:

| Purpose | Path | Git blob SHA-1 |
| --- | --- | --- |
| CPU metrics, skills, build score | `backend/src/main/java/com/fribbels/core/StatCalculator.java` | `dfd9b1e363905a0aef3a2fca2e3369acde8d020e` |
| Active GPU metrics and filtering | `backend/src/main/java/com/fribbels/gpu/GpuOptimizerKernel.java` | `80d34477fd0548be8f63f4086884756febac5425` |
| Per-item WSS/gear score | `backend/src/main/java/com/fribbels/db/ItemDb.java` | `de493420a0e6167c7a066f5d35a7a4f4e3edd623` |
| Hit/support source mapping | `backend/src/main/java/com/fribbels/model/Hero.java` | `af788037c2fd4f8fb08b426d3c4b10ab8bdb2568` |
| Skill default values | `backend/src/main/java/com/fribbels/model/SkillData.java` | `3b59146d0451e9a505c548bbf992afc17375a3f6` |
| Frontend skill option expansion | `app/js/lib/heroData.js` | `e76e07d1febe303a6758b884310910ce5983e501` |
| Frontend hit mapping | `app/js/lib/damageCalc.js` | `cd40154bada34558253b2e3e619b86ff76d051bb` |
| GS/BS UI definitions | `app/js/lib/tooltip.js` | `43b0620e3b205b0942f10d573e3ecc61f123bbb1` |
| Default Penetration target Defense | `app/js/lib/settings.js` | `baeff51b79e2fb7f433a90548919cda74e43f632` |

The source checkout used for this audit is temporary and is not a runtime or
build dependency.

## Canonical metric IDs

The immutable catalog is lexical because `BuildMetrics` already canonicalizes
string-keyed values in that order:

| Stable ID | Label | Fribbels field |
| --- | --- | --- |
| `metric.build_score` | Build Score | `bs` |
| `metric.cp` | Combat Power | `cp` |
| `metric.damage` | Average Damage | `dmg` |
| `metric.damage_defense` | Damage × Defense | `dmgd` |
| `metric.damage_health` | Damage × Health | `dmgh` |
| `metric.damage_speed` | Damage × Speed | `dmgps` |
| `metric.ehp` | Effective Health | `ehp` |
| `metric.ehp_speed` | EHP × Speed | `ehpps` |
| `metric.gear_score` | Gear Score | `score` |
| `metric.hp_speed` | Health × Speed | `hpps` |
| `metric.mcd` | Max Critical Damage | `mcdmg` |
| `metric.mcd_speed` | MCD × Speed | `mcdmgps` |
| `metric.s1` | S1 | `s1` |
| `metric.s2` | S2 | `s2` |
| `metric.s3` | S3 | `s3` |

Labels are presentation metadata and do not identify calculations.

## Numeric view and rounding

Attack, Health, and Defense use P03-T02's retained unrounded binary32 totals.
Speed, Crit Chance, Crit Damage, Effectiveness, and Resistance use displayed
integers. Gameplay Crit inputs use P03-T03's effective caps: Chance is at most
100 and Damage at most 350. `BuildMetrics.final_stats` is the same effective
eight-stat view.

Every Java `float` operation is rounded to IEEE-754 binary32 in source order.
Final Java casts are truncation toward zero. CP deliberately changes to
binary64 at `(1.0 + ...)`, because `1.0` is a Java double literal. The remaining
CP expression stays binary64 until its final integer cast. Golden coverage has
a boundary where this produces 238800 while an all-binary32 rewrite produces
238801.

With `A`, `H`, `D`, and `S` for Attack, Health, Defense, and Speed, `C` for
capped Crit Chance divided by 100, and `K` for capped Crit Damage divided by
100, the base formulas are:

```text
CP = trunc(((A*1.6 + A*1.6*C*K) * (1.0 + (S-45)*0.02)
            + H + D*9.3)
           * (1 + (Resistance/100 + Effectiveness/100)/4))

EHP           = trunc(H * (D/300 + 1))
HP×Speed      = trunc(H * S/1000)
EHP×Speed     = trunc(EHP * S/1000)
AverageDamage = trunc((C*A*K + (1-C)*A) * PenSet * PercentDamage)
Damage×Speed  = trunc(AverageDamage * S/1000)
MCD           = trunc(A*K * PenSet * PercentDamage)
MCD×Speed     = trunc(MCD * S/1000)
Damage×Health = trunc(K*H * PenSet * PercentDamage / 10)
Damage×Defense= trunc(K*D * PenSet * PercentDamage)
```

`Damage×Health` follows the active GPU multiplication/division order. The CPU
source divides by ten earlier; that can differ at a binary32 boundary.

## Damage set effects

Completed groups come only from P03-T02 diagnostics:

```text
PercentDamage = 1 + 0.3*RageGroups
                  + 0.1*TorrentGroups
                  + 0.2*min(FervorGroups, 1)

PenSet = (TargetDefense/300 + 1)
         / (0.00283333*TargetDefense + 1)
```

`PenSet` is 1 without a completed Penetration set. The request-level
`targetDefense` is the explicit replacement for Fribbels' global Penetration
setting; new requests default to 1000 while callers may choose 1500 to match
the pinned Fribbels UI default. Rage, Penetration, and Fervor are enabled,
matching the pinned source defaults. Torrent stacks once per completed pair.
No non-damage set changes these multipliers.

For S1/S2/S3, a completed Penetration set additionally reduces remaining
Defense by 15% only for a source/overridden target count of exactly one. This
uses each skill context's own target Defense. A multi-target value affects this
single-target test only; it is not a damage multiplier.

## Skills

The engine consumes an already-resolved immutable
`HeroSkillContextSelection`. Null hit type chooses the source record's first
supported hit type. Critical uses `K + criticalDamageIncrease`; crushing,
normal, and miss use 1.3, 1, and 0.75. Missing hit/target evidence and passive
skills return zero with `SkillMetricKind.UNAVAILABLE` instead of inventing
damage.

For the selected skill scalars:

```text
remainingPen = (1 - penetration)
               * (1 - PenSetCompleted*0.15*singleTarget)
scaling = selfHp*H + selfAtk*A + selfDef*D + selfSpeed*S
hit = criticalFlag*(K + criticalDamageIncrease) + nonCriticalHit
extra = (extraHp*H + extraAtk*A + extraDef*D)
        * 1.871 / (targetDefense*0.3/300 + 1)
offense = (A*rate + scaling) * 1.871 * pow * (1 + increasedValue)
          * hit * (1 + selfSpeed*S) * PercentDamage
defense = 1 / (targetDefense*max(0, remainingPen)/300 + 1)
value = trunc(offense*defense + support + extra)
```

The bundled direct source has no interpreted `selfAtkScaling` or
`extraSelfHpScaling`, so those terms are zero. A selected source option is a
complete alternate `SkillData` record: its rate, power, target, and self
HP/Attack/Defense scalings apply, while absent advanced fields take the source
model's zero defaults. Heal/barrier options return their HP/Attack/Defense
support amount with `SkillMetricKind.SUPPORT`; they never fabricate offensive
damage.

The active CPU skill method reads one global target Defense, while the active
GPU path receives a request target Defense. This engine generalizes the GPU
behavior to P02's authoritative per-skill Defense contexts. EE choices affect
the aggregated stats when they define a supported stat contribution. The
pinned data does not define an additional EE-specific skill-formula operator,
so none is invented.

## Gear score and build score

Gear score is the sum of six independently rounded item WSS values. Main stats
are excluded, exactly as `ItemDb.calculateWss` scores the `AugmentedStats`
substat fields:

```text
WSS = Attack% + Defense% + Health% + Resistance + Effectiveness
      + Speed*(8/4) + CritDamage*(8/7) + CritChance*(8/5)
      + FlatAttack*(3.46/39) + FlatDefense*(4.99/31)
      + FlatHealth*(3.09/174)
```

Each nonnegative WSS uses Java `Math.round` (`floor(value + 0.5)`). Current and
reforged execution select the corresponding complete totals and main value.
P03-T01 now carries this immutable main-stat evidence into item diagnostics;
it does not trust imported `wss` fields or reparse source rows.

`calculate_item_gear_score()` exposes the same main-stat-excluding item
calculation to P04 search preparation, allowing later hot loops to sum
precomputed integer contributions without trusting imported cached scores.

Build score is a hero-build statistic and is intentionally not gear score. It
uses unrounded Attack/Health/Defense and raw, uncapped displayed values for the
other five stats. Base, artifact flat, and active numeric set contributions are
removed. The residual Health%, Attack%, Defense%, Crit Chance, Crit Damage,
Effectiveness, Resistance, and Speed components are weighted:

```text
BS = trunc(HP% + Attack% + Defense%
           + CritChance*1.6 + CritDamage*1.14
           + Effectiveness + Resistance + Speed*2)
```

Raw over-cap Crit therefore changes build score but not gameplay damage.

## Bounds and exclusions

All 15 catalog values receive one immutable evaluation in canonical order.
Minimum and maximum are inclusive. An omitted range, an explicitly stored
blank range, and a numeric zero remain distinguishable. Unknown metric IDs
fail at engine entry without changing the version-6 persisted request shape.

P03-T04 leaves `BuildMetrics.priority_score` at zero. Priority scoring consumes
this result without recalculating its metrics or bound outcomes; its separate
formula is documented in `PRIORITY_SCORING.md`. Target-set matching,
enumeration/search, CUDA execution, result storage, and UI behavior remain
outside this stage.
