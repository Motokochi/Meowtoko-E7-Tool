# Self-imprint and exclusive-equipment selection

`hero_modifier_repository.py` is the offline, UI-independent selection layer
for imprint concentration and exclusive equipment (EE). It consumes the
immutable rich records exposed by `CharacterRepository`; it never reads a
floating endpoint or mutates the character snapshot.

## Pinned evidence

The bundled hero data is the exact Fribbels `data/cache/herodata.json` blob
`2364040f3d9424653877745fc2883ecdd632afb5` at revision
`f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`.

Two pinned Fribbels files define the interpreted behavior:

- `app/js/lib/dialog.js`, blob
  `d82799ef58fcbe1e8ae19d72a0d8dd256630835e`, converts rate imprints to
  percentage points for display and generates every integer EE roll from the
  converted base through exactly twice the base, inclusive.
- `app/js/lib/tabs/heroesTab.js`, blob
  `532f826eeb4cea1a1345c70f0878f9d4038717c2`, maps source stat kinds to flat
  Attack/Health/Defense, percentage Attack/Health/Defense, Speed, Crit,
  Effectiveness, Resistance, and dual-attack chance.

The runtime uses the bundled data only. The paths and blob hashes above are
evidence constants and are asserted by tests.

## Self-imprint boundary

All 386 source heroes have one `self_devotion` object. Only that field is used:
team imprint/imprint release is not present and is never applied as self
imprint. `select_imprint(hero_id, None)` is the explicit no-imprint state.

Available grades are taken from each hero rather than synthesized. Options use
the canonical low-to-high order D, C, B, A, S, SS, SSS while omitting grades
that the source does not contain. The snapshot contains three different grade
sets, so callers must list repository options instead of assuming one list.

`HeroModifierContribution` stores rate values as canonical ratios (`0.14` is
14%) and flat values in native units. Its `HeroModifierStatType` preserves all
ten source kinds, including `coop` as dual-attack chance. The older
`imprintBonuses` projection uses percentage points for compatibility and is
empty for dual-attack chance because `FinalStat` has no dual-attack member; the
typed contribution remains complete.

## Exclusive-equipment boundary

The snapshot has 253 heroes without EE metadata and 133 with exactly one EE
stat record. Stable EE identity is derived from canonical hero ID, source
index, source stat type, and source base value. Identity therefore proves hero
ownership and does not depend on a globally unique display name that the source
does not provide.

For percentage source stats, the source ratio is converted to integer
percentage points with positive JavaScript rounding. Flat values stay in
native units. Every integer from that converted base through 2× base is a valid
roll. Selection stores both the user-facing integer roll and the typed
canonical contribution; for example, a displayed Attack 10% roll persists as
`hero_modifier.attack_percent` with value `0.1`.

The typed custom catalog also distinguishes
`hero_modifier.final_attack_percent`, `hero_modifier.final_health_percent`,
and `hero_modifier.final_defense_percent`. These ratios are applied after gear
and completed set additions, matching Fribbels' final-stat multiplier boundary;
they are not interchangeable with ordinary base-relative percentage kinds.

The pinned source has no EE name, skill-enhancement description, skill number,
option ID, or effect value. It also does not connect `skills.S1/S2/S3.options`
to EE; those arrays are damage-calculator variants and are not reused. To keep
the required EE skill choice independent from the stat roll, each selected EE
exposes three scoped opaque slot IDs (`...skill-option.1` through `.3`). Their
description/effect remains explicitly `unavailable-in-snapshot`; no gameplay
effect is invented or applied. The three-slot count is an explicit product
constraint for the game's EE choice, not a claim that the pinned cache supplies
option metadata. Selecting no EE forbids both a roll and a skill slot.

## Persistence and validation

`HeroModifierSelection.apply_to_modifiers()` changes only imprint/EE fields and
preserves artifact, custom-bonus, and general skill-context fields. It writes:

- `imprintLevel`, the compatibility `imprintBonuses`, and typed
  `imprintContribution`;
- `exclusiveEquipmentId`, compatibility `exclusiveEquipmentBonuses`, and typed
  `exclusiveEquipmentContribution`; and
- independent `exclusiveEquipmentSkillOptionId`.

Optimizer profiles and run manifests are currently schema version 7. The
version-2-to-version-3 migration adds the three new fields as null, so it does
not reinterpret legacy untyped bonuses. Version-1 documents still migrate
sequentially through version 2 and then version 3. The unrelated version-4
migration adds typed custom bonuses and per-skill contexts without changing
imprint or EE semantics. New typed selections are catalog-validated
against the bundled rich source for exact hero ownership, grade, stat kind,
roll, and scoped skill-option identity. Tampered duplicate projections fail
instead of silently drifting.

The desktop profile boundary adds one catalog-aware recovery step after the
context-free schema migration. For v1/v2 only, it accepts a legacy imprint or
EE projection when the bundled hero catalog proves an exact grade and exactly
one EE roll, then supplies the equivalent typed contribution in memory. It
does not rewrite on read. No match, multiple matches, drift, or tampering keeps
the profile read-only and byte-preserved.

The unrelated version-5 migration adds nullable `itemProjectionMode` and does
not change modifier semantics or infer a current/reforged selection. The
unrelated version-6 migration adds no-op gear-filter defaults and likewise
does not change modifier semantics.
The unrelated version-7 migration adds the default maximum replacement
distance and does not change modifier semantics.
