# Equipment eligibility contract

`eligibility.py` is the pure domain policy that selects owned gear before a
future optimizer search. It does not import Fribbels persistence, SQLite,
filesystem, desktop, UI, logging, or search code.

## Policy

`EquipmentEligibilityPolicy` requires a non-empty stable selected-hero ID and a
strict boolean `include_equipped` value. Hero IDs are trimmed once at policy
construction and compared exactly. Display names are never accepted as
identity.

The decision order for every `GearItem` is:

1. No equipped hero ID: eligible with `eligibility.unequipped`.
2. Owner ID equals the selected hero: eligible with
   `eligibility.selected_hero`.
3. A different non-null owner while `include_equipped` is true: eligible with
   `eligibility.include_equipped`.
4. A different non-null owner while `include_equipped` is false: excluded with
   `eligibility.other_hero`.

A stale or unknown owner is intentionally the fourth case when equipped items
are disabled. The policy has no hero-catalog dependency and therefore never
converts a non-null owner into an unequipped item. Parser-normalized blank owner
IDs arrive as `None` and follow the first case.

The item's `locked` state is informational and is not read by the policy.

## APIs and invariants

`decide_equipment_eligibility()` returns one immutable structured decision.
`evaluate_equipment_eligibility()` validates the entire inventory and returns
one decision per item in input order. `filter_eligible_gear()` returns the
original eligible `GearItem` objects in that same order.

Policy validation occurs before an inventory iterable is consumed. Inventory
values must be `GearItem` records with unique stable item IDs; invalid values or
duplicates fail explicitly. Empty input produces empty tuples.

Eligibility never assigns, removes, or interprets dense IDs and never mutates
gear. Existing dense fields, when supplied by another boundary, remain exactly
as they were on the original returned records.
