# Primary-stat caps and bounds

`primary_stat_bounds.py` is the P03-T03 CPU authority for applying gameplay
caps and evaluating the eight primary-stat ranges in an `OptimizationRequest`.
It is a pure immutable layer over a completed P03-T02 `SetEvaluationResult`.
It does not recalculate items, projections, modifiers, or completed sets.

## Raw and effective values

P03-T02 `final_stats` remain the raw, uncapped displayed totals. P03-T03
creates a separate effective tuple in the same canonical `FINAL_STAT_ORDER`.
The original set result and raw tuple are retained unchanged in
`PrimaryStatBoundsResult`.

`PRIMARY_STAT_RULES` is the data-driven cap catalog:

| Primary stat | Gameplay upper cap |
|---|---:|
| Attack | none |
| Health | none |
| Defense | none |
| Speed | none |
| Crit Chance | 100 |
| Crit Damage | 350 |
| Effectiveness | none |
| Effect Resistance | none |

The tuple is canonical and immutable. Evaluation code applies each rule rather
than embedding Crit-specific branches.

## Source provenance and deliberate bound semantics

Evidence is pinned to public Fribbels offline revision
`f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`:

- `backend/src/main/java/com/fribbels/core/StatCalculator.java`, Git blob
  `dfd9b1e363905a0aef3a2fca2e3369acde8d020e`;
- `backend/src/main/java/com/fribbels/gpu/GpuOptimizerKernel.java`, Git blob
  `80d34477fd0548be8f63f4086884756febac5425`.

Those sources retain raw integer Crit Chance and Crit Damage, then use at most
100% Crit Chance and 350% Crit Damage for CP and damage calculations. The
legacy Fribbels optimizer limit predicates compare raw values.

The accepted E7 P03-T03 contract requires game caps to be data-driven and raw
values to remain available for diagnostics. User Crit Chance bounds use the
raw displayed value, matching result filtering, sorting, detail cards, and
exports. Other primary-stat bounds use the effective gameplay value. For
example, raw Crit Chance 127 is rejected by a maximum of 106 while damage and
CP calculations still use its effective value of 100.

## Inclusive and blank-aware evaluation

`OptimizationRequest.stat_ranges` is already validated and stored in canonical
order by the domain layer. P03-T03 preserves these distinctions:

- an omitted range is unrestricted and has `range_supplied == False`;
- an explicit `StatRange()` is unrestricted but has
  `range_supplied == True`;
- zero is a real minimum or maximum;
- values equal to a supplied minimum or maximum pass;
- values below a minimum or above a maximum fail on that exact side;
- a requested minimum above a finite cap is `minimum-above-cap`, except for
  Crit Chance because its bounds deliberately use the raw displayed value.

Every result contains eight `PrimaryStatBoundEvaluation` records in canonical
order. Each record contains the raw value, effective value, optional upper cap,
requested range, whether the range was supplied, status, cap application, and
failure side. `failures` is the canonical filtered tuple and `passes` is true
only when it is empty.

## Downstream distance boundary

P03-T03 does not itself calculate derived or damage metrics, priority scores,
skill values, requested-set matches, search eligibility, result storage, CUDA,
or UI state.
