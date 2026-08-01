# Typed custom bonuses and per-skill context

`skill_context_repository.py` is the offline, UI-independent selection layer
for custom hero bonuses and S1/S2/S3 calculation context. It consumes the
immutable rich records exposed by `CharacterRepository` and never contacts a
remote endpoint.

## Pinned source coverage

The repository uses the bundled Fribbels `data/cache/herodata.json` snapshot at
revision `f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`. It validates and preserves
exactly 1,158 records: S1, S2, and S3 for each of 386 heroes. Direct `rate`,
`pow`, `targets`, `penetration`, scaling, note, increased-value, and critical
damage fields remain optional. A passive or non-damaging skill therefore stays
usable without fabricated damage fields, target count, hit type, or
penetration. Interpreted fields are validated strictly; retained unknown fields
remain recursively immutable and lossless.

The snapshot contains 209 nested calculator options across 143 heroes. Each
option receives a deterministic ID from its canonical hero ID, skill slot,
zero-based source index, and exact immutable option evidence. Names are display
text only and need not be unique. The source entries are alternate calculation
variants, so a context selects zero or one option for a skill; multiple source
options remain independently listable but are not simultaneously applicable.

These source-option IDs use the `skill-option.fribbels.` namespace. They are
separate from P02-T05's exclusive-equipment option IDs and can never be resolved
as EE enhancements or vice versa.

## Typed custom bonuses

`CustomBonusSelection` accepts at most one nonnegative finite contribution for
each supported kind: flat and percentage Attack, Health, and Defense, plus
Speed, Crit chance, Effectiveness, and Resistance. Zero is an explicit value.
Dual-attack chance is outside this custom-bonus contract. Contributions are
stored in deterministic `HeroModifierStatType` order.

Percentage values use canonical ratios internally (`0.14` means 14%). Display
conversion and the legacy `customBonuses` projection use percentage points.
Because flat and percentage Attack, Health, and Defense both project to one
legacy `FinalStat` key, `customContributions` is authoritative. The projection
is validated against it and is never reversed by guessing a typed kind.
Applying a custom selection changes only the custom fields on `HeroModifiers`;
artifact, imprint, EE, and skill-option fields are preserved.

## S1/S2/S3 context

An `OptimizationRequest` persists exactly one immutable context for each of S1,
S2, and S3. Every context independently stores:

- an optional source-option ID;
- an optional supported hit type (`critical`, `crushing`, `normal`, or `miss`);
- an optional positive-integer target-count override;
- an optional finite penetration-ratio override from 0 through 1; and
- a required finite, nonnegative target Defense.

Null means no user override or selection. An empty source hit-type list remains
an explicit non-damaging/not-applicable state; a normal hit is not invented.
The current 209 options are heal/barrier variants with zero rate and power, so
selecting one also makes damage-only hit, target-count, and penetration inputs
not applicable. Their raw zero `targets` evidence is preserved but is not
fabricated into a literal zero-target calculation. For a damaging selection,
target-count precedence is user override, a positive selected-option value,
then the direct skill value. Effective penetration is user override, direct
source value, then zero. Hit types must be listed by the selected hero's source
skill.

The legacy request-level `targetDefense` remains for compatibility. A new
request without explicit contexts initializes all three target Defense values
from it. The version-3-to-version-4 migration performs the same copy once while
leaving all option, hit, target-count, and penetration selections null. After
construction or migration, the explicit S1/S2/S3 values are authoritative and
are never silently synchronized from a later global-field change.

## Persistence validation

Optimizer profiles and run manifests are schema version 7. Their deterministic
version-3 migration adds `customContributions: []` and all three explicit skill
contexts without inferring typed custom values or source options. Version-1 and
version-2 documents continue through every sequential migration. Old-version
payloads that already contain newer fields are rejected.

The later v4-to-v5 migration adds only nullable `itemProjectionMode`; it does
not change skill contexts or infer a projection choice. The v5-to-v6 migration
adds only no-op gear-filter defaults and does not change skill contexts.
The v6-to-v7 migration adds only the default maximum replacement distance and
does not change skill contexts.

When a structured source choice is active, catalog validation checks hero and
skill ownership, stable option evidence, supported hit type, source/default
resolution, numerical bounds, and agreement between the selected structured
option IDs and the compatibility `modifiers.skillOptions` projection.
