# Offline artifact repository and selection

P02-T04 combines the 283 canonical `ArtifactDefinition` records with their
exact immutable Fribbels source-sidecar records. Runtime construction and stat
selection are offline and perform no URL or filesystem probes.

## Pinned calculation evidence

All evidence uses Fribbels revision
`f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`.

- `app/js/lib/artifact.js`, Git blob
  `34dfb714ef97d6bc05da79048f1ee8e14b1c342a`, sets each maximum stat to
  13 times its source base, linearly interpolates base to maximum with
  `level / 30`, and uses the same calculation for Attack, Health, and Defense.
- `app/js/lib/utils.js`, Git blob
  `18acebcc88380f9f3a86d98e0693000486830f1b`, defines `round10ths` as
  JavaScript `Math.round(number * 10) / 10`.
- `backend/src/main/java/com/fribbels/db/ArtifactStatsDb.java`, Git blob
  `f1e9d6cedbc4915f6ad2dd82e1d89bf17bca9c98`, independently uses the same
  13-times maximum and `level / 30f` interpolation for all three stats.

For nonnegative source stats, the implemented result is therefore:

`round10ths((base * 13 - base) * (level / 30) + base)`

Levels must be integers from 0 through 30. The result retains Fribbels'
one-tenth precision; it is not rounded to the integer displayed in game.

The pinned artifact cache contains only `code`, `name`, `rarity`, `role`, and
base Attack/Health/Defense. It has no skill text, proc values, descriptions,
limit-break table, or limit-break-dependent effect values. The repository
therefore exposes selected artifacts as
`ArtifactEffectDataState.UNAVAILABLE_IN_SNAPSHOT`, with `effect_value=None`.
A supplied limit break is rejected rather than guessed. No-artifact selections
use `NOT_APPLICABLE`.

## Repository and identity

`load_bundled_artifact_repository()` constructs an `ArtifactRepository` and
cross-checks catalog/sidecar timestamp, provenance, counts, names, stable-ID
derivation, level cap, base stats, and 13-times maximum relationships.

Stable IDs combine normalized source code, normalized name, and an
eight-character SHA-256 name digest because six source codes are reused.
`get(artifact_id)` is the selection identity. `source_code_matches(code)`
returns every matching record and never silently chooses one. `artifacts`
returns all options in normalized-name and stable-ID order.

Each frozen `ArtifactRecord` exposes canonical identity/stat metadata, source
code, rarity, role, source Defense, immutable raw stats, unknown fields, and the
complete raw source record. All six nonzero-Defense records are retained.

## Selection and overrides

`select_none()` represents no artifact without a sentinel ID. `select(...)`
requires a stable artifact ID and level. `ArtifactSelection.calculated_flat_stats`
contains the pinned level result; `flat_stats` contains the effective result.

`ArtifactStatOverrides` has independent nullable Attack, Health, and Defense.
An override replaces that stat's final leveled flat contribution. It does not
replace the source base, mutate the catalog, or enter percentage/custom hero
bonuses. Zero is a valid explicit override; negative, boolean, and non-finite
values are rejected.

`to_artifact_only_modifiers()` and `select_from_modifiers(...)` bridge the
selection to `HeroModifiers` without applying unrelated modifier categories.

## Persistence migration

Artifact configuration was introduced in optimizer-profile and run-manifest
schema version 2. Their version-1 migrations add these nullable fields under
`modifiers`:

- `artifactLimitBreaks`;
- `artifactAttackOverride`;
- `artifactHealthOverride`;
- `artifactDefenseOverride`.

Old artifact ID/level values are unchanged. No-artifact remains all-null.
Supplying a catalog validates the artifact ID and maximum level and rejects a
non-null limit-break value while effect data remains unavailable.

The current schema version is 5 because later phases add unrelated typed
imprint/EE, custom/skill-context, and item-projection-selection fields. Artifact semantics and the
version-1-to-version-2 artifact migration remain unchanged in the sequential
migration chain.
